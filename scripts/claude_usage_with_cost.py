#!/usr/bin/env python3
"""
Rapport d'utilisation Claude sur Vertex AI avec estimation des couts.
Agrege par utilisateur, modele et jour.
"""

import argparse
import csv
import json
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

logger = logging.getLogger(__name__)


# Tarifs Claude sur Vertex AI (par million de tokens) - Janvier 2026
# Source: https://cloud.google.com/vertex-ai/generative-ai/pricing
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

# Moyennes de tokens calibrees par modele (basees sur facturation GCP Janvier 2026)
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

# Valeurs par defaut (fallback)
AVG_TOKENS_PER_REQUEST = {
    'input': 3000,
    'output': 1200,
}

# Transient error types for retry logic
_TRANSIENT_ERRORS = (
    api_exceptions.TooManyRequests,
    api_exceptions.ServiceUnavailable,
    api_exceptions.InternalServerError,
    api_exceptions.GatewayTimeout,
    ConnectionError,
)


def build_filter(start_date: datetime, end_date: datetime = None) -> str:
    """Construit le filtre Cloud Logging pour une date ou une plage de dates."""
    start_ts = start_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    if end_date:
        end_ts = end_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(days=1)
    else:
        end_ts = start_ts + timedelta(days=1)

    start_str = start_ts.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_ts.strftime('%Y-%m-%dT%H:%M:%SZ')

    filter_parts = [
        'protoPayload.serviceName="aiplatform.googleapis.com"',
        'protoPayload.methodName=~"rawPredict|streamRawPredict|Predict"',
        f'timestamp >= "{start_str}"',
        f'timestamp < "{end_str}"',
    ]

    return ' '.join(filter_parts)


def fetch_logs(project_id: str, filter_str: str, max_retries: int = 3) -> list:
    """Recupere les logs depuis Cloud Logging avec retry sur erreurs transitoires."""
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Fetching logs (attempt %d/%d)...", attempt, max_retries)
            client = cloud_logging.Client(project=project_id)
            entries = []

            for entry in client.list_entries(
                filter_=filter_str,
                order_by=DESCENDING,
                page_size=1000
            ):
                entries.append(entry)

            return entries

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
    """Extrait le nom du modele depuis resourceName."""
    match = re.search(r'models/([^@/]+)', resource_name)
    if match:
        return match.group(1)
    return 'unknown'


def get_pricing_for_model(model_name: str) -> dict:
    """Retourne les tarifs pour un modele donne, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(PRICING.keys(), key=len, reverse=True):
        if key != 'default' and key in model_lower:
            return PRICING[key]
    return PRICING['default']


def get_token_averages_for_model(model_name: str) -> dict:
    """Retourne les moyennes de tokens calibrees pour un modele, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(MODEL_TOKEN_AVERAGES.keys(), key=len, reverse=True):
        if key != 'default' and key in model_lower:
            return MODEL_TOKEN_AVERAGES[key]
    return MODEL_TOKEN_AVERAGES['default']


def estimate_cost(request_count: int, model_name: str, use_calibrated: bool = True) -> dict:
    """Estime le cout base sur le nombre de requetes et les moyennes de tokens."""
    pricing = get_pricing_for_model(model_name)

    if use_calibrated:
        token_avgs = get_token_averages_for_model(model_name)
        avg_input = token_avgs['input']
        avg_output = token_avgs['output']
    else:
        avg_input = AVG_TOKENS_PER_REQUEST['input']
        avg_output = AVG_TOKENS_PER_REQUEST['output']

    est_input_tokens = request_count * avg_input
    est_output_tokens = request_count * avg_output

    input_cost = (est_input_tokens / 1_000_000) * pricing['input']
    output_cost = (est_output_tokens / 1_000_000) * pricing['output']

    return {
        'input_tokens': est_input_tokens,
        'output_tokens': est_output_tokens,
        'cost_usd': round(input_cost + output_cost, 4)
    }


def parse_entry(entry) -> dict:
    """Parse une entree de log et extrait les informations pertinentes."""
    try:
        api_repr = entry.to_api_repr()
        proto_payload = api_repr.get('protoPayload', {})

        # Email utilisateur
        auth_info = proto_payload.get('authenticationInfo', {})
        email = auth_info.get('principalEmail', 'unknown')

        # Modele
        resource_name = proto_payload.get('resourceName', '')
        model = extract_model_name(resource_name)

        # Verifier si c'est un modele Claude/Anthropic
        if 'claude' not in resource_name.lower() and 'anthropic' not in resource_name.lower():
            return None

        # Ne compter que les "first" operations (eviter les doublons first/last)
        operation = api_repr.get('operation', {})
        if operation.get('last') and not operation.get('first'):
            return None

        return {
            'email': email,
            'model': model
        }
    except Exception as e:
        logger.debug("Failed to parse log entry: %s", e)
        return None


