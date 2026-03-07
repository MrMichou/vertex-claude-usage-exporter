#!/usr/bin/env python3
"""
Basic Claude usage report on Vertex AI.
Identifies users by API call count. Outputs CSV or JSON.
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from vertex_claude_exporter import aggregate_usage, build_filter, fetch_logs

logger = logging.getLogger(__name__)


def generate_report(
    usage: dict, target_date: datetime, output_format: str, output_file: str
):
    """Generate report in the specified format."""
    # Flatten (email, model) -> email-only aggregation for this simple report
    user_counts = {}
    for (email, _model), count in usage.items():
        user_counts[email] = user_counts.get(email, 0) + count

    sorted_usage = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)
    date_str = target_date.strftime("%Y-%m-%d")

    if output_format == "csv":
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "email", "request_count", "rank"])
            for rank, (email, count) in enumerate(sorted_usage, 1):
                writer.writerow([date_str, email, count, rank])

    elif output_format == "json":
        report = {
            "report_date": date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_requests": sum(user_counts.values()),
            "unique_users": len(user_counts),
            "top_users": [
                {"rank": rank, "email": email, "request_count": count}
                for rank, (email, count) in enumerate(sorted_usage, 1)
            ],
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Report generated: %s", output_file)
    logger.info("Total requests: %d", sum(user_counts.values()))
    logger.info("Unique users: %d", len(user_counts))

    if sorted_usage:
        logger.info("Top 10 users:")
        for rank, (email, count) in enumerate(sorted_usage[:10], 1):
            logger.info("  %d. %s: %d requests", rank, email, count)


def main():
    parser = argparse.ArgumentParser(
        description="Basic Claude usage report on Vertex AI"
    )
    parser.add_argument("--project", "-p", required=True, help="GCP Project ID")
    parser.add_argument(
        "--date",
        "-d",
        default=None,
        help="Report date (YYYY-MM-DD). Default: yesterday",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["csv", "json"],
        default="csv",
        help="Output format (csv or json). Default: csv",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file. Default: claude_usage_YYYY-MM-DD.{format}",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

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
        output_file = f"claude_usage_{date_str}.{args.format}"

    logger.info("Project: %s", args.project)
    logger.info("Date: %s", target_date.strftime("%Y-%m-%d"))

    filter_str = build_filter(target_date)
    entries = fetch_logs(args.project, filter_str)

    logger.info("Log entries fetched: %d", len(entries))

    if not entries:
        logger.warning("No entries found. Check:")
        logger.warning("  - Project ID is correct")
        logger.warning(
            "  - Data Access Audit Logs are enabled for aiplatform.googleapis.com"
        )
        logger.warning("  - Claude API calls were made on this date")
        return

    usage = aggregate_usage(entries)

    if not usage:
        logger.info("No Claude API calls identified.")
        return

    generate_report(usage, target_date, args.format, output_file)


if __name__ == "__main__":
    main()
