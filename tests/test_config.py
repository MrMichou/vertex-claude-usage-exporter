"""Tests for config constants."""

import pytest

from vertex_claude_exporter.config import (
    MODEL_TOKEN_AVERAGES,
    PRICING,
)


def test_pricing_has_default():
    assert "default" in PRICING
    assert "input" in PRICING["default"]
    assert "output" in PRICING["default"]


def test_pricing_all_models_have_input_output():
    for model, prices in PRICING.items():
        assert "input" in prices, f"{model} missing 'input'"
        assert "output" in prices, f"{model} missing 'output'"
        assert prices["input"] >= 0, f"{model} has negative input price"
        assert prices["output"] >= 0, f"{model} has negative output price"


def test_pricing_all_models_have_cache_rates():
    for model, prices in PRICING.items():
        assert "cache_write" in prices, f"{model} missing 'cache_write'"
        assert "cache_read" in prices, f"{model} missing 'cache_read'"
        # cache_write = 1.25x input, cache_read = 0.1x input
        assert prices["cache_write"] == pytest.approx(prices["input"] * 1.25), (
            f"{model} cache_write should be 1.25x input"
        )
        assert prices["cache_read"] == pytest.approx(prices["input"] * 0.1), (
            f"{model} cache_read should be 0.1x input"
        )


def test_model_token_averages_has_default():
    assert "default" in MODEL_TOKEN_AVERAGES
    assert MODEL_TOKEN_AVERAGES["default"]["input"] > 0
    assert MODEL_TOKEN_AVERAGES["default"]["output"] > 0


def test_model_token_averages_all_have_input_output():
    for model, avgs in MODEL_TOKEN_AVERAGES.items():
        assert "input" in avgs, f"{model} missing 'input'"
        assert "output" in avgs, f"{model} missing 'output'"
        assert avgs["input"] >= 0, f"{model} has negative input tokens"
        assert avgs["output"] >= 0, f"{model} has negative output tokens"


def test_model_token_averages_all_have_cache_averages():
    for model, avgs in MODEL_TOKEN_AVERAGES.items():
        assert "cache_write" in avgs, f"{model} missing 'cache_write'"
        assert "cache_read" in avgs, f"{model} missing 'cache_read'"
        assert avgs["cache_write"] >= 0, f"{model} has negative cache_write tokens"
        assert avgs["cache_read"] >= 0, f"{model} has negative cache_read tokens"


def test_pricing_and_averages_cover_same_models():
    """Every model in PRICING should also be in MODEL_TOKEN_AVERAGES."""
    for model in PRICING:
        assert model in MODEL_TOKEN_AVERAGES, (
            f"{model} in PRICING but not in MODEL_TOKEN_AVERAGES"
        )


def test_count_tokens_is_zero_cost():
    assert PRICING["count-tokens"]["input"] == 0
    assert PRICING["count-tokens"]["output"] == 0
    assert MODEL_TOKEN_AVERAGES["count-tokens"]["input"] == 0
    assert MODEL_TOKEN_AVERAGES["count-tokens"]["output"] == 0
