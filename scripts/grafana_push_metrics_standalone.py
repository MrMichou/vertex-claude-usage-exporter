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


# Pricing per million tokens (January 2026)
PRICING = {
    'claude-opus-4-6': {'input': 5.00, 'output': 25.00},
    'claude-opus-4-5': {'input': 5.00, 'output': 25.00},
    'claude-opus-4': {'input': 15.00, 'output': 75.00},
    'claude-3-opus': {'input': 15.00, 'output': 75.00},
    'claude-sonnet-4-5': {'input': 3.00, 'output': 15.00},
    'claude-sonnet-4': {'input': 3.00, 'output': 15.00},
    'claude-3-5-sonnet': {'input': 3.00, 'output': 15.00},
    'claude-haiku-4-5': {'input': 1.00, 'output': 5.00},
    'claude-3-5-haiku': {'input': 1.00, 'output': 5.00},
    'count-tokens': {'input': 0.00, 'output': 0.00},
    'default': {'input': 3.00, 'output': 15.00},
}

# Calibrated token averages per model (based on January 2026 GCP billing data)
MODEL_TOKEN_AVERAGES = {
    'claude-opus-4-6': {'input': 8871, 'output': 3548},
    'claude-opus-4-5': {'input': 8871, 'output': 3548},
    'claude-opus-4': {'input': 8871, 'output': 3548},
    'claude-3-opus': {'input': 8871, 'output': 3548},
    'claude-sonnet-4-5': {'input': 4820, 'output': 1928},
    'claude-sonnet-4': {'input': 3309, 'output': 1323},
    'claude-3-5-sonnet': {'input': 3309, 'output': 1323},
    'claude-haiku-4-5': {'input': 840, 'output': 336},
    'claude-3-5-haiku': {'input': 382, 'output': 153},
    'count-tokens': {'input': 0, 'output': 0},
    'default': {'input': 3000, 'output': 1200},
}

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
    start_ts = target_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end_ts = start_ts + timedelta(days=1)
    start_str = start_ts.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_ts.strftime('%Y-%m-%dT%H:%M:%SZ')

    return ' '.join([
        'protoPayload.serviceName="aiplatform.googleapis.com"',
        'protoPayload.methodName=~"rawPredict|streamRawPredict|Predict"',
        f'timestamp >= "{start_str}"',
        f'timestamp < "{end_str}"',
    ])


def fetch_logs(project_id: str, filter_str: str, max_retries: int = 3) -> list:
    """Fetch logs from Cloud Logging with retry on transient errors."""
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Fetching logs (attempt %d/%d)...", attempt, max_retries)
            client = cloud_logging.Client(project=project_id)
            return list(client.list_entries(filter_=filter_str, order_by=DESCENDING, page_size=1000))

        except (auth_exceptions.DefaultCredentialsError, api_exceptions.PermissionDenied) as e:
            logger.error("Authentication/permission error (not retryable): %s", e)
            sys.exit(1)

        except _TRANSIENT_ERRORS as e:
            if attempt < max_retries:
                wait = 2 ** attempt
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
    match = re.search(r'models/([^@/]+)', resource_name)
    return match.group(1) if match else 'unknown'


def get_pricing_for_model(model_name: str) -> dict:
    """Get pricing for a model, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(PRICING.keys(), key=len, reverse=True):
        if key != 'default' and key in model_lower:
            return PRICING[key]
    return PRICING['default']


def get_token_averages_for_model(model_name: str) -> dict:
    """Get calibrated token averages for a model, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(MODEL_TOKEN_AVERAGES.keys(), key=len, reverse=True):
        if key != 'default' and key in model_lower:
            return MODEL_TOKEN_AVERAGES[key]
    return MODEL_TOKEN_AVERAGES['default']


def parse_entry(entry) -> dict:
    try:
        api_repr = entry.to_api_repr()
        proto_payload = api_repr.get('protoPayload', {})
        auth_info = proto_payload.get('authenticationInfo', {})
        email = auth_info.get('principalEmail', 'unknown')
        resource_name = proto_payload.get('resourceName', '')
        model = extract_model_name(resource_name)

        if 'claude' not in resource_name.lower() and 'anthropic' not in resource_name.lower():
            return None

        # Deduplicate streaming requests: skip "last-only" operations
        operation = api_repr.get('operation', {})
        if operation.get('last') and not operation.get('first'):
            return None

        return {'email': email, 'model': model}
    except Exception as e:
        logger.debug("Failed to parse log entry: %s", e)
        return None


