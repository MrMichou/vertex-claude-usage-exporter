#!/usr/bin/env python3
"""
Push Claude usage metrics from Vertex AI to Prometheus Pushgateway for Grafana.
Collects usage data from Cloud Logging and exposes it as Prometheus metrics.
"""

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from vertex_claude_exporter import (
    DEFAULT_AVG_INPUT_TOKENS,
    DEFAULT_AVG_OUTPUT_TOKENS,
    aggregate_usage,
    aggregate_usage_by_hour,
    build_filter,
    estimate_cost,
    fetch_logs,
)

# Fixed Pushgateway job for the live (intra-day) metrics group. Each hourly run
# re-pushes every hour-so-far under this group, so the push is idempotent and
# self-healing, and rotates to the new day on the first run after midnight.
LIVE_JOB_NAME = "claude_vertex_live"

logger = logging.getLogger(__name__)


def push_metrics_to_gateway(
    usage: dict,
    target_date: datetime,
    pushgateway_url: str,
    job_name: str,
    project_id: str,
    avg_input: int = None,
    avg_output: int = None,
    use_calibrated: bool = True,
    avg_cache_write: int = None,
    avg_cache_read: int = None,
):
    """Push metrics to Prometheus Pushgateway."""

    registry = CollectorRegistry()
    date_str = target_date.strftime("%Y-%m-%d")

    # Per-user metrics
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
    cache_write_tokens_gauge = Gauge(
        "claude_vertex_estimated_cache_write_tokens",
        "Estimated prompt cache write tokens for Claude API usage",
        ["user", "model", "date", "project"],
        registry=registry,
    )
    cache_read_tokens_gauge = Gauge(
        "claude_vertex_estimated_cache_read_tokens",
        "Estimated prompt cache read tokens for Claude API usage",
        ["user", "model", "date", "project"],
        registry=registry,
    )

    # Aggregated metrics (without user dimension)
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
    total_cache_write_tokens_gauge = Gauge(
        "claude_vertex_total_cache_write_tokens",
        "Total estimated prompt cache write tokens across all users",
        ["model", "date", "project"],
        registry=registry,
    )
    total_cache_read_tokens_gauge = Gauge(
        "claude_vertex_total_cache_read_tokens",
        "Total estimated prompt cache read tokens across all users",
        ["model", "date", "project"],
        registry=registry,
    )

    # Populate per-user metrics
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

    for (email, model), count in usage.items():
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
        sanitized_email = email.replace("@", "_at_").replace(".", "_")

        labels = dict(
            user=sanitized_email, model=model, date=date_str, project=project_id
        )
        requests_gauge.labels(**labels).set(count)
        cost_gauge.labels(**labels).set(cost_info["cost_usd"])
        input_tokens_gauge.labels(**labels).set(cost_info["input_tokens"])
        output_tokens_gauge.labels(**labels).set(cost_info["output_tokens"])
        cache_write_tokens_gauge.labels(**labels).set(cost_info["cache_write_tokens"])
        cache_read_tokens_gauge.labels(**labels).set(cost_info["cache_read_tokens"])

        model_totals[model]["requests"] += count
        model_totals[model]["cost"] += cost_info["cost_usd"]
        model_totals[model]["input_tokens"] += cost_info["input_tokens"]
        model_totals[model]["output_tokens"] += cost_info["output_tokens"]
        model_totals[model]["cache_write_tokens"] += cost_info["cache_write_tokens"]
        model_totals[model]["cache_read_tokens"] += cost_info["cache_read_tokens"]
        unique_users.add(email)

    # Populate aggregated metrics
    for model, totals in model_totals.items():
        agg_labels = dict(model=model, date=date_str, project=project_id)
        total_requests_gauge.labels(**agg_labels).set(totals["requests"])
        total_cost_gauge.labels(**agg_labels).set(totals["cost"])
        total_input_tokens_gauge.labels(**agg_labels).set(totals["input_tokens"])
        total_output_tokens_gauge.labels(**agg_labels).set(totals["output_tokens"])
        total_cache_write_tokens_gauge.labels(**agg_labels).set(
            totals["cache_write_tokens"]
        )
        total_cache_read_tokens_gauge.labels(**agg_labels).set(
            totals["cache_read_tokens"]
        )

    total_users_gauge.labels(date=date_str, project=project_id).set(len(unique_users))

    push_to_gateway(pushgateway_url, job=job_name, registry=registry)

    return {
        "total_requests": sum(t["requests"] for t in model_totals.values()),
        "total_cost": sum(t["cost"] for t in model_totals.values()),
        "unique_users": len(unique_users),
        "models": list(model_totals.keys()),
    }


