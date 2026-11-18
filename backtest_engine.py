import os
import logging
import numpy as np
import pandas as pd
from psycopg2.extras import execute_values
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import root_mean_squared_error
from db_config import get_connection
import joblib
import Path
import warnings

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

MODEL_VERSION = "rf_walkforward_v1"
INITIAL_TRAIN_SIZE = 252
RETRAIN_STEP = 21
SLIPPAGE_BPS = 0.0005

FEATURE_COLS = [
    "daily_return",
    "return_lag_1",
    "return_lag_5",
    "sma_ratio",
    "price_to_sma20",
    "volatility_20d",
    "volume_change",
]

def ensure_feature_table(conn):
    """Creates a table to persist feature importance weights for dashboard explainability."""
    query = """
        CREATE TABLE IF NOT EXISTS model_feature_importance (
            importance_id BIGSERIAL PRIMARY KEY,
            ticker_id INT NOT NULL REFERENCES tickers(ticker_id) ON DELETE CASCADE,
            model_version VARCHAR(40) NOT NULL,
            feature_name VARCHAR(60) NOT NULL,
            importance_weight NUMERIC(8, 6) NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_ticker_feature UNIQUE (ticker_id, model_version, feature_name)
        );
    """
    with conn.cursor() as cur:
        cur.execute(query)
    conn.commit()