def aggregate_usage(entries: list) -> dict:
    usage = defaultdict(int)
    skipped = 0
    for entry in entries:
        parsed = parse_entry(entry)
        if parsed:
            usage[(parsed['email'], parsed['model'])] += 1
        else:
            skipped += 1

    if skipped:
        logger.info("Skipped %d non-Claude/unparseable entries out of %d total", skipped, len(entries))

    return dict(usage)


def estimate_cost(request_count: int, model_name: str, avg_input: int = None, avg_output: int = None, use_calibrated: bool = True) -> dict:
    pricing = get_pricing_for_model(model_name)

    # Use calibrated model-specific averages if not overridden
    if use_calibrated and avg_input is None and avg_output is None:
        model_avgs = get_token_averages_for_model(model_name)
        avg_input = model_avgs['input']
        avg_output = model_avgs['output']
    else:
        if avg_input is None:
            avg_input = DEFAULT_AVG_INPUT_TOKENS
        if avg_output is None:
            avg_output = DEFAULT_AVG_OUTPUT_TOKENS

    est_input = request_count * avg_input
    est_output = request_count * avg_output
    input_cost = (est_input / 1_000_000) * pricing['input']
    output_cost = (est_output / 1_000_000) * pricing['output']
    return {
        'input_tokens': est_input,
        'output_tokens': est_output,
        'cost_usd': round(input_cost + output_cost, 4)
    }


def generate_prometheus_metrics(usage: dict, target_date: datetime, project_id: str,
                                 avg_input: int, avg_output: int, use_calibrated: bool = True) -> str:
    """Generate Prometheus text format metrics."""
    lines = []
    date_str = target_date.strftime('%Y-%m-%d')
    model_totals = defaultdict(lambda: {'requests': 0, 'cost': 0.0, 'input_tokens': 0, 'output_tokens': 0})
    unique_users = set()

    # Per-user metrics: requests
    lines.append('# HELP claude_vertex_requests_total Total Claude API requests on Vertex AI')
    lines.append('# TYPE claude_vertex_requests_total gauge')
    for (email, model), count in usage.items():
        sanitized_email = email.replace('@', '_at_').replace('.', '_')
        lines.append(f'claude_vertex_requests_total{{user="{sanitized_email}",model="{model}",date="{date_str}",project="{project_id}"}} {count}')
        model_totals[model]['requests'] += count
        unique_users.add(email)

    # Per-user metrics: cost and tokens
    lines.append('# HELP claude_vertex_estimated_cost_usd Estimated cost in USD')
    lines.append('# TYPE claude_vertex_estimated_cost_usd gauge')
    lines.append('# HELP claude_vertex_estimated_input_tokens Estimated input tokens')
    lines.append('# TYPE claude_vertex_estimated_input_tokens gauge')
    lines.append('# HELP claude_vertex_estimated_output_tokens Estimated output tokens')
    lines.append('# TYPE claude_vertex_estimated_output_tokens gauge')
    for (email, model), count in usage.items():
        sanitized_email = email.replace('@', '_at_').replace('.', '_')
        cost_info = estimate_cost(count, model, avg_input, avg_output, use_calibrated)
        labels = f'user="{sanitized_email}",model="{model}",date="{date_str}",project="{project_id}"'
        lines.append(f'claude_vertex_estimated_cost_usd{{{labels}}} {cost_info["cost_usd"]}')
        lines.append(f'claude_vertex_estimated_input_tokens{{{labels}}} {cost_info["input_tokens"]}')
        lines.append(f'claude_vertex_estimated_output_tokens{{{labels}}} {cost_info["output_tokens"]}')
        model_totals[model]['cost'] += cost_info['cost_usd']
        model_totals[model]['input_tokens'] += cost_info['input_tokens']
        model_totals[model]['output_tokens'] += cost_info['output_tokens']

    # Aggregated metrics
    lines.append('# HELP claude_vertex_total_requests Total requests per model')
    lines.append('# TYPE claude_vertex_total_requests gauge')
    for model, totals in model_totals.items():
        lines.append(f'claude_vertex_total_requests{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["requests"]}')

    lines.append('# HELP claude_vertex_total_cost_usd Total cost per model')
    lines.append('# TYPE claude_vertex_total_cost_usd gauge')
    for model, totals in model_totals.items():
        lines.append(f'claude_vertex_total_cost_usd{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["cost"]}')

    lines.append('# HELP claude_vertex_total_input_tokens Total estimated input tokens per model')
    lines.append('# TYPE claude_vertex_total_input_tokens gauge')
    for model, totals in model_totals.items():
        lines.append(f'claude_vertex_total_input_tokens{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["input_tokens"]}')

    lines.append('# HELP claude_vertex_total_output_tokens Total estimated output tokens per model')
    lines.append('# TYPE claude_vertex_total_output_tokens gauge')
    for model, totals in model_totals.items():
        lines.append(f'claude_vertex_total_output_tokens{{model="{model}",date="{date_str}",project="{project_id}"}} {totals["output_tokens"]}')

    lines.append('# HELP claude_vertex_unique_users Number of unique users')
    lines.append('# TYPE claude_vertex_unique_users gauge')
    lines.append(f'claude_vertex_unique_users{{date="{date_str}",project="{project_id}"}} {len(unique_users)}')

    return '\n'.join(lines) + '\n'