def push_live_metrics_to_gateway(
    hourly_usage: dict,
    target_date: datetime,
    pushgateway_url: str,
    project_id: str,
    avg_input: int = None,
    avg_output: int = None,
    use_calibrated: bool = True,
    avg_cache_write: int = None,
    avg_cache_read: int = None,
):
    """Push intra-day "live" metrics bucketed by hour.

    Distinct from the daily batch metrics: a separate ``claude_vertex_live_*``
    family, aggregated at model+hour granularity (no per-user dimension), pushed
    under the fixed ``claude_vertex_live`` job. ``hourly_usage`` maps
    ``(model, hour) -> request count`` for the current day so far.
    """
    registry = CollectorRegistry()
    date_str = target_date.strftime("%Y-%m-%d")

    labels = ["model", "hour", "date", "project"]
    requests_gauge = Gauge(
        "claude_vertex_live_total_requests",
        "Live (intra-day) Claude API requests on Vertex AI, by hour",
        labels,
        registry=registry,
    )
    cost_gauge = Gauge(
        "claude_vertex_live_total_cost_usd",
        "Live (intra-day) estimated cost in USD, by hour",
        labels,
        registry=registry,
    )
    input_tokens_gauge = Gauge(
        "claude_vertex_live_total_input_tokens",
        "Live (intra-day) estimated input tokens, by hour",
        labels,
        registry=registry,
    )
    output_tokens_gauge = Gauge(
        "claude_vertex_live_total_output_tokens",
        "Live (intra-day) estimated output tokens, by hour",
        labels,
        registry=registry,
    )
    cache_write_tokens_gauge = Gauge(
        "claude_vertex_live_total_cache_write_tokens",
        "Live (intra-day) estimated prompt cache write tokens, by hour",
        labels,
        registry=registry,
    )
    cache_read_tokens_gauge = Gauge(
        "claude_vertex_live_total_cache_read_tokens",
        "Live (intra-day) estimated prompt cache read tokens, by hour",
        labels,
        registry=registry,
    )

    total_requests = 0
    total_cost = 0.0
    models = set()

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
        bucket_labels = dict(model=model, hour=hour, date=date_str, project=project_id)
        requests_gauge.labels(**bucket_labels).set(count)
        cost_gauge.labels(**bucket_labels).set(cost_info["cost_usd"])
        input_tokens_gauge.labels(**bucket_labels).set(cost_info["input_tokens"])
        output_tokens_gauge.labels(**bucket_labels).set(cost_info["output_tokens"])
        cache_write_tokens_gauge.labels(**bucket_labels).set(
            cost_info["cache_write_tokens"]
        )
        cache_read_tokens_gauge.labels(**bucket_labels).set(
            cost_info["cache_read_tokens"]
        )

        total_requests += count
        total_cost += cost_info["cost_usd"]
        models.add(model)

    push_to_gateway(pushgateway_url, job=LIVE_JOB_NAME, registry=registry)

    return {
        "total_requests": total_requests,
        "total_cost": total_cost,
        "models": list(models),
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

    if args.live:
        job_name = args.job or LIVE_JOB_NAME
    else:
        job_name = args.job or f"claude_vertex_{target_date.strftime('%Y-%m-%d')}"

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

    # Build filter and fetch logs
    filter_str = build_filter(target_date)
    entries = fetch_logs(args.project, filter_str, use_grpc=False)

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
        if args.dry_run:
            logger.info("[DRY RUN] Live metrics that would be pushed:")
            total_cost = 0
            for (model, hour), count in sorted(usage.items()):
                cost_info = estimate_cost(
                    count,
                    model,
                    args.avg_input_tokens,
                    args.avg_output_tokens,
                    use_calibrated,
                    args.avg_cache_write_tokens,
                    args.avg_cache_read_tokens,
                )
                total_cost += cost_info["cost_usd"]
                logger.info(
                    "  %sh / %s: %d requests, $%.4f",
                    hour,
                    model,
                    count,
                    cost_info["cost_usd"],
                )
            logger.info("Total estimated cost (today so far): $%.2f", total_cost)
            logger.info("Metrics NOT pushed (dry run mode)")
        else:
            try:
                result = push_live_metrics_to_gateway(
                    hourly_usage=usage,
                    target_date=target_date,
                    pushgateway_url=pushgateway_url,
                    project_id=args.project,
                    avg_input=args.avg_input_tokens,
                    avg_output=args.avg_output_tokens,
                    use_calibrated=use_calibrated,
                    avg_cache_write=args.avg_cache_write_tokens,
                    avg_cache_read=args.avg_cache_read_tokens,
                )
                logger.info("Live metrics pushed successfully to %s", pushgateway_url)
                logger.info(
                    "  Total requests (today so far): %d", result["total_requests"]
                )
                logger.info(
                    "  Estimated cost (today so far): $%.2f", result["total_cost"]
                )
                logger.info("  Models: %s", ", ".join(result["models"]))
            except Exception as e:
                logger.error("Failed to push live metrics: %s", e)
                sys.exit(1)
        return

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
                args.avg_cache_write_tokens,
                args.avg_cache_read_tokens,
                user_email=email,
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
                avg_cache_write=args.avg_cache_write_tokens,
                avg_cache_read=args.avg_cache_read_tokens,
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
