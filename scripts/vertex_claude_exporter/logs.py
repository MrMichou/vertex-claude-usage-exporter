"""Cloud Logging query and fetch logic."""

import logging
import sys
import time
import traceback
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
    """Build Cloud Logging filter for the target date.

    Converts the target date to UTC if it has timezone info,
    then queries from midnight to midnight.
    """
    if target_date.tzinfo is not None:
        target_date = target_date.astimezone(timezone.utc)

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


def fetch_logs(
    project_id: str,
    filter_str: str,
    max_retries: int = 3,
    use_grpc: bool = True,
) -> list:
    """Fetch logs from Cloud Logging with retry on transient errors.

    Args:
        project_id: GCP project ID.
        filter_str: Cloud Logging filter string.
        max_retries: Number of retry attempts for transient errors.
        use_grpc: Use gRPC transport. Set to False when behind HTTP proxies.
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Fetching logs (attempt %d/%d)...", attempt, max_retries)
            client = cloud_logging.Client(project=project_id, _use_grpc=use_grpc)
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
