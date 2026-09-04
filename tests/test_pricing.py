import json

import pricing


def test_known_model_cost_matches_manual_calculation():
    cost = pricing.estimate_cost_usd(
        model="claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    # 2.00 (input) + 10.00 (output) + 2.50 (cache write 5m) + 0.20 (cache read)
    assert cost == 14.70


def test_zero_tokens_cost_zero():
    cost = pricing.estimate_cost_usd(
        model="claude-opus-5",
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cost == 0.0


def test_unknown_model_returns_none():
    cost = pricing.estimate_cost_usd(
        model="some-future-model",
        input_tokens=100,
        output_tokens=100,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cost is None


def test_dated_model_id_resolves_to_same_cost_as_undated():
    dated = pricing.estimate_cost_usd(
        model="claude-haiku-4-5-20251001",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    undated = pricing.estimate_cost_usd(
        model="claude-haiku-4-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert dated is not None
    assert dated == undated


def test_unrecognized_model_with_zero_tokens_returns_zero_not_none():
    cost = pricing.estimate_cost_usd(
        model="some-future-model",
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cost == 0.0


def test_inference_geo_us_applies_data_residency_multiplier():
    base = pricing.estimate_cost_usd(
        model="claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    us = pricing.estimate_cost_usd(
        model="claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        inference_geo="us",
    )
    assert us == round(base * 1.1, 10)


def test_inference_geo_other_values_use_standard_pricing():
    base = pricing.estimate_cost_usd(
        model="claude-sonnet-5", input_tokens=1000, output_tokens=0,
        cache_creation_tokens=0, cache_read_tokens=0,
    )
    for geo in (None, "not_available", "global"):
        assert pricing.estimate_cost_usd(
            model="claude-sonnet-5", input_tokens=1000, output_tokens=0,
            cache_creation_tokens=0, cache_read_tokens=0, inference_geo=geo,
        ) == base


def test_managed_pricing_override_replaces_model_rate(tmp_path):
    settings_path = tmp_path / "managed-settings.json"
    settings_path.write_text(json.dumps({
        "modelPricing": {
            "overrides": {
                "claude-sonnet-4-6": {"input": 2.4, "output": 12, "cacheRead": 0.24, "cacheWrite": 3},
            }
        }
    }))

    cost = pricing.estimate_cost_usd(
        model="claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000,
        cache_creation_tokens=1_000_000, cache_read_tokens=1_000_000,
        managed_settings_path=settings_path,
    )

    assert cost == 17.64


def test_managed_pricing_multiplier_discounts_every_rate(tmp_path):
    settings_path = tmp_path / "managed-settings.json"
    settings_path.write_text(json.dumps({"modelPricing": {"multiplier": 0.85}}))

    cost = pricing.estimate_cost_usd(
        model="claude-sonnet-5", input_tokens=1_000_000, output_tokens=0,
        cache_creation_tokens=0, cache_read_tokens=0,
        managed_settings_path=settings_path,
    )

    assert cost == round(2.00 * 0.85, 10)


def test_managed_pricing_multiplier_applies_on_top_of_override(tmp_path):
    settings_path = tmp_path / "managed-settings.json"
    settings_path.write_text(json.dumps({
        "modelPricing": {
            "multiplier": 0.85,
            "overrides": {
                "claude-sonnet-4-6": {"input": 2.4, "output": 12, "cacheRead": 0.24, "cacheWrite": 3},
            },
        }
    }))

    cost = pricing.estimate_cost_usd(
        model="claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=0,
        cache_creation_tokens=0, cache_read_tokens=0,
        managed_settings_path=settings_path,
    )

    assert cost == round(2.4 * 0.85, 10)


def test_missing_managed_settings_file_falls_back_to_static_table(tmp_path):
    cost = pricing.estimate_cost_usd(
        model="claude-sonnet-5", input_tokens=1_000_000, output_tokens=0,
        cache_creation_tokens=0, cache_read_tokens=0,
        managed_settings_path=tmp_path / "does-not-exist.json",
    )
    assert cost == 2.00


def test_invalid_managed_settings_json_falls_back_to_static_table(tmp_path):
    settings_path = tmp_path / "managed-settings.json"
    settings_path.write_text("{not valid json")

    cost = pricing.estimate_cost_usd(
        model="claude-sonnet-5", input_tokens=1_000_000, output_tokens=0,
        cache_creation_tokens=0, cache_read_tokens=0,
        managed_settings_path=settings_path,
    )
    assert cost == 2.00
