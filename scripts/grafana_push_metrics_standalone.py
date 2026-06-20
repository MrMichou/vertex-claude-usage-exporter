#!/usr/bin/env python3
"""
Push Claude usage metrics to Prometheus Pushgateway using HTTP POST.
Standalone version that doesn't require prometheus-client library.
Uses only google-cloud-logging (pre-installed in cloud-sdk image).
"""

import argparse
import logging
import re
import sys
import time
import traceback
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from google.auth import exceptions as auth_exceptions
from google.api_core import exceptions as api_exceptions
from google.cloud import logging as cloud_logging
from google.cloud.logging_v2 import DESCENDING

logger = logging.getLogger(__name__)

# Fixed Pushgateway job for the live (intra-day) metrics group. Each hourly run
# re-pushes every hour-so-far under this group, so the push is idempotent and
# self-healing, and rotates to the new day on the first run after midnight.
LIVE_JOB_NAME = "claude_vertex_live"


# Pricing per million tokens (January 2026)
# NOTE: keep in sync with scripts/vertex_claude_exporter/config.py (source of truth).
# This file is intentionally standalone (no package import) for drop-in k8s jobs.
# Prompt caching: cache_write = 1.25x input (5-min TTL), cache_read = 0.1x input
PRICING = {
    # fable-5 / opus-4-7: not billed by GCP as of 2026-06-11 (no SKU yet)
    "claude-fable-5": {
        "input": 0.00,
        "output": 0.00,
        "cache_write": 0.00,
        "cache_read": 0.00,
    },
    "claude-opus-4-8": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
    },
    "claude-opus-4-7": {
        "input": 0.00,
        "output": 0.00,
        "cache_write": 0.00,
        "cache_read": 0.00,
    },
    "claude-opus-4-6": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
    },
    "claude-opus-4-5": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
    },
    "claude-opus-4": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
    "claude-3-opus": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-sonnet-4-5": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-sonnet-4": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-3-5-sonnet": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
    "claude-3-5-haiku": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
    "count-tokens": {
        "input": 0.00,
        "output": 0.00,
        "cache_write": 0.00,
        "cache_read": 0.00,
    },
    "default": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
}

# Recalibrated from June 2026 GCP billing (Jun 1-18 month-to-date). Uniform
# per-model averages applied to every principal: average = billed_tokens /
# total_requests, reproducing the per-model invoice to within rounding
# (aggregate delta +0.003%). Values absorb the account's billed-vs-list factor
# (CAD + premium).
MODEL_TOKEN_AVERAGES = {
    # claude-fable-5: not billing-calibrated (no billing SKU yet) — Opus
    # averages x1.3 (Fable 5 tokenizer yields ~30% more tokens)
    "claude-fable-5": {
        "input": 11532,
        "output": 4612,
        "cache_write": 0,
        "cache_read": 0,
    },
    # opus-4-8: recalibrated Jun 11 PM (usage ramped 3x on Jun 10-11; all
    # interactive). Billing still ingesting — recheck in a few days.
    "claude-opus-4-8": {
        "input": 207,
        "output": 1097,
        "cache_write": 15491,
        "cache_read": 105548,
    },
    # opus-4-6: Jun 1-18 invoice, all principals averaged together.
    # opus-4-7 / 4-5: reuse opus-4-6.
    "claude-opus-4-7": {
        "input": 449,
        "output": 701,
        "cache_write": 10090,
        "cache_read": 133090,
    },
    "claude-opus-4-6": {
        "input": 449,
        "output": 701,
        "cache_write": 10090,
        "cache_read": 133090,
    },
    "claude-opus-4-5": {
        "input": 449,
        "output": 701,
        "cache_write": 10090,
        "cache_read": 133090,
    },
    "claude-opus-4": {"input": 8871, "output": 3548, "cache_write": 0, "cache_read": 0},
    "claude-3-opus": {"input": 8871, "output": 3548, "cache_write": 0, "cache_read": 0},
    "claude-sonnet-4-6": {
        "input": 1833,
        "output": 988,
        "cache_write": 13388,
        "cache_read": 109720,
    },
    "claude-sonnet-4-5": {
        "input": 14089,
        "output": 641,
        "cache_write": 12218,
        "cache_read": 67746,
    },
    "claude-sonnet-4": {
        "input": 3309,
        "output": 1323,
        "cache_write": 0,
        "cache_read": 0,
    },
    "claude-3-5-sonnet": {
        "input": 3309,
        "output": 1323,
        "cache_write": 0,
        "cache_read": 0,
    },
    "claude-haiku-4-5": {
        "input": 524,
        "output": 411,
        "cache_write": 4241,
        "cache_read": 57766,
    },
    "claude-3-5-haiku": {
        "input": 382,
        "output": 153,
        "cache_write": 0,
        "cache_read": 0,
    },
    "count-tokens": {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0},
    "default": {"input": 3000, "output": 1200, "cache_write": 0, "cache_read": 0},
}