def aggregate_usage(entries: list) -> dict:
    """Agrege l'utilisation par (email, model)."""
    usage = defaultdict(int)
    skipped = 0

    for entry in entries:
        parsed = parse_entry(entry)
        if parsed:
            key = (parsed['email'], parsed['model'])
            usage[key] += 1
        else:
            skipped += 1

    if skipped:
        logger.info("Skipped %d non-Claude/unparseable entries out of %d total", skipped, len(entries))

    return dict(usage)


def generate_report(usage: dict, target_date: datetime, output_file: str):
    """Genere le rapport CSV avec estimations de couts."""
    date_str = target_date.strftime('%Y-%m-%d')

    # Calculer les totaux par utilisateur
    user_totals = defaultdict(lambda: {'requests': 0, 'cost': 0.0})

    rows = []
    for (email, model), count in usage.items():
        cost_info = estimate_cost(count, model)

        rows.append({
            'date': date_str,
            'email': email,
            'model': model,
            'requests': count,
            'est_input_tokens': cost_info['input_tokens'],
            'est_output_tokens': cost_info['output_tokens'],
            'est_cost_usd': cost_info['cost_usd']
        })

        user_totals[email]['requests'] += count
        user_totals[email]['cost'] += cost_info['cost_usd']

    # Trier par cout decroissant
    rows.sort(key=lambda x: x['est_cost_usd'], reverse=True)

    # Ecrire le CSV
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'date', 'email', 'model', 'requests',
            'est_input_tokens', 'est_output_tokens', 'est_cost_usd'
        ])
        writer.writeheader()
        writer.writerows(rows)

    # Afficher le resume
    logger.info("Rapport genere: %s", output_file)
    logger.info("Date: %s", date_str)
    logger.info("Total requetes: %d", sum(r['requests'] for r in rows))
    logger.info("Cout total estime: $%.2f", sum(r['est_cost_usd'] for r in rows))

    # Top utilisateurs par cout
    sorted_users = sorted(user_totals.items(), key=lambda x: x[1]['cost'], reverse=True)
    logger.info("Top 10 utilisateurs par cout estime:")
    logger.info("-" * 60)
    for i, (email, data) in enumerate(sorted_users[:10], 1):
        logger.info("  %2d. %-35s %5d req  $%8.2f", i, email, data['requests'], data['cost'])

    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Rapport d'utilisation Claude sur Vertex AI avec couts estimes"
    )
    parser.add_argument(
        '--project', '-p',
        required=True,
        help='ID du projet GCP'
    )
    parser.add_argument(
        '--date', '-d',
        default=None,
        help='Date du rapport (YYYY-MM-DD). Defaut: hier'
    )
    parser.add_argument(
        '--output', '-o',
        default=None,
        help='Fichier de sortie. Defaut: claude_cost_YYYY-MM-DD.csv'
    )
    parser.add_argument(
        '--avg-input-tokens',
        type=int,
        default=AVG_TOKENS_PER_REQUEST['input'],
        help=f"Tokens input moyens par requete (defaut: {AVG_TOKENS_PER_REQUEST['input']})"
    )
    parser.add_argument(
        '--avg-output-tokens',
        type=int,
        default=AVG_TOKENS_PER_REQUEST['output'],
        help=f"Tokens output moyens par requete (defaut: {AVG_TOKENS_PER_REQUEST['output']})"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Valider les tokens
    if args.avg_input_tokens <= 0:
        logger.error("--avg-input-tokens must be > 0, got %d", args.avg_input_tokens)
        sys.exit(1)
    if args.avg_output_tokens <= 0:
        logger.error("--avg-output-tokens must be > 0, got %d", args.avg_output_tokens)
        sys.exit(1)

    # Mettre a jour les moyennes si specifiees
    AVG_TOKENS_PER_REQUEST['input'] = args.avg_input_tokens
    AVG_TOKENS_PER_REQUEST['output'] = args.avg_output_tokens

    # Determiner la date cible
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

    # Determiner le fichier de sortie
    if args.output:
        output_file = args.output
    else:
        date_str = target_date.strftime('%Y-%m-%d')
        output_file = f"claude_cost_{date_str}.csv"

    logger.info("Projet: %s", args.project)
    logger.info("Date: %s", target_date.strftime('%Y-%m-%d'))
    logger.info("Estimation basee sur: %d input + %d output tokens/requete",
                AVG_TOKENS_PER_REQUEST['input'], AVG_TOKENS_PER_REQUEST['output'])

    # Construire le filtre et recuperer les logs
    filter_str = build_filter(target_date)
    entries = fetch_logs(args.project, filter_str)

    logger.info("Entrees de log recuperees: %d", len(entries))

    if not entries:
        logger.info("Aucune entree trouvee.")
        return

    # Agreger l'utilisation
    usage = aggregate_usage(entries)

    if not usage:
        logger.info("Aucun appel Claude identifie.")
        return

    # Generer le rapport
    generate_report(usage, target_date, output_file)

    logger.warning("Note: Les couts sont ESTIMES bases sur des moyennes de tokens.")
    logger.info("Pour des couts precis, activez le Request-Response Logging vers BigQuery.")


if __name__ == '__main__':
    main()
