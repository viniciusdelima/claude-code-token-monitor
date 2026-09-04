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
