import pytest
import pandas as pd
import numpy as np


def make_df(n=50, trend="up"):
    """Create a price DataFrame for testing."""
    np.random.seed(42)
    if trend == "up":
        prices = 100.0 + np.cumsum(np.random.uniform(0.1, 1.0, n))
    elif trend == "down":
        prices = 200.0 - np.cumsum(np.random.uniform(0.1, 1.0, n))
    else:
        prices = 100.0 + np.random.normal(0, 1, n)

    volumes = np.random.uniform(800_000, 1_200_000, n)
    return pd.DataFrame({
        "close": prices,
        "volume": volumes,
        "open": prices * 0.99,
        "high": prices * 1.01,
        "low": prices * 0.98,
    })


class TestComputeIndicators:
    def test_returns_dict(self):
        from shark.data.technical import compute_indicators
        df = make_df(50)
        result = compute_indicators(df)
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        from shark.data.technical import compute_indicators
        df = make_df(50)
        result = compute_indicators(df)
        for key in ["sma_20", "sma_50", "rsi_14", "volume_ratio", "signals"]:
            assert key in result, f"Missing key: {key}"

    def test_sma_20_is_float(self):
        from shark.data.technical import compute_indicators
        df = make_df(50)
        result = compute_indicators(df)
        assert isinstance(result["sma_20"], float)

    def test_sma_50_present_with_enough_data(self):
        from shark.data.technical import compute_indicators
        df = make_df(60)
        result = compute_indicators(df)
        assert result["sma_50"] is not None
        assert isinstance(result["sma_50"], float)

    def test_sma_50_none_with_insufficient_data(self):
        from shark.data.technical import compute_indicators
        df = make_df(30)
        result = compute_indicators(df)
        assert result["sma_50"] is None

    def test_rsi_in_valid_range(self):
        from shark.data.technical import compute_indicators
        df = make_df(50)
        result = compute_indicators(df)
        assert 0.0 <= result["rsi_14"] <= 100.0

    def test_rsi_high_for_uptrend(self):
        from shark.data.technical import compute_indicators
        df = make_df(50, trend="up")
        result = compute_indicators(df)
        assert result["rsi_14"] > 50.0

    def test_rsi_low_for_downtrend(self):
        from shark.data.technical import compute_indicators
        df = make_df(50, trend="down")
        result = compute_indicators(df)
        assert result["rsi_14"] < 50.0

    def test_volume_ratio_is_positive(self):
        from shark.data.technical import compute_indicators
        df = make_df(50)
        result = compute_indicators(df)
        assert result["volume_ratio"] > 0.0

    def test_signals_is_dict(self):
        from shark.data.technical import compute_indicators
        df = make_df(50)
        result = compute_indicators(df)
        assert isinstance(result["signals"], dict)

    def test_raises_on_insufficient_data(self):
        from shark.data.technical import compute_indicators
        df = make_df(15)
        with pytest.raises(ValueError):
            compute_indicators(df)

    def test_current_price_present(self):
        from shark.data.technical import compute_indicators
        df = make_df(50)
        result = compute_indicators(df)
        assert "current_price" in result
        assert result["current_price"] == pytest.approx(df["close"].iloc[-1])

    def test_price_above_sma20_signal(self):
        from shark.data.technical import compute_indicators
        df = make_df(50, trend="up")
        result = compute_indicators(df)
        above = result["current_price"] > result["sma_20"]
        assert result["signals"].get("above_sma20") == above


class TestRSIWilderSmoothing:
    """Verify Wilder RSI smoothing properties."""

    def test_rsi_consistent_across_calls(self):
        from shark.data.technical import compute_indicators
        df = make_df(50)
        r1 = compute_indicators(df)
        r2 = compute_indicators(df)
        assert r1["rsi_14"] == r2["rsi_14"]

    def test_all_gains_rsi_near_100(self):
        from shark.data.technical import compute_indicators
        prices = [100.0 + i * 0.5 for i in range(50)]
        df = pd.DataFrame({
            "close": prices,
            "volume": [1_000_000] * 50,
        })
        result = compute_indicators(df)
        assert result["rsi_14"] > 85.0

    def test_all_losses_rsi_near_0(self):
        from shark.data.technical import compute_indicators
        prices = [200.0 - i * 0.5 for i in range(50)]
        df = pd.DataFrame({
            "close": prices,
            "volume": [1_000_000] * 50,
        })
        result = compute_indicators(df)
        assert result["rsi_14"] < 15.0
