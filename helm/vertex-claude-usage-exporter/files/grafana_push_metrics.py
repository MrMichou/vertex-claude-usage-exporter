#!/usr/bin/env python3
"""
Push Claude usage metrics from Vertex AI to Prometheus Pushgateway for Grafana.
Collects usage data from Cloud Logging and exposes it as Prometheus metrics.
"""

import argparse
import logging
import re
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from google.auth import exceptions as auth_exceptions
from google.api_core import exceptions as api_exceptions
from google.cloud import logging as cloud_logging
from google.cloud.logging_v2 import DESCENDING
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

logger = logging.getLogger(__name__)


# Pricing per million tokens (January 2026)
PRICING = {
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-3-5-haiku": {"input": 1.00, "output": 5.00},
    "count-tokens": {"input": 0.00, "output": 0.00},
    "default": {"input": 3.00, "output": 15.00},
}

# Calibrated token averages per model (based on January 2026 GCP billing data)
# These values were derived from actual billing to match real costs
MODEL_TOKEN_AVERAGES = {
    "claude-opus-4-6": {"input": 8871, "output": 3548},
    "claude-opus-4-5": {"input": 8871, "output": 3548},
    "claude-opus-4": {"input": 8871, "output": 3548},
    "claude-3-opus": {"input": 8871, "output": 3548},
    "claude-sonnet-4-5": {"input": 4820, "output": 1928},
    "claude-sonnet-4": {"input": 3309, "output": 1323},
    "claude-3-5-sonnet": {"input": 3309, "output": 1323},
    "claude-haiku-4-5": {"input": 840, "output": 336},
    "claude-3-5-haiku": {"input": 382, "output": 153},
    "count-tokens": {"input": 0, "output": 0},
    "default": {"input": 3000, "output": 1200},
}

# Default token averages (fallback, used if --avg-input-tokens/--avg-output-tokens specified)
DEFAULT_AVG_INPUT_TOKENS = 3000
DEFAULT_AVG_OUTPUT_TOKENS = 1200

# Transient error types for retry logic
_TRANSIENT_ERRORS = (
    api_exceptions.TooManyRequests,
    api_exceptions.ServiceUnavailable,
    api_exceptions.InternalServerError,
    api_exceptions.GatewayTimeout,
    ConnectionError,
)


def build_filter(target_date: datetime) -> str:
    """Build Cloud Logging filter for the target date."""
    start_ts = target_date.replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    end_ts = start_ts + timedelta(days=1)

    start_str = start_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end_ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    filter_parts = [
        'protoPayload.serviceName="aiplatform.googleapis.com"',
        'protoPayload.methodName=~"rawPredict|streamRawPredict|Predict"',
        f'timestamp >= "{start_str}"',
        f'timestamp < "{end_str}"',
    ]

    return " ".join(filter_parts)


def fetch_logs(project_id: str, filter_str: str, max_retries: int = 3) -> list:
    """Fetch logs from Cloud Logging with retry on transient errors."""
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Fetching logs (attempt %d/%d)...", attempt, max_retries)
            # Use REST API instead of gRPC (gRPC doesn't work through HTTP proxies)
            client = cloud_logging.Client(project=project_id, _use_grpc=False)
            entries = []

            for entry in client.list_entries(
                filter_=filter_str, order_by=DESCENDING, page_size=1000
            ):
                entries.append(entry)

            return entries

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
    """Extract model name from resourceName."""
    match = re.search(r"models/([^@/]+)", resource_name)
    if match:
        return match.group(1)
    return "unknown"


def get_pricing_for_model(model_name: str) -> dict:
    """Get pricing for a model, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(PRICING.keys(), key=len, reverse=True):
        if key != "default" and key in model_lower:
            return PRICING[key]
    return PRICING["default"]


def get_token_averages_for_model(model_name: str) -> dict:
    """Get calibrated token averages for a model, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(MODEL_TOKEN_AVERAGES.keys(), key=len, reverse=True):
        if key != "default" and key in model_lower:
            return MODEL_TOKEN_AVERAGES[key]
    return MODEL_TOKEN_AVERAGES["default"]


def parse_entry(entry) -> dict:
    """Parse a log entry and extract relevant information."""
    try:
        api_repr = entry.to_api_repr()
        proto_payload = api_repr.get("protoPayload", {})

        # User email
        auth_info = proto_payload.get("authenticationInfo", {})
        email = auth_info.get("principalEmail", "unknown")

        # Model
        resource_name = proto_payload.get("resourceName", "")
        model = extract_model_name(resource_name)

        # Check if it's a Claude/Anthropic model
        if (
            "claude" not in resource_name.lower()
            and "anthropic" not in resource_name.lower()
        ):
            return None

        # Deduplicate streaming requests: skip "last-only" operations
        operation = api_repr.get("operation", {})
        if operation.get("last") and not operation.get("first"):
            return None

        return {"email": email, "model": model}
    except Exception as e:
        logger.debug("Failed to parse log entry: %s", e)
        return None


