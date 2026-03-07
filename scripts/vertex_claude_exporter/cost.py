"""Cost estimation logic."""

from vertex_claude_exporter.config import (
    PRICING,
    MODEL_TOKEN_AVERAGES,
    DEFAULT_AVG_INPUT_TOKENS,
    DEFAULT_AVG_OUTPUT_TOKENS,
)


def get_pricing_for_model(model_name: str) -> dict:
    """Get pricing for a model, matching longest key first."""
    model_lower = model_name.lower()
    for key in sorted(PRICING.keys(), key=len, reverse=True):
        if key != "default" and key in model_lower:
            return PRICING[key]
    return PRICING["default"]


def get_token_averages_for_model(model_name: str) -> dict:
    """Get calibrated token averages for a model, matching longest key first."""
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
) -> dict:
    """Estimate cost based on request count and average tokens.

    If use_calibrated is True and avg_input/avg_output are None, uses model-specific
    calibrated token averages derived from actual GCP billing data.
    """
    pricing = get_pricing_for_model(model_name)

    if use_calibrated and avg_input is None and avg_output is None:
        model_avgs = get_token_averages_for_model(model_name)
        avg_input = model_avgs["input"]
        avg_output = model_avgs["output"]
    else:
        if avg_input is None:
            avg_input = DEFAULT_AVG_INPUT_TOKENS
        if avg_output is None:
            avg_output = DEFAULT_AVG_OUTPUT_TOKENS

    est_input_tokens = request_count * avg_input
    est_output_tokens = request_count * avg_output

    input_cost = (est_input_tokens / 1_000_000) * pricing["input"]
    output_cost = (est_output_tokens / 1_000_000) * pricing["output"]

    return {
        "input_tokens": est_input_tokens,
        "output_tokens": est_output_tokens,
        "cost_usd": round(input_cost + output_cost, 4),
    }
