"""Cost estimation logic."""

from vertex_claude_exporter.config import (
    PRICING,
    MODEL_TOKEN_AVERAGES,
    DEFAULT_AVG_INPUT_TOKENS,
    DEFAULT_AVG_OUTPUT_TOKENS,
    DEFAULT_AVG_CACHE_WRITE_TOKENS,
    DEFAULT_AVG_CACHE_READ_TOKENS,
)


def get_pricing_for_model(model_name: str) -> dict:
    """Get pricing for a model, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(PRICING.keys(), key=len, reverse=True):
        if key != "default" and key in model_lower:
            return PRICING[key]
    return PRICING["default"]


def get_token_averages_for_model(model_name: str, user_email: str = None) -> dict:
    """Get calibrated token averages for a model, matching longest key first.

    Calibration is uniform per model: every principal gets the same averages.
    ``user_email`` is accepted for call-site compatibility but ignored.
    """
    model_lower = model_name.lower()
    for key in sorted(MODEL_TOKEN_AVERAGES.keys(), key=len, reverse=True):
        if key != "default" and key in model_lower:
            return MODEL_TOKEN_AVERAGES[key]
    return MODEL_TOKEN_AVERAGES["default"]


def estimate_cost(
    request_count: int,
    model_name: str,
    avg_input: int = None,
    avg_output: int = None,
    use_calibrated: bool = True,
    avg_cache_write: int = None,
    avg_cache_read: int = None,
    user_email: str = None,
) -> dict:
    """Estimate cost based on request count and average tokens.

    If use_calibrated is True and avg_input/avg_output are None, uses model-specific
    calibrated token averages derived from actual GCP billing data. user_email is
    accepted for call-site compatibility but no longer affects the estimate
    (calibration is uniform across principals).

    Prompt caching tokens (avg_cache_write/avg_cache_read per request) are billed
    at cache_write (1.25x input) and cache_read (0.1x input) rates.
    """
    pricing = get_pricing_for_model(model_name)

    if use_calibrated and avg_input is None and avg_output is None:
        model_avgs = get_token_averages_for_model(model_name, user_email)
        avg_input = model_avgs["input"]
        avg_output = model_avgs["output"]
        if avg_cache_write is None:
            avg_cache_write = model_avgs["cache_write"]
        if avg_cache_read is None:
            avg_cache_read = model_avgs["cache_read"]
    else:
        if avg_input is None:
            avg_input = DEFAULT_AVG_INPUT_TOKENS
        if avg_output is None:
            avg_output = DEFAULT_AVG_OUTPUT_TOKENS
        if avg_cache_write is None:
            avg_cache_write = DEFAULT_AVG_CACHE_WRITE_TOKENS
        if avg_cache_read is None:
            avg_cache_read = DEFAULT_AVG_CACHE_READ_TOKENS

    est_input_tokens = request_count * avg_input
    est_output_tokens = request_count * avg_output
    est_cache_write_tokens = request_count * avg_cache_write
    est_cache_read_tokens = request_count * avg_cache_read

    input_cost = (est_input_tokens / 1_000_000) * pricing["input"]
    output_cost = (est_output_tokens / 1_000_000) * pricing["output"]
    cache_write_cost = (est_cache_write_tokens / 1_000_000) * pricing["cache_write"]
    cache_read_cost = (est_cache_read_tokens / 1_000_000) * pricing["cache_read"]

    return {
        "input_tokens": est_input_tokens,
        "output_tokens": est_output_tokens,
        "cache_write_tokens": est_cache_write_tokens,
        "cache_read_tokens": est_cache_read_tokens,
        "cost_usd": round(
            input_cost + output_cost + cache_write_cost + cache_read_cost, 4
        ),
    }
