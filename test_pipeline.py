import numpy as np
import pandas as pd
import pytest
from etl_ingest import validate_and_filter_prices
from backtest_engine import engineer_features, calculate_risk_metrics


def test_validate_and_filter_prices_drops_corrupt_rows():
    """Verifies that NULLs, negative prices, High < Low violations, and duplicate dates are filtered."""
    dates = pd.to_datetime([
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-07",
    ])
    raw_data = pd.DataFrame(
        {
            "Open": [100.0, -10.0, 105.0, np.nan, 108.0],
            "High": [105.0, 102.0, 100.0, 110.0, 112.0],
            "Low": [99.0, 98.0, 104.0, 105.0, 107.0],
            "Close": [104.0, 101.0, 102.0, 109.0, 111.0],
            "Adj Close": [104.0, 101.0, 102.0, 109.0, 111.0],
            "Volume": [10000, 12000, 15000, 11000, 14000],
        },
        index=dates,
    )

    clean_df, dropped_count = validate_and_filter_prices(raw_data)

    assert len(clean_df) == 2
    assert dropped_count == 3
    assert (clean_df["High"] >= clean_df["Low"]).all()
    assert (clean_df["Open"] > 0).all()


def test_engineer_features_prevents_lookahead_bias():
    """Ensures altering future market prices never mutates historical feature rows."""
    n_days = 70
    dates = pd.bdate_range("2025-01-01", periods=n_days)
    base_prices = np.linspace(100.0, 135.0, n_days)

    def make_mock_view(prices):
        s = pd.Series(prices)
        ret = s.pct_change().fillna(0.0)
        return pd.DataFrame({
            "ticker_id": 1,
            "symbol": "TEST",
            "trade_date": dates,
            "close_price": s,
            "adj_close": s,
            "volume": np.full(n_days, 500000),
            "daily_return": ret,
            "sma_20": s.rolling(20, min_periods=1).mean(),
            "sma_50": s.rolling(50, min_periods=1).mean(),
            "volatility_20d": ret.rolling(20, min_periods=2).std().fillna(0.01),
        })

    df_original = engineer_features(make_mock_view(base_prices))

    shocked_prices = base_prices.copy()
    shocked_prices[-5:] = shocked_prices[-5:] * 3.0
    df_shocked = engineer_features(make_mock_view(shocked_prices))

    cutoff = len(df_original) - 6
    feature_cols = [
        "daily_return", "return_lag_1", "return_lag_5",
        "sma_ratio", "price_to_sma20", "volatility_20d", "volume_change",
    ]
    pd.testing.assert_frame_equal(
        df_original.iloc[:cutoff][feature_cols],
        df_shocked.iloc[:cutoff][feature_cols],
    )


def test_calculate_risk_metrics_slippage_and_drawdown_bounds():
    """Verifies that transaction costs reduce net returns and drawdown stays non-positive."""
    mock_predictions = pd.DataFrame({
        "ticker_id": [1, 1, 1, 1],
        "target_date": pd.to_datetime(["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05"]),
        "pred_return": [0.012, 0.008, -0.005, 0.015],
        "actual_return": [0.010, -0.020, -0.010, 0.012],
        "predicted_close": [101.2, 101.8, 98.5, 101.5],
        "actual_close": [101.0, 99.0, 98.0, 99.2],
    })

    evaluated = calculate_risk_metrics(mock_predictions)

    assert (evaluated["drawdown"] <= 0.0).all()
    assert evaluated["max_drawdown"].iloc[-1] < 0.0
    assert "rolling_rmse" in evaluated.columns
    assert evaluated["strategy_net_return"].iloc[0] < evaluated["actual_return"].iloc[0]