def push_to_gateway(pushgateway_url: str, job_name: str, metrics_text: str):
    """Push metrics to Pushgateway using HTTP POST."""
    url = f'http://{pushgateway_url}/metrics/job/{job_name}'
    data = metrics_text.encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'text/plain; charset=utf-8')

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
    parser = argparse.ArgumentParser(description="Push Claude usage metrics to Prometheus Pushgateway")
    parser.add_argument('--project', '-p', required=True, help='GCP Project ID')
    parser.add_argument('--date', '-d', default=None, help='Report date (YYYY-MM-DD). Default: yesterday')
    parser.add_argument('--pushgateway', '-g', default='localhost:9091', help='Pushgateway URL')
    parser.add_argument('--job', '-j', default='claude_vertex_usage', help='Prometheus job name')
    parser.add_argument('--avg-input-tokens', type=int, default=None,
                        help='Override average input tokens per request (disables per-model calibration)')
    parser.add_argument('--avg-output-tokens', type=int, default=None,
                        help='Override average output tokens per request (disables per-model calibration)')
    parser.add_argument('--no-calibrated', action='store_true',
                        help='Disable calibrated per-model token averages, use defaults instead')
    parser.add_argument('--dry-run', action='store_true', help='Show metrics without pushing')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Validate date
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
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
    if pushgateway_url.startswith('http://') or pushgateway_url.startswith('https://'):
        logger.warning("Stripping URL scheme from pushgateway address: %s", pushgateway_url)
        pushgateway_url = pushgateway_url.split('://', 1)[1]

    # Determine if using calibrated mode
    use_calibrated = not args.no_calibrated and args.avg_input_tokens is None and args.avg_output_tokens is None

    logger.info("Project: %s", args.project)
    logger.info("Date: %s", target_date.strftime('%Y-%m-%d'))
    logger.info("Pushgateway: %s", pushgateway_url)
    if use_calibrated:
        logger.info("Token estimates: Using calibrated per-model averages")
    else:
        avg_in = args.avg_input_tokens or DEFAULT_AVG_INPUT_TOKENS
        avg_out = args.avg_output_tokens or DEFAULT_AVG_OUTPUT_TOKENS
        logger.info("Token estimates: %d input, %d output (global override)", avg_in, avg_out)

    filter_str = build_filter(target_date)
    entries = fetch_logs(args.project, filter_str)
    logger.info("Log entries fetched: %d", len(entries))

    if not entries:
        logger.info("No log entries found.")
        sys.exit(0)

    usage = aggregate_usage(entries)
    if not usage:
        logger.info("No Claude API calls identified.")
        sys.exit(0)

    logger.info("Claude API calls identified: %d", sum(usage.values()))

    metrics_text = generate_prometheus_metrics(
        usage, target_date, args.project,
        args.avg_input_tokens, args.avg_output_tokens, use_calibrated
    )

    if args.dry_run:
        logger.info("[DRY RUN] Metrics:\n%s", metrics_text)
    else:
        try:
            push_to_gateway(pushgateway_url, args.job, metrics_text)
            logger.info("Metrics pushed to %s", pushgateway_url)
        except Exception as e:
            logger.error("Failed: %s", e)
            sys.exit(1)


if __name__ == '__main__':
    main()
