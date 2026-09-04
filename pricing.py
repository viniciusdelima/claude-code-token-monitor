import json
import re
from pathlib import Path

MANAGED_SETTINGS_PATH = Path("/etc/claude-code/managed-settings.json")

# Applies to responses processed under US-only data residency
# (usage.inference_geo == "us"): 1.1x on every token pricing category.
DATA_RESIDENCY_MULTIPLIER = 1.1

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


def load_managed_pricing(path=None):
    path = path or MANAGED_SETTINGS_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return data.get("modelPricing")


def _resolve_prices(model, managed_pricing):
    # Real Claude Code logs record dated model ids (e.g.
    # "claude-haiku-4-5-20251001"), but the pricing table is keyed on the
    # undated id. Try the exact model first, then fall back to the
    # date-stripped form.
    normalized = re.sub(r"-\d{8}$", "", model) if isinstance(model, str) else model

    overrides = (managed_pricing or {}).get("overrides") or {}
    override = overrides.get(model) or overrides.get(normalized)
    if override is not None:
        prices = {
            "input": override["input"],
            "output": override["output"],
            "cache_write_5m": override["cacheWrite"],
            "cache_read": override["cacheRead"],
        }
    else:
        prices = MODEL_PRICING.get(model) or MODEL_PRICING.get(normalized)
        if prices is None:
            return None
        prices = dict(prices)

    multiplier = (managed_pricing or {}).get("multiplier")
    if multiplier is not None:
        prices = {k: v * multiplier for k, v in prices.items()}
    return prices


def estimate_cost_usd(
    model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
    inference_geo=None, managed_settings_path=None,
):
    if not (input_tokens or output_tokens or cache_creation_tokens or cache_read_tokens):
        # A model contributing zero tokens shouldn't blank out a whole bucket
        # as "unknown cost", even if the model itself is unrecognized.
        return 0.0

    managed_pricing = load_managed_pricing(managed_settings_path)
    prices = _resolve_prices(model, managed_pricing)
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
    if inference_geo == "us":
        total *= DATA_RESIDENCY_MULTIPLIER
    return total / 1_000_000
