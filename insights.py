import statistics


def detect_latest_anomaly(series, z_threshold=2.0, fixed_multiplier=1.5):
    if len(series) < 2:
        return None

    *history, latest = series
    latest_day, latest_value = latest
    history_values = [v for _, v in history]
    mean = statistics.mean(history_values)

    if len(history_values) >= 7:
        stdev = statistics.stdev(history_values)
        if stdev == 0:
            return None
        z = (latest_value - mean) / stdev
        if abs(z) > z_threshold:
            return {"day": latest_day, "value": latest_value, "mean": mean, "method": "zscore", "score": z}
        return None

    if mean > 0 and latest_value > mean * fixed_multiplier:
        return {"day": latest_day, "value": latest_value, "mean": mean, "method": "fixed_rule", "score": latest_value / mean}
    return None
