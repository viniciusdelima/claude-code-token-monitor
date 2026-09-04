import insights


def test_no_anomaly_with_less_than_two_points():
    assert insights.detect_latest_anomaly([("2026-09-01", 100)]) is None
    assert insights.detect_latest_anomaly([]) is None


def test_fixed_rule_flags_spike_with_short_history():
    series = [
        ("2026-09-01", 100),
        ("2026-09-02", 110),
        ("2026-09-03", 105),
        ("2026-09-04", 300),  # latest, > 1.5x mean of history (105)
    ]
    result = insights.detect_latest_anomaly(series)
    assert result is not None
    assert result["method"] == "fixed_rule"
    assert result["day"] == "2026-09-04"


def test_fixed_rule_does_not_flag_normal_day():
    series = [
        ("2026-09-01", 100),
        ("2026-09-02", 110),
        ("2026-09-03", 105),
        ("2026-09-04", 108),
    ]
    assert insights.detect_latest_anomaly(series) is None


def test_zscore_flags_spike_with_long_history():
    history = [(f"2026-08-{d:02d}", 100) for d in range(1, 8)]  # 7 days, all 100
    series = history + [("2026-09-01", 500)]  # far above mean/stdev=0 case avoided by variance below
    # Introduce slight variance so stdev isn't zero, then a real spike:
    history = [("2026-08-01", 95), ("2026-08-02", 105), ("2026-08-03", 98),
               ("2026-08-04", 102), ("2026-08-05", 97), ("2026-08-06", 103),
               ("2026-08-07", 100)]
    series = history + [("2026-09-01", 500)]

    result = insights.detect_latest_anomaly(series)

    assert result is not None
    assert result["method"] == "zscore"
    assert result["day"] == "2026-09-01"


def test_zscore_does_not_flag_typical_day_with_long_history():
    history = [("2026-08-01", 95), ("2026-08-02", 105), ("2026-08-03", 98),
               ("2026-08-04", 102), ("2026-08-05", 97), ("2026-08-06", 103),
               ("2026-08-07", 100)]
    series = history + [("2026-09-01", 101)]

    assert insights.detect_latest_anomaly(series) is None


def test_zero_variance_history_never_flags():
    history = [("2026-08-0" + str(d), 100) for d in range(1, 8)]
    series = history + [("2026-09-01", 100)]

    assert insights.detect_latest_anomaly(series) is None
