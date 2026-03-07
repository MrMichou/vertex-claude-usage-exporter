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
    build_filter,
    estimate_cost,
    fetch_logs,
)

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

    # Populate per-user metrics
    model_totals = defaultdict(
        lambda: {"requests": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0}
    )
    unique_users = set()

    for (email, model), count in usage.items():
        cost_info = estimate_cost(count, model, avg_input, avg_output, use_calibrated)
        sanitized_email = email.replace("@", "_at_").replace(".", "_")

        labels = dict(
            user=sanitized_email, model=model, date=date_str, project=project_id
        )
        requests_gauge.labels(**labels).set(count)
        cost_gauge.labels(**labels).set(cost_info["cost_usd"])
        input_tokens_gauge.labels(**labels).set(cost_info["input_tokens"])
        output_tokens_gauge.labels(**labels).set(cost_info["output_tokens"])

        model_totals[model]["requests"] += count
        model_totals[model]["cost"] += cost_info["cost_usd"]
        model_totals[model]["input_tokens"] += cost_info["input_tokens"]
        model_totals[model]["output_tokens"] += cost_info["output_tokens"]
        unique_users.add(email)

    # Populate aggregated metrics
    for model, totals in model_totals.items():
        agg_labels = dict(model=model, date=date_str, project=project_id)
        total_requests_gauge.labels(**agg_labels).set(totals["requests"])
        total_cost_gauge.labels(**agg_labels).set(totals["cost"])
        total_input_tokens_gauge.labels(**agg_labels).set(totals["input_tokens"])
        total_output_tokens_gauge.labels(**agg_labels).set(totals["output_tokens"])

    total_users_gauge.labels(date=date_str, project=project_id).set(len(unique_users))

    push_to_gateway(pushgateway_url, job=job_name, registry=registry)

    return {
        "total_requests": sum(t["requests"] for t in model_totals.values()),
        "total_cost": sum(t["cost"] for t in model_totals.values()),
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
    entries = fetch_logs(args.project, filter_str, use_grpc=False)

    logger.info("Log entries fetched: %d", len(entries))

    if not entries:
        logger.info("No log entries found.")
        sys.exit(0)

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
