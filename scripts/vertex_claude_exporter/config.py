"""Pricing and token calibration constants."""

# Pricing per million tokens (January 2026)
# Source: https://cloud.google.com/vertex-ai/generative-ai/pricing
# Prompt caching: cache_write = 1.25x input (5-min TTL), cache_read = 0.1x input
PRICING = {
    "claude-fable-5": {
        "input": 10.00,
        "output": 50.00,
        "cache_write": 12.50,
        "cache_read": 1.00,
    },
    "claude-opus-4-8": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
    },
    "claude-opus-4-7": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
    },
    "claude-opus-4-6": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
    },
    "claude-opus-4-5": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
    },
    "claude-opus-4": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
    "claude-3-opus": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-sonnet-4-5": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-sonnet-4": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-3-5-sonnet": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
    "claude-3-5-haiku": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
    "count-tokens": {
        "input": 0.00,
        "output": 0.00,
        "cache_write": 0.00,
        "cache_read": 0.00,
    },
    "default": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
}

# Calibrated token averages per model (based on January 2026 GCP billing data)
# Calibrated from June 2026 GCP billing (Jun 1-10 SKU export / pushed request
# counts). Estimated cost with these averages reproduces the invoice per model
# to within 0.1%. The values absorb the account's billed-vs-list factor
# (~1.52x on pre-4.7 models = CAD conversion x1.38 plus a 1.1x premium,
# ~1.38x on opus-4-8), so the "USD" metrics actually track invoice currency.
MODEL_TOKEN_AVERAGES = {
    # claude-fable-5: not billing-calibrated (no billing SKU yet) — Opus
    # averages x1.3 (Fable 5 tokenizer yields ~30% more tokens)
    "claude-fable-5": {
        "input": 11532,
        "output": 4612,
        "cache_write": 0,
        "cache_read": 0,
    },
    "claude-opus-4-8": {
        "input": 177,
        "output": 464,
        "cache_write": 4748,
        "cache_read": 49795,
    },
    # claude-opus-4-7 / 4-5: no billing lines — reuse the opus-4-6 calibration
    "claude-opus-4-7": {
        "input": 563,
        "output": 620,
        "cache_write": 7274,
        "cache_read": 108176,
    },
    "claude-opus-4-6": {
        "input": 563,
        "output": 620,
        "cache_write": 7274,
        "cache_read": 108176,
    },
    "claude-opus-4-5": {
        "input": 563,
        "output": 620,
        "cache_write": 7274,
        "cache_read": 108176,
    },
    "claude-opus-4": {"input": 8871, "output": 3548, "cache_write": 0, "cache_read": 0},
    "claude-3-opus": {"input": 8871, "output": 3548, "cache_write": 0, "cache_read": 0},
    "claude-sonnet-4-6": {
        "input": 2032,
        "output": 810,
        "cache_write": 11440,
        "cache_read": 99292,
    },
    "claude-sonnet-4-5": {
        "input": 18569,
        "output": 666,
        "cache_write": 11251,
        "cache_read": 67044,
    },
    "claude-sonnet-4": {
        "input": 3309,
        "output": 1323,
        "cache_write": 0,
        "cache_read": 0,
    },
    "claude-3-5-sonnet": {
        "input": 3309,
        "output": 1323,
        "cache_write": 0,
        "cache_read": 0,
    },
    "claude-haiku-4-5": {
        "input": 232,
        "output": 363,
        "cache_write": 3872,
        "cache_read": 50086,
    },
    "claude-3-5-haiku": {
        "input": 382,
        "output": 153,
        "cache_write": 0,
        "cache_read": 0,
    },
    "count-tokens": {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0},
    "default": {"input": 3000, "output": 1200, "cache_write": 0, "cache_read": 0},
}

# Default token averages (fallback when calibration is disabled)
DEFAULT_AVG_INPUT_TOKENS = 3000
DEFAULT_AVG_OUTPUT_TOKENS = 1200
DEFAULT_AVG_CACHE_WRITE_TOKENS = 0
DEFAULT_AVG_CACHE_READ_TOKENS = 0