def fetch_analytics_data(conn, symbol: str) -> pd.DataFrame:
    """Queries the pre-aggregated SQL view for a specific ticker."""
    query = """
        SELECT 
            ticker_id, symbol, trade_date, close_price, adj_close,
            volume, daily_return, sma_20, sma_50, volatility_20d
        FROM vw_ticker_analytics
        WHERE symbol = %s
        ORDER BY trade_date ASC;
    """
    df = pd.read_sql_query(query, conn, params=(symbol,))
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Builds strictly lagged technical features and next-day targets without look-ahead bias."""
    feat_df = df.copy()
    numeric_cols = ["close_price", "adj_close", "volume", "daily_return", "sma_20", "sma_50", "volatility_20d"]
    for col in numeric_cols:
        feat_df[col] = pd.to_numeric(feat_df[col], errors="coerce")

    feat_df["return_lag_1"] = feat_df["daily_return"].shift(1)
    feat_df["return_lag_5"] = feat_df["adj_close"].pct_change(5)
    feat_df["sma_ratio"] = feat_df["sma_20"] / feat_df["sma_50"]
    feat_df["price_to_sma20"] = feat_df["adj_close"] / feat_df["sma_20"]
    feat_df["volume_change"] = feat_df["volume"].pct_change(1).replace([np.inf, -np.inf], 0)

    # Target: Next trading day's return and adjusted close
    feat_df["target_next_return"] = feat_df["daily_return"].shift(-1)
    feat_df["actual_next_close"] = feat_df["adj_close"].shift(-1)
    feat_df["target_date"] = feat_df["trade_date"].shift(-1)

    feat_df = feat_df.dropna(subset=FEATURE_COLS + ["target_next_return", "actual_next_close"]).reset_index(drop=True)
    return feat_df


def calculate_risk_metrics(results_df: pd.DataFrame) -> pd.DataFrame:
    """Calculates strategy returns net of slippage, rolling RMSE, Sharpe Ratio, and Max Drawdown."""
    df = results_df.copy()

    # Signal: 1 (Long) if predicted return > slippage threshold, else 0 (Cash)
    df["signal"] = np.where(df["pred_return"] > SLIPPAGE_BPS, 1.0, 0.0)
    df["trade_executed"] = df["signal"].diff().abs().fillna(df["signal"])
    df["strategy_net_return"] = (df["signal"] * df["actual_return"]) - (df["trade_executed"] * SLIPPAGE_BPS)

    # Cumulative equity curve and running drawdown
    df["cum_equity"] = (1.0 + df["strategy_net_return"]).cumprod()
    df["running_peak"] = df["cum_equity"].cummax()
    df["drawdown"] = (df["cum_equity"] - df["running_peak"]) / df["running_peak"]

    # Rolling 20-day RMSE on price predictions
    squared_errors = (df["actual_close"] - df["predicted_close"]) ** 2
    df["rolling_rmse"] = np.sqrt(squared_errors.rolling(window=20, min_periods=1).mean())

    # Overall annualized Sharpe Ratio and Maximum Drawdown
    ret_std = df["strategy_net_return"].std()
    sharpe = (df["strategy_net_return"].mean() / ret_std) * np.sqrt(252) if ret_std > 0 else 0.0
    max_dd = df["drawdown"].min()

    df["sharpe_ratio"] = round(float(sharpe), 4)
    df["max_drawdown"] = round(float(max_dd), 4)
    return df


def run_walk_forward_backtest(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, RandomForestRegressor]:
    """Executes an expanding-window walk-forward training and prediction loop."""
    if len(df) <= INITIAL_TRAIN_SIZE:
        raise ValueError(f"Insufficient rows ({len(df)}) for initial training window ({INITIAL_TRAIN_SIZE}).")

    predictions = []
    latest_importances = {}
    model = None

    for start_idx in range(INITIAL_TRAIN_SIZE, len(df), RETRAIN_STEP):
        end_idx = min(start_idx + RETRAIN_STEP, len(df))

        train_slice = df.iloc[:start_idx]
        test_slice = df.iloc[start_idx:end_idx]

        X_train = train_slice[FEATURE_COLS]
        y_train = train_slice["target_next_return"]
        X_test = test_slice[FEATURE_COLS]

        model = RandomForestRegressor(
            n_estimators=150,
            max_depth=6,
            min_samples_leaf=5,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_train, y_train)

        pred_returns = model.predict(X_test)
        latest_importances = dict(zip(FEATURE_COLS, model.feature_importances_))

        for i, (_, row) in enumerate(test_slice.iterrows()):
            pred_ret = float(pred_returns[i])
            pred_close = float(row["adj_close"]) * (1.0 + pred_ret)
            predictions.append({
                "ticker_id": int(row["ticker_id"]),
                "target_date": row["target_date"],
                "pred_return": pred_ret,
                "actual_return": float(row["target_next_return"]),
                "predicted_close": round(pred_close, 4),
                "actual_close": round(float(row["actual_next_close"]), 4),
            })

    results_df = pd.DataFrame(predictions)
    evaluated_df = calculate_risk_metrics(results_df)
    return evaluated_df, latest_importances, model


def persist_backtest_results(conn, evaluated_df: pd.DataFrame, ticker_id: int, importances: dict):
    """Upserts walk-forward predictions, risk metrics, and feature importances into PostgreSQL."""
    pred_records = [
        (
            int(row["ticker_id"]),
            row["target_date"],
            MODEL_VERSION,
            round(float(row["predicted_close"]), 4),
            round(float(row["actual_close"]), 4),
            round(float(row["rolling_rmse"]), 4),
            round(float(row["sharpe_ratio"]), 4),
            round(float(row["max_drawdown"]), 4),
        )
        for _, row in evaluated_df.iterrows()
    ]

    pred_upsert_sql = """
        INSERT INTO model_predictions (
            ticker_id, target_date, model_version, predicted_close,
            actual_close, rolling_rmse, sharpe_ratio, max_drawdown
        ) VALUES %s
        ON CONFLICT (ticker_id, target_date, model_version) DO UPDATE SET
            predicted_close = EXCLUDED.predicted_close,
            actual_close = EXCLUDED.actual_close,
            rolling_rmse = EXCLUDED.rolling_rmse,
            sharpe_ratio = EXCLUDED.sharpe_ratio,
            max_drawdown = EXCLUDED.max_drawdown,
            created_at = CURRENT_TIMESTAMP;
    """

    imp_records = [
        (ticker_id, MODEL_VERSION, feat, round(float(weight), 6))
        for feat, weight in importances.items()
    ]

    imp_upsert_sql = """
        INSERT INTO model_feature_importance (
            ticker_id, model_version, feature_name, importance_weight
        ) VALUES %s
        ON CONFLICT (ticker_id, model_version, feature_name) DO UPDATE SET
            importance_weight = EXCLUDED.importance_weight,
            updated_at = CURRENT_TIMESTAMP;
    """

    with conn.cursor() as cur:
        execute_values(cur, pred_upsert_sql, pred_records, page_size=1000)
        execute_values(cur, imp_upsert_sql, imp_records)
    conn.commit()


def main():
    conn = get_connection()
    ensure_feature_table(conn)
    symbols = ["SPY", "AAPL", "MSFT", "LMT", "GD"]

    try:
        for symbol in symbols:
            logging.info(f"Running walk-forward backtest for {symbol}...")
            raw_df = fetch_analytics_data(conn, symbol)
            feat_df = engineer_features(raw_df)

            # Unpack the returned model
            evaluated_df, importances, model = run_walk_forward_backtest(feat_df)
            ticker_id = int(evaluated_df["ticker_id"].iloc[0])
            persist_backtest_results(conn, evaluated_df, ticker_id, importances)

            # <-- Save the model to disk
            model_filename = f"models/{symbol}_rf.joblib"

            # Make the directory if it doesn't exist
            Path(model_filename).parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(model, model_filename)
            logging.info(f"Saved {symbol} model to {model_filename}")

            sharpe = evaluated_df["sharpe_ratio"].iloc[-1]
            max_dd = evaluated_df["max_drawdown"].iloc[-1]
            rmse = root_mean_squared_error(evaluated_df["actual_close"], evaluated_df["predicted_close"])
            logging.info(
                f"[{symbol}] Completed {len(evaluated_df)} out-of-sample days | "
                f"RMSE: ${rmse:.2f} | Sharpe: {sharpe:.2f} | Max Drawdown: {max_dd * 100:.2f}%"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()