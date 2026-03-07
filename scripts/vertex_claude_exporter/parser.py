"""Log entry parsing and aggregation."""

import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)


def extract_model_name(resource_name: str) -> str:
    """Extract model name from Vertex AI resourceName."""
    match = re.search(r"models/([^@/]+)", resource_name)
    if match:
        return match.group(1)
    return "unknown"


def parse_entry(entry) -> dict:
    """Parse a Cloud Logging entry and extract user email and model.

    Returns None for non-Claude entries or streaming duplicates.
    """
    try:
        api_repr = entry.to_api_repr()
        proto_payload = api_repr.get("protoPayload", {})

        auth_info = proto_payload.get("authenticationInfo", {})
        email = auth_info.get("principalEmail", "unknown")

        resource_name = proto_payload.get("resourceName", "")
        model = extract_model_name(resource_name)

        # Filter: only Claude/Anthropic models
        resource_lower = resource_name.lower()
        if "claude" not in resource_lower and "anthropic" not in resource_lower:
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
    """Aggregate usage by (email, model).

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
