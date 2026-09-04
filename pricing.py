MODEL_PRICING = {
    "claude-opus-5": {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-opus-4-8": {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-opus-4-7": {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-opus-4-6": {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-sonnet-5": {"input": 2.00, "cache_write_5m": 2.50, "cache_write_1h": 4.00, "cache_read": 0.20, "output": 10.00},
    "claude-sonnet-4-6": {"input": 3.00, "cache_write_5m": 3.75, "cache_write_1h": 6.00, "cache_read": 0.30, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "cache_write_5m": 1.25, "cache_write_1h": 2.00, "cache_read": 0.10, "output": 5.00},
    "claude-fable-5-1": {"input": 10.00, "cache_write_5m": 12.50, "cache_write_1h": 20.00, "cache_read": 0.25, "output": 50.00},
    "claude-fable-5": {"input": 10.00, "cache_write_5m": 12.50, "cache_write_1h": 20.00, "cache_read": 1.00, "output": 50.00},
}


def estimate_cost_usd(model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens):
    prices = MODEL_PRICING.get(model)
    if prices is None:
        return None
    # cache_creation_tokens isn't split by TTL in usage_events, so this
    # approximates every cache write at the 5-minute rate (the common case).
    total = (
        input_tokens * prices["input"]
        + output_tokens * prices["output"]
        + cache_creation_tokens * prices["cache_write_5m"]
        + cache_read_tokens * prices["cache_read"]
    )
    return total / 1_000_000
