"""Pricing and token calibration constants."""

# Pricing per million tokens (January 2026)
# Source: https://cloud.google.com/vertex-ai/generative-ai/pricing
PRICING = {
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-3-5-haiku": {"input": 1.00, "output": 5.00},
    "count-tokens": {"input": 0.00, "output": 0.00},
    "default": {"input": 3.00, "output": 15.00},
}

# Calibrated token averages per model (based on January 2026 GCP billing data)
# These values were derived from actual billing to match real costs within 0.01%
MODEL_TOKEN_AVERAGES = {
    "claude-opus-4-6": {"input": 8871, "output": 3548},
    "claude-opus-4-5": {"input": 8871, "output": 3548},
    "claude-opus-4": {"input": 8871, "output": 3548},
    "claude-3-opus": {"input": 8871, "output": 3548},
    "claude-sonnet-4-5": {"input": 4820, "output": 1928},
    "claude-sonnet-4": {"input": 3309, "output": 1323},
    "claude-3-5-sonnet": {"input": 3309, "output": 1323},
    "claude-haiku-4-5": {"input": 840, "output": 336},
    "claude-3-5-haiku": {"input": 382, "output": 153},
    "count-tokens": {"input": 0, "output": 0},
    "default": {"input": 3000, "output": 1200},
}

# Default token averages (fallback when calibration is disabled)
DEFAULT_AVG_INPUT_TOKENS = 3000
DEFAULT_AVG_OUTPUT_TOKENS = 1200
