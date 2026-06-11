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

    def test_cache_rates(self):
        pricing = get_pricing_for_model("claude-sonnet-4-6")
        assert pricing["cache_write"] == 3.75
        assert pricing["cache_read"] == 0.30

    def test_fable_5(self):
        pricing = get_pricing_for_model("claude-fable-5")
        assert pricing["input"] == 10.00
        assert pricing["output"] == 50.00
        assert pricing["cache_write"] == 12.50
        assert pricing["cache_read"] == 1.00


class TestGetTokenAveragesForModel:
    def test_sonnet_calibrated(self):
        avgs = get_token_averages_for_model("claude-sonnet-4-5")
        assert avgs["input"] == 18569
        assert avgs["output"] == 666
        assert avgs["cache_write"] == 11251
        assert avgs["cache_read"] == 67044

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
        # input:  100 * 18569 = 1856900 -> /1M * $3.00  = $5.5707
        # output: 100 * 666   = 66600   -> /1M * $15.00 = $0.9990
        # write:  100 * 11251 = 1125100 -> /1M * $3.75  = $4.2191
        # read:   100 * 67044 = 6704400 -> /1M * $0.30  = $2.0113
        assert result["input_tokens"] == 1856900
        assert result["output_tokens"] == 66600
        assert result["cache_write_tokens"] == 1125100
        assert result["cache_read_tokens"] == 6704400
        assert result["cost_usd"] == pytest.approx(12.8001, abs=0.001)

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

    def test_cache_tokens_default_to_zero_without_calibration(self):
        """Without calibration nor overrides, cache tokens default to 0."""
        result = estimate_cost(100, "claude-sonnet-4-5", use_calibrated=False)
        assert result["cache_write_tokens"] == 0
        assert result["cache_read_tokens"] == 0

    def test_cache_tokens_override(self):
        result = estimate_cost(
            10,
            "claude-sonnet-4-6",
            avg_input=1000,
            avg_output=500,
            avg_cache_write=2000,
            avg_cache_read=8000,
        )
        assert result["cache_write_tokens"] == 20000
        assert result["cache_read_tokens"] == 80000
        # input:  10000/1M * $3.00  = $0.03
        # output:  5000/1M * $15.00 = $0.075
        # write:  20000/1M * $3.75  = $0.075
        # read:   80000/1M * $0.30  = $0.024
        assert result["cost_usd"] == pytest.approx(0.204, abs=0.001)

    def test_cache_override_with_calibrated_input_output(self):
        """Cache overrides compose with calibrated input/output averages."""
        result = estimate_cost(
            100, "claude-sonnet-4-5", avg_cache_read=10000, use_calibrated=True
        )
        assert result["input_tokens"] == 1856900
        assert result["output_tokens"] == 66600
        assert result["cache_write_tokens"] == 1125100  # calibrated, not overridden
        assert result["cache_read_tokens"] == 1000000  # overridden
        # 5.5707 + 0.999 + 4.2191 + 1M/1M * $0.30 = 11.0888
        assert result["cost_usd"] == pytest.approx(11.0888, abs=0.001)