DEFAULT_AVG_INPUT_TOKENS = 3000
DEFAULT_AVG_OUTPUT_TOKENS = 1200
DEFAULT_AVG_CACHE_WRITE_TOKENS = 0
DEFAULT_AVG_CACHE_READ_TOKENS = 0

# Transient error types for retry logic
_TRANSIENT_ERRORS = (
    api_exceptions.TooManyRequests,
    api_exceptions.ServiceUnavailable,
    api_exceptions.InternalServerError,
    api_exceptions.GatewayTimeout,
    ConnectionError,
)


def build_filter(target_date: datetime) -> str:
    start_ts = target_date.replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    end_ts = start_ts + timedelta(days=1)
    start_str = start_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end_ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    return " ".join(
        [
            'protoPayload.serviceName="aiplatform.googleapis.com"',
            'protoPayload.methodName=~"rawPredict|streamRawPredict|Predict"',
            f'timestamp >= "{start_str}"',
            f'timestamp < "{end_str}"',
        ]
    )


def fetch_logs(project_id: str, filter_str: str, max_retries: int = 3) -> list:
    """Fetch logs from Cloud Logging with retry on transient errors."""
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Fetching logs (attempt %d/%d)...", attempt, max_retries)
            client = cloud_logging.Client(project=project_id)
            return list(
                client.list_entries(
                    filter_=filter_str, order_by=DESCENDING, page_size=1000
                )
            )

        except (
            auth_exceptions.DefaultCredentialsError,
            api_exceptions.PermissionDenied,
        ) as e:
            logger.error("Authentication/permission error (not retryable): %s", e)
            sys.exit(1)

        except _TRANSIENT_ERRORS as e:
            if attempt < max_retries:
                wait = 2**attempt
                logger.warning("Transient error: %s. Retrying in %ds...", e, wait)
                time.sleep(wait)
            else:
                logger.error("Failed after %d attempts: %s", max_retries, e)
                logger.debug("Traceback:\n%s", traceback.format_exc())
                raise

        except Exception as e:
            logger.error("Unexpected error fetching logs: %s", e)
            logger.debug("Traceback:\n%s", traceback.format_exc())
            raise

    return []


def extract_model_name(resource_name: str) -> str:
    match = re.search(r"models/([^@/]+)", resource_name)
    return match.group(1) if match else "unknown"


