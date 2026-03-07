#!/usr/bin/env python3
"""
Claude usage report on Vertex AI with cost estimation.
Aggregates by user, model, and day. Outputs CSV.
"""

import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from vertex_claude_exporter import (
    DEFAULT_AVG_INPUT_TOKENS,
    DEFAULT_AVG_OUTPUT_TOKENS,
    aggregate_usage,
    build_filter,
    estimate_cost,
    fetch_logs,
)

logger = logging.getLogger(__name__)


def generate_report(usage: dict, target_date: datetime, output_file: str):
    """Generate CSV cost report."""
    date_str = target_date.strftime("%Y-%m-%d")

    user_totals = defaultdict(lambda: {"requests": 0, "cost": 0.0})

    rows = []
    for (email, model), count in usage.items():
        cost_info = estimate_cost(count, model)

        rows.append(
            {
                "date": date_str,
                "email": email,
                "model": model,
                "requests": count,
                "est_input_tokens": cost_info["input_tokens"],
                "est_output_tokens": cost_info["output_tokens"],
                "est_cost_usd": cost_info["cost_usd"],
            }
        )

        user_totals[email]["requests"] += count
        user_totals[email]["cost"] += cost_info["cost_usd"]

    rows.sort(key=lambda x: x["est_cost_usd"], reverse=True)

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date",
                "email",
                "model",
                "requests",
                "est_input_tokens",
                "est_output_tokens",
                "est_cost_usd",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Report generated: %s", output_file)
    logger.info("Date: %s", date_str)
    logger.info("Total requests: %d", sum(r["requests"] for r in rows))
    logger.info("Total estimated cost: $%.2f", sum(r["est_cost_usd"] for r in rows))

    sorted_users = sorted(user_totals.items(), key=lambda x: x[1]["cost"], reverse=True)
    logger.info("Top 10 users by estimated cost:")
    logger.info("-" * 60)
    for i, (email, data) in enumerate(sorted_users[:10], 1):
        logger.info(
            "  %2d. %-35s %5d req  $%8.2f", i, email, data["requests"], data["cost"]
        )

    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Claude Vertex AI usage report with cost estimation"
    )
    parser.add_argument("--project", "-p", required=True, help="GCP Project ID")
    parser.add_argument(
        "--date",
        "-d",
        default=None,
        help="Report date (YYYY-MM-DD). Default: yesterday",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file. Default: claude_cost_YYYY-MM-DD.csv",
    )
    parser.add_argument(
        "--avg-input-tokens",
        type=int,
        default=DEFAULT_AVG_INPUT_TOKENS,
        help=f"Average input tokens per request (default: {DEFAULT_AVG_INPUT_TOKENS})",
    )
    parser.add_argument(
        "--avg-output-tokens",
        type=int,
        default=DEFAULT_AVG_OUTPUT_TOKENS,
        help=f"Average output tokens per request (default: {DEFAULT_AVG_OUTPUT_TOKENS})",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.avg_input_tokens <= 0:
        logger.error("--avg-input-tokens must be > 0, got %d", args.avg_input_tokens)
        sys.exit(1)
    if args.avg_output_tokens <= 0:
        logger.error("--avg-output-tokens must be > 0, got %d", args.avg_output_tokens)
        sys.exit(1)

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

    if args.output:
        output_file = args.output
    else:
        date_str = target_date.strftime("%Y-%m-%d")
        output_file = f"claude_cost_{date_str}.csv"

    logger.info("Project: %s", args.project)
    logger.info("Date: %s", target_date.strftime("%Y-%m-%d"))
    logger.info(
        "Estimation based on: %d input + %d output tokens/request",
        args.avg_input_tokens,
        args.avg_output_tokens,
    )

    filter_str = build_filter(target_date)
    entries = fetch_logs(args.project, filter_str)

    logger.info("Log entries fetched: %d", len(entries))

    if not entries:
        logger.info("No entries found.")
        return

    usage = aggregate_usage(entries)

    if not usage:
        logger.info("No Claude API calls identified.")
        return

    generate_report(usage, target_date, output_file)

    logger.warning("Note: Costs are ESTIMATED based on token averages.")
    logger.info("For precise costs, enable Request-Response Logging to BigQuery.")


if __name__ == "__main__":
    main()
