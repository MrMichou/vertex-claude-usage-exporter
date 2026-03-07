#!/usr/bin/env python3
"""
Rapport quotidien d'utilisation Claude sur Vertex AI.
Identifie les utilisateurs par nombre d'appels API.
"""

import argparse
import csv
import json
import logging
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from google.auth import exceptions as auth_exceptions
from google.api_core import exceptions as api_exceptions
from google.cloud import logging as cloud_logging
from google.cloud.logging_v2 import DESCENDING

logger = logging.getLogger(__name__)

# Transient error types for retry logic
_TRANSIENT_ERRORS = (
    api_exceptions.TooManyRequests,
    api_exceptions.ServiceUnavailable,
    api_exceptions.InternalServerError,
    api_exceptions.GatewayTimeout,
    ConnectionError,
)


def build_filter(target_date: datetime) -> str:
    """Construit le filtre Cloud Logging pour les appels Claude sur Vertex AI."""
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
    """Recupere les logs depuis Cloud Logging avec retry sur erreurs transitoires."""
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Fetching logs (attempt %d/%d)...", attempt, max_retries)
            logger.info("Filter: %s", filter_str)
            client = cloud_logging.Client(project=project_id)
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


def is_claude_request(entry) -> bool:
    """Verifie si l'entree de log est un appel a un modele Claude/Anthropic."""
    try:
        api_repr = entry.to_api_repr()
        proto_payload = api_repr.get("protoPayload", {})

        # Verifier dans resourceName
        resource_name = proto_payload.get("resourceName", "").lower()
        if "claude" in resource_name or "anthropic" in resource_name:
            return True

        # Verifier dans request
        request = proto_payload.get("request", {})
        if isinstance(request, dict):
            endpoint = str(request.get("endpoint", "")).lower()
            if "claude" in endpoint or "anthropic" in endpoint:
                return True

            # Verifier dans le corps de la requete si present
            request_str = json.dumps(request).lower()
            if "claude" in request_str or "anthropic" in request_str:
                return True

        # Verifier dans la representation complete en dernier recours
        full_str = json.dumps(api_repr).lower()
        return "claude" in full_str or "anthropic" in full_str

    except Exception as e:
        logger.debug("Failed to check if entry is Claude request: %s", e)
        return False


def extract_user_email(entry) -> str:
    """Extrait l'email de l'utilisateur depuis l'entree de log."""
    try:
        api_repr = entry.to_api_repr()
        proto_payload = api_repr.get("protoPayload", {})
        auth_info = proto_payload.get("authenticationInfo", {})
        email = auth_info.get("principalEmail", "")

        if email:
            return email
    except Exception as e:
        logger.debug("Failed to extract user email: %s", e)

    return "unknown"


def aggregate_usage(entries: list) -> dict:
    """Agrege l'utilisation par utilisateur."""
    usage = defaultdict(int)
    claude_count = 0
    skipped = 0

    for entry in entries:
        if is_claude_request(entry):
            claude_count += 1
            email = extract_user_email(entry)
            usage[email] += 1
        else:
            skipped += 1

    logger.info("Claude entries identified: %d/%d", claude_count, len(entries))
    if skipped:
        logger.info(
            "Skipped %d non-Claude entries out of %d total", skipped, len(entries)
        )

    return dict(usage)


def generate_report(
    usage: dict, target_date: datetime, output_format: str, output_file: str
):
    """Genere le rapport dans le format specifie."""
    sorted_usage = sorted(usage.items(), key=lambda x: x[1], reverse=True)
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
            "total_requests": sum(usage.values()),
            "unique_users": len(usage),
            "top_users": [
                {"rank": rank, "email": email, "request_count": count}
                for rank, (email, count) in enumerate(sorted_usage, 1)
            ],
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Rapport genere: %s", output_file)
    logger.info("Total requetes: %d", sum(usage.values()))
    logger.info("Utilisateurs uniques: %d", len(usage))

    if sorted_usage:
        logger.info("Top 10 utilisateurs:")
        for rank, (email, count) in enumerate(sorted_usage[:10], 1):
            logger.info("  %d. %s: %d requetes", rank, email, count)


def main():
    parser = argparse.ArgumentParser(
        description="Rapport d'utilisation Claude sur Vertex AI"
    )
    parser.add_argument("--project", "-p", required=True, help="ID du projet GCP")
    parser.add_argument(
        "--date", "-d", default=None, help="Date du rapport (YYYY-MM-DD). Defaut: hier"
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["csv", "json"],
        default="csv",
        help="Format de sortie (csv ou json). Defaut: csv",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Fichier de sortie. Defaut: claude_usage_YYYY-MM-DD.{format}",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Determiner la date cible
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

    # Determiner le fichier de sortie
    if args.output:
        output_file = args.output
    else:
        date_str = target_date.strftime("%Y-%m-%d")
        output_file = f"claude_usage_{date_str}.{args.format}"

    logger.info("Projet: %s", args.project)
    logger.info("Date: %s", target_date.strftime("%Y-%m-%d"))

    # Construire le filtre et recuperer les logs
    filter_str = build_filter(target_date)
    entries = fetch_logs(args.project, filter_str)

    logger.info("Entrees de log recuperees: %d", len(entries))

    if not entries:
        logger.warning("Aucune entree trouvee. Verifiez:")
        logger.warning("  - L'ID du projet est correct")
        logger.warning(
            "  - Les Data Access Audit Logs sont actives pour aiplatform.googleapis.com"
        )
        logger.warning("  - Des appels Claude ont ete effectues a cette date")
        return

    # Agreger l'utilisation
    usage = aggregate_usage(entries)

    if not usage:
        logger.info("Aucun appel Claude identifie dans les logs.")
        return

    # Generer le rapport
    generate_report(usage, target_date, args.format, output_file)


if __name__ == "__main__":
    main()