def get_pricing_for_model(model_name: str) -> dict:
    """Get pricing for a model, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(PRICING.keys(), key=len, reverse=True):
        if key != "default" and key in model_lower:
            return PRICING[key]
    return PRICING["default"]


def get_token_averages_for_model(model_name: str, user_email: str = None) -> dict:
    """Get calibrated token averages for a model, matching longest key first.

    Calibration is uniform per model; user_email is accepted for call-site
    compatibility but ignored.
    """
    model_lower = model_name.lower()
    for key in sorted(MODEL_TOKEN_AVERAGES.keys(), key=len, reverse=True):
        if key != "default" and key in model_lower:
            return MODEL_TOKEN_AVERAGES[key]
    return MODEL_TOKEN_AVERAGES["default"]


def extract_hour(api_repr: dict) -> str:
    """Extract the UTC hour ("00".."23") from a log entry's timestamp.

    Returns "unknown" when no parseable timestamp is present.
    """
    ts = api_repr.get("timestamp") or api_repr.get("receiveTimestamp")
    if not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return f"{dt.hour:02d}"


def parse_entry(entry) -> dict:
    try:
        api_repr = entry.to_api_repr()
        proto_payload = api_repr.get("protoPayload", {})
        auth_info = proto_payload.get("authenticationInfo", {})
        email = auth_info.get("principalEmail", "unknown")
        resource_name = proto_payload.get("resourceName", "")
        model = extract_model_name(resource_name)

        if (
            "claude" not in resource_name.lower()
            and "anthropic" not in resource_name.lower()
        ):
            return None

        # Deduplicate streaming requests: skip "last-only" operations
        operation = api_repr.get("operation", {})
        if operation.get("last") and not operation.get("first"):
            return None

        # Skip utility endpoints that aren't actual model calls
        if model == "count-tokens":
            return None

        return {"email": email, "model": model, "hour": extract_hour(api_repr)}
    except Exception as e:
        logger.debug("Failed to parse log entry: %s", e)
        return None


def aggregate_usage(entries: list) -> dict:
    usage = defaultdict(int)
    skipped = 0
    for entry in entries:
        parsed = parse_entry(entry)
        if parsed:
            usage[(parsed["email"], parsed["model"])] += 1
        else:
            skipped += 1

    if skipped:
        logger.info(
            "Skipped %d non-Claude/unparseable entries out of %d total",
            skipped,
            len(entries),
        )

    return dict(usage)


def aggregate_usage_by_hour(entries: list) -> dict:
    """Aggregate usage by (model, hour) for intra-day "live" metrics.

    Hour is the zero-padded UTC hour ("00".."23"). Same streaming-dedup and
    Claude-only filtering as aggregate_usage; model+hour granularity only (no
    per-user dimension) to keep cardinality low.
    """
    usage = defaultdict(int)
    skipped = 0
    for entry in entries:
        parsed = parse_entry(entry)
        if parsed:
            usage[(parsed["model"], parsed["hour"])] += 1
        else:
            skipped += 1

    if skipped:
        logger.info(
            "Skipped %d non-Claude/unparseable entries out of %d total",
            skipped,
            len(entries),
        )

    return dict(usage)


def estimate_cost(
    request_count: int,
    model_name: str,
    avg_input: int = None,
    avg_output: int = None,
    use_calibrated: bool = True,
    avg_cache_write: int = None,
    avg_cache_read: int = None,
    user_email: str = None,
) -> dict:
    pricing = get_pricing_for_model(model_name)

    # Use calibrated model-specific averages if not overridden
    if use_calibrated and avg_input is None and avg_output is None:
        model_avgs = get_token_averages_for_model(model_name, user_email)
        avg_input = model_avgs["input"]
        avg_output = model_avgs["output"]
        if avg_cache_write is None:
            avg_cache_write = model_avgs["cache_write"]
        if avg_cache_read is None:
            avg_cache_read = model_avgs["cache_read"]
    else:
        if avg_input is None:
            avg_input = DEFAULT_AVG_INPUT_TOKENS
        if avg_output is None:
            avg_output = DEFAULT_AVG_OUTPUT_TOKENS
        if avg_cache_write is None:
            avg_cache_write = DEFAULT_AVG_CACHE_WRITE_TOKENS
        if avg_cache_read is None:
            avg_cache_read = DEFAULT_AVG_CACHE_READ_TOKENS

    est_input = request_count * avg_input
    est_output = request_count * avg_output
    est_cache_write = request_count * avg_cache_write
    est_cache_read = request_count * avg_cache_read
    input_cost = (est_input / 1_000_000) * pricing["input"]
    output_cost = (est_output / 1_000_000) * pricing["output"]
    cache_write_cost = (est_cache_write / 1_000_000) * pricing["cache_write"]
    cache_read_cost = (est_cache_read / 1_000_000) * pricing["cache_read"]
    return {
        "input_tokens": est_input,
        "output_tokens": est_output,
        "cache_write_tokens": est_cache_write,
        "cache_read_tokens": est_cache_read,
        "cost_usd": round(
            input_cost + output_cost + cache_write_cost + cache_read_cost, 4
        ),
    }


def generate_prometheus_metrics(
    usage: dict,
    target_date: datetime,
    project_id: str,
    avg_input: int,
    avg_output: int,
    use_calibrated: bool = True,
    avg_cache_write: int = None,
    avg_cache_read: int = None,
) -> str:
    """Generate Prometheus text format metrics."""
    lines = []
    date_str = target_date.strftime("%Y-%m-%d")
    model_totals = defaultdict(
        lambda: {
            "requests": 0,
            "cost": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
        }
    )
    unique_users = set()

    # Per-user metrics: requests
    lines.append(
        "# HELP claude_vertex_requests_total Total Claude API requests on Vertex AI"
    )
    lines.append("# TYPE claude_vertex_requests_total gauge")
    for (email, model), count in usage.items():
        sanitized_email = email.replace("@", "_at_").replace(".", "_")
        lines.append(
            f'claude_vertex_requests_total{{user="{sanitized_email}",model="{model}",date="{date_str}",project="{project_id}"}} {count}'
        )
        model_totals[model]["requests"] += count
        unique_users.add(email)

    # Per-user metrics: cost and tokens
    lines.append("# HELP claude_vertex_estimated_cost_usd Estimated cost in USD")
    lines.append("# TYPE claude_vertex_estimated_cost_usd gauge")
    lines.append("# HELP claude_vertex_estimated_input_tokens Estimated input tokens")
    lines.append("# TYPE claude_vertex_estimated_input_tokens gauge")
    lines.append("# HELP claude_vertex_estimated_output_tokens Estimated output tokens")
    lines.append("# TYPE claude_vertex_estimated_output_tokens gauge")
    lines.append(
        "# HELP claude_vertex_estimated_cache_write_tokens Estimated prompt cache write tokens"
    )
    lines.append("# TYPE claude_vertex_estimated_cache_write_tokens gauge")
    lines.append(
        "# HELP claude_vertex_estimated_cache_read_tokens Estimated prompt cache read tokens"
    )
    lines.append("# TYPE claude_vertex_estimated_cache_read_tokens gauge")
    for (email, model), count in usage.items():
        sanitized_email = email.replace("@", "_at_").replace(".", "_")
        cost_info = estimate_cost(
            count,
            model,
            avg_input,
            avg_output,
            use_calibrated,
            avg_cache_write,
            avg_cache_read,
            user_email=email,
        )
        labels = f'user="{sanitized_email}",model="{model}",date="{date_str}",project="{project_id}"'
        lines.append(
            f"claude_vertex_estimated_cost_usd{{{labels}}} {cost_info['cost_usd']}"
        )
        lines.append(
            f"claude_vertex_estimated_input_tokens{{{labels}}} {cost_info['input_tokens']}"
        )
        lines.append(
            f"claude_vertex_estimated_output_tokens{{{labels}}} {cost_info['output_tokens']}"
        )
        lines.append(
            f"claude_vertex_estimated_cache_write_tokens{{{labels}}} {cost_info['cache_write_tokens']}"
        )
        lines.append(
            f"claude_vertex_estimated_cache_read_tokens{{{labels}}} {cost_info['cache_read_tokens']}"
        )
        model_totals[model]["cost"] += cost_info["cost_usd"]
        model_totals[model]["input_tokens"] += cost_info["input_tokens"]
        model_totals[model]["output_tokens"] += cost_info["output_tokens"]
        model_totals[model]["cache_write_tokens"] += cost_info["cache_write_tokens"]
        model_totals[model]["cache_read_tokens"] += cost_info["cache_read_tokens"]

    # Aggregated metrics
    lines.append("# HELP claude_vertex_total_requests Total requests per model")
    lines.append("# TYPE claude_vertex_total_requests gauge")
    for model, totals in model_totals.items():
        lines.append(
            f'claude_vertex_total_requests{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["requests"]}'
        )

    lines.append("# HELP claude_vertex_total_cost_usd Total cost per model")
    lines.append("# TYPE claude_vertex_total_cost_usd gauge")
    for model, totals in model_totals.items():
        lines.append(
            f'claude_vertex_total_cost_usd{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["cost"]}'
        )

    lines.append(
        "# HELP claude_vertex_total_input_tokens Total estimated input tokens per model"
    )
    lines.append("# TYPE claude_vertex_total_input_tokens gauge")
    for model, totals in model_totals.items():
        lines.append(
            f'claude_vertex_total_input_tokens{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["input_tokens"]}'
        )

    lines.append(
        "# HELP claude_vertex_total_output_tokens Total estimated output tokens per model"
    )
    lines.append("# TYPE claude_vertex_total_output_tokens gauge")
    for model, totals in model_totals.items():
        lines.append(
            f'claude_vertex_total_output_tokens{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["output_tokens"]}'
        )

    lines.append(
        "# HELP claude_vertex_total_cache_write_tokens Total estimated prompt cache write tokens per model"
    )
    lines.append("# TYPE claude_vertex_total_cache_write_tokens gauge")
    for model, totals in model_totals.items():
        lines.append(
            f'claude_vertex_total_cache_write_tokens{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["cache_write_tokens"]}'
        )

    lines.append(
        "# HELP claude_vertex_total_cache_read_tokens Total estimated prompt cache read tokens per model"
    )
    lines.append("# TYPE claude_vertex_total_cache_read_tokens gauge")
    for model, totals in model_totals.items():
        lines.append(
            f'claude_vertex_total_cache_read_tokens{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["cache_read_tokens"]}'
        )

    lines.append("# HELP claude_vertex_unique_users Number of unique users")
    lines.append("# TYPE claude_vertex_unique_users gauge")
    lines.append(
        f'claude_vertex_unique_users{{date="{date_str}",project="{project_id}"}} {len(unique_users)}'
    )

    return "\n".join(lines) + "\n"


def generate_live_prometheus_metrics(
    hourly_usage: dict,
    target_date: datetime,
    project_id: str,
    avg_input: int,
    avg_output: int,
    use_calibrated: bool = True,
    avg_cache_write: int = None,
    avg_cache_read: int = None,
) -> str:
    """Generate intra-day "live" Prometheus text metrics, bucketed by hour.

    Separate claude_vertex_live_* family (labels model/hour/date/project),
    aggregated at model+hour granularity. hourly_usage maps (model, hour) -> count.
    """
    lines = []
    date_str = target_date.strftime("%Y-%m-%d")

    metrics = {
        "claude_vertex_live_total_requests": (
            "Live (intra-day) Claude API requests on Vertex AI, by hour",
            "requests",
        ),
        "claude_vertex_live_total_cost_usd": (
            "Live (intra-day) estimated cost in USD, by hour",
            "cost",
        ),
        "claude_vertex_live_total_input_tokens": (
            "Live (intra-day) estimated input tokens, by hour",
            "input_tokens",
        ),
        "claude_vertex_live_total_output_tokens": (
            "Live (intra-day) estimated output tokens, by hour",
            "output_tokens",
        ),
        "claude_vertex_live_total_cache_write_tokens": (
            "Live (intra-day) estimated prompt cache write tokens, by hour",
            "cache_write_tokens",
        ),
        "claude_vertex_live_total_cache_read_tokens": (
            "Live (intra-day) estimated prompt cache read tokens, by hour",
            "cache_read_tokens",
        ),
    }

    # Precompute per-bucket values
    bucket_values = {}
    for (model, hour), count in hourly_usage.items():
        cost_info = estimate_cost(
            count,
            model,
            avg_input,
            avg_output,
            use_calibrated,
            avg_cache_write,
            avg_cache_read,
        )
        bucket_values[(model, hour)] = {
            "requests": count,
            "cost": cost_info["cost_usd"],
            "input_tokens": cost_info["input_tokens"],
            "output_tokens": cost_info["output_tokens"],
            "cache_write_tokens": cost_info["cache_write_tokens"],
            "cache_read_tokens": cost_info["cache_read_tokens"],
        }

    for metric_name, (help_text, value_key) in metrics.items():
        lines.append(f"# HELP {metric_name} {help_text}")
        lines.append(f"# TYPE {metric_name} gauge")
        for (model, hour), values in bucket_values.items():
            labels = f'model="{model}",hour="{hour}",date="{date_str}",project="{project_id}"'
            lines.append(f"{metric_name}{{{labels}}} {values[value_key]}")

    return "\n".join(lines) + "\n"


def push_to_gateway(pushgateway_url: str, job_name: str, metrics_text: str):
    """Push metrics to Pushgateway using HTTP POST."""
    url = f"http://{pushgateway_url}/metrics/job/{job_name}"
    data = metrics_text.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "text/plain; charset=utf-8")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status == 200
    except urllib.error.HTTPError as e:
        logger.error("HTTP Error: %d - %s", e.code, e.reason)
        raise
    except urllib.error.URLError as e:
        logger.error("URL Error: %s", e.reason)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Push Claude usage metrics to Prometheus Pushgateway"
    )
    parser.add_argument("--project", "-p", required=True, help="GCP Project ID")
    parser.add_argument(
        "--date",
        "-d",
        default=None,
        help="Report date (YYYY-MM-DD). Default: yesterday",
    )
    parser.add_argument(
        "--pushgateway", "-g", default="localhost:9091", help="Pushgateway URL"
    )
    parser.add_argument(
        "--job", "-j", default="claude_vertex_usage", help="Prometheus job name"
    )
    parser.add_argument(
        "--avg-input-tokens",
        type=int,
        default=None,
        help="Override average input tokens per request (disables per-model calibration)",
    )
    parser.add_argument(
        "--avg-output-tokens",
        type=int,
        default=None,
        help="Override average output tokens per request (disables per-model calibration)",
    )
    parser.add_argument(
        "--avg-cache-write-tokens",
        type=int,
        default=None,
        help="Average prompt cache write tokens per request (default: 0)",
    )
    parser.add_argument(
        "--avg-cache-read-tokens",
        type=int,
        default=None,
        help="Average prompt cache read tokens per request (default: 0)",
    )
    parser.add_argument(
        "--no-calibrated",
        action="store_true",
        help="Disable calibrated per-model token averages, use defaults instead",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Live mode: query the current day so far, bucket usage by hour, and "
            "push the claude_vertex_live_* metric family (job 'claude_vertex_live'). "
            "Defaults --date to today. Run hourly for near-real-time dashboards."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show metrics without pushing"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Validate date
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            logger.error("Invalid date format: '%s'. Expected YYYY-MM-DD.", args.date)
            sys.exit(1)
    elif args.live:
        # Live mode defaults to the current day (00:00 UTC -> now). build_filter
        # queries midnight..midnight+1d, which naturally returns everything up to
        # now since there are no future logs.
        target_date = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    # Validate token counts
    if args.avg_input_tokens is not None and args.avg_input_tokens <= 0:
        logger.error("--avg-input-tokens must be > 0, got %d", args.avg_input_tokens)
        sys.exit(1)
    if args.avg_output_tokens is not None and args.avg_output_tokens <= 0:
        logger.error("--avg-output-tokens must be > 0, got %d", args.avg_output_tokens)
        sys.exit(1)
    if args.avg_cache_write_tokens is not None and args.avg_cache_write_tokens < 0:
        logger.error(
            "--avg-cache-write-tokens must be >= 0, got %d", args.avg_cache_write_tokens
        )
        sys.exit(1)
    if args.avg_cache_read_tokens is not None and args.avg_cache_read_tokens < 0:
        logger.error(
            "--avg-cache-read-tokens must be >= 0, got %d", args.avg_cache_read_tokens
        )
        sys.exit(1)

    # Strip http(s):// prefix from pushgateway URL if present
    pushgateway_url = args.pushgateway
    if pushgateway_url.startswith("http://") or pushgateway_url.startswith("https://"):
        logger.warning(
            "Stripping URL scheme from pushgateway address: %s", pushgateway_url
        )
        pushgateway_url = pushgateway_url.split("://", 1)[1]

    # Determine if using calibrated mode
    use_calibrated = (
        not args.no_calibrated
        and args.avg_input_tokens is None
        and args.avg_output_tokens is None
    )

    if args.live and args.job == "claude_vertex_usage":
        job_name = LIVE_JOB_NAME
    else:
        job_name = args.job

    logger.info("Mode: %s", "live (hourly buckets)" if args.live else "daily")
    logger.info("Project: %s", args.project)
    logger.info("Date: %s", target_date.strftime("%Y-%m-%d"))
    logger.info("Pushgateway: %s", pushgateway_url)
    logger.info("Job: %s", job_name)
    if use_calibrated:
        logger.info("Token estimates: Using calibrated per-model averages")
    else:
        avg_in = args.avg_input_tokens or DEFAULT_AVG_INPUT_TOKENS
        avg_out = args.avg_output_tokens or DEFAULT_AVG_OUTPUT_TOKENS
        logger.info(
            "Token estimates: %d input, %d output (global override)", avg_in, avg_out
        )

    filter_str = build_filter(target_date)
    entries = fetch_logs(args.project, filter_str)
    logger.info("Log entries fetched: %d", len(entries))

    if not entries:
        logger.info("No log entries found.")
        sys.exit(0)

    usage = aggregate_usage_by_hour(entries) if args.live else aggregate_usage(entries)
    if not usage:
        logger.info("No Claude API calls identified.")
        sys.exit(0)

    logger.info("Claude API calls identified: %d", sum(usage.values()))

    if args.live:
        metrics_text = generate_live_prometheus_metrics(
            usage,
            target_date,
            args.project,
            args.avg_input_tokens,
            args.avg_output_tokens,
            use_calibrated,
            args.avg_cache_write_tokens,
            args.avg_cache_read_tokens,
        )
    else:
        metrics_text = generate_prometheus_metrics(
            usage,
            target_date,
            args.project,
            args.avg_input_tokens,
            args.avg_output_tokens,
            use_calibrated,
            args.avg_cache_write_tokens,
            args.avg_cache_read_tokens,
        )

    if args.dry_run:
        logger.info("[DRY RUN] Metrics:\n%s", metrics_text)
    else:
        try:
            push_to_gateway(pushgateway_url, job_name, metrics_text)
            logger.info("Metrics pushed to %s", pushgateway_url)
        except Exception as e:
            logger.error("Failed: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()
