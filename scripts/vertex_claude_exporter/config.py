"""Pricing and token calibration constants."""

# Pricing per million tokens (January 2026)
# Source: https://cloud.google.com/vertex-ai/generative-ai/pricing
# Prompt caching: cache_write = 1.25x input (5-min TTL), cache_read = 0.1x input
PRICING = {
    # fable-5 / opus-4-7: usage visible in audit logs but NOT billed by GCP
    # as of 2026-06-11 (no billing SKU). Priced at 0 so estimates match the
    # invoice — restore list prices if SKUs appear (possibly backdated).
    "claude-fable-5": {
        "input": 0.00,
        "output": 0.00,
        "cache_write": 0.00,
        "cache_read": 0.00,
    },
    "claude-opus-4-8": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
    },
    "claude-opus-4-7": {
        "input": 0.00,
        "output": 0.00,
        "cache_write": 0.00,
        "cache_read": 0.00,
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

# Recalibrated from June 2026 GCP billing (Jun 1-18 month-to-date SKU export /
# deduplicated pushed request counts). Uniform per-model averages applied to
# every principal (no per-user profiles): for each (model, token-type),
# average = billed_tokens / total_requests, which reproduces the per-model
# invoice to within rounding (aggregate delta +0.003%). The values absorb the
# account's billed-vs-list factor (CAD conversion + premium), so the "USD"
# metrics actually track invoice currency.
MODEL_TOKEN_AVERAGES = {
    # claude-fable-5: not billing-calibrated (no billing SKU yet) — Opus
    # averages x1.3 (Fable 5 tokenizer yields ~30% more tokens)
    "claude-fable-5": {
        "input": 11532,
        "output": 4612,
        "cache_write": 0,
        "cache_read": 0,
    },
    # opus-4-8: Jun 1-18 invoice (usage now established, ~1290 reqs); cache-heavy
    # interactive profile. Was provisional on Jun 11; now billing-confirmed.
    "claude-opus-4-8": {
        "input": 207,
        "output": 1097,
        "cache_write": 15491,
        "cache_read": 105548,
    },
    # opus-4-6: Jun 1-18 invoice, ~10.8K reqs (all principals averaged together).
    # claude-opus-4-7 / 4-5: no billing lines — reuse the opus-4-6 calibration
    "claude-opus-4-7": {
        "input": 449,
        "output": 701,
        "cache_write": 10090,
        "cache_read": 133090,
    },
    "claude-opus-4-6": {
        "input": 449,
        "output": 701,
        "cache_write": 10090,
        "cache_read": 133090,
    },
    "claude-opus-4-5": {
        "input": 449,
        "output": 701,
        "cache_write": 10090,
        "cache_read": 133090,
    },
    "claude-opus-4": {"input": 8871, "output": 3548, "cache_write": 0, "cache_read": 0},
    "claude-3-opus": {"input": 8871, "output": 3548, "cache_write": 0, "cache_read": 0},
    "claude-sonnet-4-6": {
        "input": 1833,
        "output": 988,
        "cache_write": 13388,
        "cache_read": 109720,
    },
    "claude-sonnet-4-5": {
        "input": 14089,
        "output": 641,
        "cache_write": 12218,
        "cache_read": 67746,
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
        "input": 524,
        "output": 411,
        "cache_write": 4241,
        "cache_read": 57766,
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