def aggregate_usage(entries: list) -> dict:
    """
    Aggregate usage by (email, model).
    Returns: {(email, model): count}
    """
    usage = defaultdict(int)
    skipped = 0

    for entry in entries:
        parsed = parse_entry(entry)
        if parsed:
            key = (parsed["email"], parsed["model"])
            usage[key] += 1
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
) -> dict:
    """Estimate cost based on request count and average tokens.

    If use_calibrated is True and avg_input/avg_output are None, uses model-specific
    calibrated token averages derived from actual GCP billing data.
    """
    pricing = get_pricing_for_model(model_name)

    # Use calibrated model-specific averages if not overridden
    if use_calibrated and avg_input is None and avg_output is None:
        model_avgs = get_token_averages_for_model(model_name)
        avg_input = model_avgs["input"]
        avg_output = model_avgs["output"]
    else:
        if avg_input is None:
            avg_input = DEFAULT_AVG_INPUT_TOKENS
        if avg_output is None:
            avg_output = DEFAULT_AVG_OUTPUT_TOKENS

    est_input_tokens = request_count * avg_input
    est_output_tokens = request_count * avg_output

    input_cost = (est_input_tokens / 1_000_000) * pricing["input"]
    output_cost = (est_output_tokens / 1_000_000) * pricing["output"]

    return {
        "input_tokens": est_input_tokens,
        "output_tokens": est_output_tokens,
        "cost_usd": round(input_cost + output_cost, 4),
    }


