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

    def test_fable_5_not_billed(self):
        """fable-5/opus-4-7: no GCP billing SKU as of Jun 2026 -> priced at 0."""
        for model in ("claude-fable-5", "claude-opus-4-7"):
            pricing = get_pricing_for_model(model)
            assert pricing["input"] == 0.00
            assert pricing["output"] == 0.00


class TestGetTokenAveragesForModel:
    def test_sonnet_calibrated(self):
        avgs = get_token_averages_for_model("claude-sonnet-4-5")
        assert avgs["input"] == 14089
        assert avgs["output"] == 641
        assert avgs["cache_write"] == 12218
        assert avgs["cache_read"] == 67746

    def test_haiku_calibrated(self):
        avgs = get_token_averages_for_model("claude-3-5-haiku")
        assert avgs["input"] == 382

    def test_unknown_returns_default(self):
        avgs = get_token_averages_for_model("unknown-model")
        assert avgs["input"] == 3000
        assert avgs["output"] == 1200

    def test_user_email_ignored(self):
        """Calibration is uniform per model: user_email must not change averages."""
        base = get_token_averages_for_model("claude-opus-4-6")
        for email in (
            "automation@example.com",
            "anyone@example.com",
            None,
        ):
            assert (
                get_token_averages_for_model("claude-opus-4-6", user_email=email)
                == base
            )


class TestEstimateCost:
    def test_zero_requests(self):
        result = estimate_cost(0, "claude-sonnet-4-5")
        assert result["cost_usd"] == 0
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0

    def test_calibrated_sonnet(self):
        result = estimate_cost(100, "claude-sonnet-4-5", use_calibrated=True)
        # input:  100 * 14089 = 1408900 -> /1M * $3.00  = $4.2267
        # output: 100 * 641   = 64100   -> /1M * $15.00 = $0.9615
        # write:  100 * 12218 = 1221800 -> /1M * $3.75  = $4.5818
        # read:   100 * 67746 = 6774600 -> /1M * $0.30  = $2.0324
        assert result["input_tokens"] == 1408900
        assert result["output_tokens"] == 64100
        assert result["cache_write_tokens"] == 1221800
        assert result["cache_read_tokens"] == 6774600
        assert result["cost_usd"] == pytest.approx(11.8023, abs=0.001)

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

    def test_cost_independent_of_user(self):
        """Uniform calibration: cost depends only on count and model, not user."""
        a_cost = estimate_cost(
            100, "claude-opus-4-6", user_email="automation@example.com"
        )["cost_usd"]
        b_cost = estimate_cost(100, "claude-opus-4-6", user_email="person@example.com")[
            "cost_usd"
        ]
        assert a_cost == b_cost

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
        assert result["input_tokens"] == 1408900
        assert result["output_tokens"] == 64100
        assert result["cache_write_tokens"] == 1221800  # calibrated, not overridden
        assert result["cache_read_tokens"] == 1000000  # overridden
        # 4.2267 + 0.9615 + 4.5818 + 1M/1M * $0.30 = 10.0700
        assert result["cost_usd"] == pytest.approx(10.0700, abs=0.001)
