"""Tests for cost estimation."""

import pytest

from vertex_claude_exporter.cost import (
    estimate_cost,
    get_pricing_for_model,
    get_token_averages_for_model,
)


class TestGetPricingForModel:
    def test_exact_match(self):
        pricing = get_pricing_for_model("claude-sonnet-4-5")
        assert pricing["input"] == 3.00
        assert pricing["output"] == 15.00

    def test_substring_match(self):
        pricing = get_pricing_for_model("claude-sonnet-4-5@20260101")
        assert pricing["input"] == 3.00

    def test_longest_key_wins(self):
        """claude-opus-4-5 should match before claude-opus-4."""
        pricing = get_pricing_for_model("claude-opus-4-5")
        assert pricing["input"] == 5.00
        assert pricing["output"] == 25.00

    def test_opus_4_matches_correctly(self):
        pricing = get_pricing_for_model("claude-opus-4")
        assert pricing["input"] == 15.00

    def test_unknown_model_returns_default(self):
        pricing = get_pricing_for_model("some-unknown-model")
        assert pricing["input"] == 3.00
        assert pricing["output"] == 15.00

    def test_case_insensitive(self):
        pricing = get_pricing_for_model("Claude-Haiku-4-5")
        assert pricing["input"] == 1.00

    def test_count_tokens(self):
        pricing = get_pricing_for_model("count-tokens")
        assert pricing["input"] == 0.00
        assert pricing["output"] == 0.00


class TestGetTokenAveragesForModel:
    def test_sonnet_calibrated(self):
        avgs = get_token_averages_for_model("claude-sonnet-4-5")
        assert avgs["input"] == 4820
        assert avgs["output"] == 1928

    def test_haiku_calibrated(self):
        avgs = get_token_averages_for_model("claude-3-5-haiku")
        assert avgs["input"] == 382

    def test_unknown_returns_default(self):
        avgs = get_token_averages_for_model("unknown-model")
        assert avgs["input"] == 3000
        assert avgs["output"] == 1200


class TestEstimateCost:
    def test_zero_requests(self):
        result = estimate_cost(0, "claude-sonnet-4-5")
        assert result["cost_usd"] == 0
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0

    def test_calibrated_sonnet(self):
        result = estimate_cost(100, "claude-sonnet-4-5", use_calibrated=True)
        # 100 * 4820 = 482000 input tokens -> 482000/1M * $3 = $1.446
        # 100 * 1928 = 192800 output tokens -> 192800/1M * $15 = $2.892
        assert result["input_tokens"] == 482000
        assert result["output_tokens"] == 192800
        assert result["cost_usd"] == pytest.approx(4.338, abs=0.001)

    def test_override_tokens(self):
        result = estimate_cost(10, "claude-sonnet-4-5", avg_input=1000, avg_output=500)
        assert result["input_tokens"] == 10000
        assert result["output_tokens"] == 5000
        # 10000/1M * $3 + 5000/1M * $15 = $0.03 + $0.075 = $0.105
        assert result["cost_usd"] == pytest.approx(0.105, abs=0.001)

    def test_no_calibrated_uses_defaults(self):
        result = estimate_cost(10, "claude-sonnet-4-5", use_calibrated=False)
        assert result["input_tokens"] == 30000  # 10 * 3000
        assert result["output_tokens"] == 12000  # 10 * 1200

    def test_count_tokens_is_free(self):
        result = estimate_cost(1000, "count-tokens")
        assert result["cost_usd"] == 0
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
