"""Tests for config constants."""

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