def push_metrics_to_gateway(
    usage: dict,
    target_date: datetime,
    pushgateway_url: str,
    job_name: str,
    project_id: str,
    avg_input: int = None,
    avg_output: int = None,
    use_calibrated: bool = True,
):
    """Push metrics to Prometheus Pushgateway."""

    registry = CollectorRegistry()
    date_str = target_date.strftime("%Y-%m-%d")

    # Define metrics
    requests_gauge = Gauge(
        "claude_vertex_requests_total",
        "Total Claude API requests on Vertex AI",
        ["user", "model", "date", "project"],
        registry=registry,
    )

    cost_gauge = Gauge(
        "claude_vertex_estimated_cost_usd",
        "Estimated cost in USD for Claude API usage",
        ["user", "model", "date", "project"],
        registry=registry,
    )

    input_tokens_gauge = Gauge(
        "claude_vertex_estimated_input_tokens",
        "Estimated input tokens for Claude API usage",
        ["user", "model", "date", "project"],
        registry=registry,
    )

    output_tokens_gauge = Gauge(
        "claude_vertex_estimated_output_tokens",
        "Estimated output tokens for Claude API usage",
        ["user", "model", "date", "project"],
        registry=registry,
    )

    # Aggregated metrics (without user dimension for overview)
    total_requests_gauge = Gauge(
        "claude_vertex_total_requests",
        "Total Claude API requests across all users",
        ["model", "date", "project"],
        registry=registry,
    )

    total_cost_gauge = Gauge(
        "claude_vertex_total_cost_usd",
        "Total estimated cost in USD across all users",
        ["model", "date", "project"],
        registry=registry,
    )

    total_users_gauge = Gauge(
        "claude_vertex_unique_users",
        "Number of unique users",
        ["date", "project"],
        registry=registry,
    )

    total_input_tokens_gauge = Gauge(
        "claude_vertex_total_input_tokens",
        "Total estimated input tokens across all users",
        ["model", "date", "project"],
        registry=registry,
    )

    total_output_tokens_gauge = Gauge(
        "claude_vertex_total_output_tokens",
        "Total estimated output tokens across all users",
        ["model", "date", "project"],
        registry=registry,
    )

    # Populate per-user metrics
    model_totals = defaultdict(
        lambda: {"requests": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0}
    )
    unique_users = set()

    for (email, model), count in usage.items():
        cost_info = estimate_cost(count, model, avg_input, avg_output, use_calibrated)

        # Sanitize email for Prometheus labels (replace @ and . with _)
        sanitized_email = email.replace("@", "_at_").replace(".", "_")

        requests_gauge.labels(
            user=sanitized_email, model=model, date=date_str, project=project_id
        ).set(count)

        cost_gauge.labels(
            user=sanitized_email, model=model, date=date_str, project=project_id
        ).set(cost_info["cost_usd"])

        input_tokens_gauge.labels(
            user=sanitized_email, model=model, date=date_str, project=project_id
        ).set(cost_info["input_tokens"])

        output_tokens_gauge.labels(
            user=sanitized_email, model=model, date=date_str, project=project_id
        ).set(cost_info["output_tokens"])

        # Accumulate totals
        model_totals[model]["requests"] += count
        model_totals[model]["cost"] += cost_info["cost_usd"]
        model_totals[model]["input_tokens"] += cost_info["input_tokens"]
        model_totals[model]["output_tokens"] += cost_info["output_tokens"]
        unique_users.add(email)

    # Populate aggregated metrics
    for model, totals in model_totals.items():
        total_requests_gauge.labels(model=model, date=date_str, project=project_id).set(
            totals["requests"]
        )

        total_cost_gauge.labels(model=model, date=date_str, project=project_id).set(
            totals["cost"]
        )

        total_input_tokens_gauge.labels(
            model=model, date=date_str, project=project_id
        ).set(totals["input_tokens"])

        total_output_tokens_gauge.labels(
            model=model, date=date_str, project=project_id
        ).set(totals["output_tokens"])

    total_users_gauge.labels(date=date_str, project=project_id).set(len(unique_users))

    # Push to gateway
    push_to_gateway(pushgateway_url, job=job_name, registry=registry)

    return {
        "total_requests": sum(model_totals[m]["requests"] for m in model_totals),
        "total_cost": sum(model_totals[m]["cost"] for m in model_totals),
        "unique_users": len(unique_users),
        "models": list(model_totals.keys()),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Push Claude Vertex AI usage metrics to Prometheus Pushgateway"
    )
    parser.add_argument("--project", "-p", required=True, help="GCP Project ID")
    parser.add_argument(
        "--date",
        "-d",
        default=None,
        help="Report date (YYYY-MM-DD). Default: yesterday",
    )
    parser.add_argument(
        "--pushgateway",
        "-g",
        default="localhost:9091",
        help="Prometheus Pushgateway URL (default: localhost:9091)",
    )
    parser.add_argument(
        "--job",
        "-j",
        default=None,
        help="Prometheus job name (default: claude_vertex_YYYY-MM-DD based on date)",
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
        "--no-calibrated",
        action="store_true",
        help="Disable calibrated per-model token averages, use defaults instead",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect metrics but do not push to gateway",
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

    # Generate job name based on date if not provided
    job_name = args.job or f"claude_vertex_{target_date.strftime('%Y-%m-%d')}"

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

    # Build filter and fetch logs
    filter_str = build_filter(target_date)
    entries = fetch_logs(args.project, filter_str)

    logger.info("Log entries fetched: %d", len(entries))

    if not entries:
        logger.info("No log entries found.")
        sys.exit(0)

    # Aggregate usage
    usage = aggregate_usage(entries)

    if not usage:
        logger.info("No Claude API calls identified.")
        sys.exit(0)

    logger.info("Claude API calls identified: %d", sum(usage.values()))

    if args.dry_run:
        logger.info("[DRY RUN] Metrics that would be pushed:")
        total_cost = 0
        for (email, model), count in sorted(
            usage.items(), key=lambda x: x[1], reverse=True
        ):
            cost_info = estimate_cost(
                count,
                model,
                args.avg_input_tokens,
                args.avg_output_tokens,
                use_calibrated,
            )
            total_cost += cost_info["cost_usd"]
            logger.info(
                "  %s / %s: %d requests, $%.4f",
                email,
                model,
                count,
                cost_info["cost_usd"],
            )
        logger.info("Total estimated cost: $%.2f", total_cost)
        logger.info("Metrics NOT pushed (dry run mode)")
    else:
        # Push metrics to gateway
        try:
            result = push_metrics_to_gateway(
                usage=usage,
                target_date=target_date,
                pushgateway_url=pushgateway_url,
                job_name=job_name,
                project_id=args.project,
                avg_input=args.avg_input_tokens,
                avg_output=args.avg_output_tokens,
                use_calibrated=use_calibrated,
            )

            logger.info("Metrics pushed successfully to %s", pushgateway_url)
            logger.info("  Total requests: %d", result["total_requests"])
            logger.info("  Estimated cost: $%.2f", result["total_cost"])
            logger.info("  Unique users: %d", result["unique_users"])
            logger.info("  Models: %s", ", ".join(result["models"]))

        except Exception as e:
            logger.error("Failed to push metrics: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()
