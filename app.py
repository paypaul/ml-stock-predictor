import os
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from db_config import get_connection
import warnings

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

st.set_page_config(
    page_title="Enterprise Equity Forecasting & Risk Portal",
    layout="wide",
    initial_sidebar_state="expanded",
)

MODEL_VERSION = "rf_walkforward_v1"

FEATURE_LABELS = {
    "daily_return": "Prior-Day Return Momentum",
    "return_lag_1": "Two-Day Lagged Return",
    "return_lag_5": "Five-Day Weekly Trend",
    "sma_ratio": "20-Day / 50-Day Moving Average Ratio",
    "price_to_sma20": "Price Distance from 20-Day Mean",
    "volatility_20d": "20-Day Realized Volatility",
    "volume_change": "Daily Trading Volume Surge",
}


@st.cache_data(ttl=300)
def load_active_tickers() -> pd.DataFrame:
    query = """
        SELECT symbol, company_name, sector, exchange
        FROM tickers
        WHERE is_active = TRUE
        ORDER BY symbol ASC;
    """
    with get_connection() as conn:
        return pd.read_sql_query(query, conn)


@st.cache_data(ttl=300)
def load_prediction_report(symbol: str, model_version: str) -> pd.DataFrame:
    """
    Applies documented join keys across model_predictions, tickers, and
    vw_ticker_analytics to align strategy forecasts with SPY benchmark returns.
    """
    query = """
        WITH aligned_predictions AS (
            SELECT 
                p.target_date,
                p.predicted_close,
                p.actual_close,
                LAG(p.actual_close, 1) OVER (ORDER BY p.target_date) AS prev_actual_close,
                p.rolling_rmse,
                p.sharpe_ratio,
                p.max_drawdown,
                v.sma_20,
                v.sma_50,
                v.volatility_20d,
                v.daily_return AS asset_daily_return,
                spy.daily_return AS spy_daily_return
            FROM model_predictions p
            INNER JOIN tickers t 
                ON p.ticker_id = t.ticker_id
            LEFT JOIN vw_ticker_analytics v 
                ON p.ticker_id = v.ticker_id AND p.target_date = v.trade_date
            LEFT JOIN (
                SELECT trade_date, daily_return
                FROM vw_ticker_analytics
                WHERE symbol = 'SPY'
            ) spy 
                ON p.target_date = spy.trade_date
            WHERE t.symbol = %s
              AND p.model_version = %s
        )
        SELECT *
        FROM aligned_predictions
        WHERE prev_actual_close IS NOT NULL
        ORDER BY target_date ASC;
    """
    with get_connection() as conn:
        df = pd.read_sql_query(query, conn, params=(symbol, model_version))
    df["target_date"] = pd.to_datetime(df["target_date"])
    return df


@st.cache_data(ttl=300)
def load_feature_importances(symbol: str, model_version: str) -> pd.DataFrame:
    query = """
        SELECT f.feature_name, f.importance_weight, f.updated_at
        FROM model_feature_importance f
        INNER JOIN tickers t ON f.ticker_id = t.ticker_id
        WHERE t.symbol = %s AND f.model_version = %s
        ORDER BY f.importance_weight ASC;
    """
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=(symbol, model_version))


@st.cache_data(ttl=120)
def load_etl_health_log() -> pd.DataFrame:
    query = """
        SELECT symbol, records_fetched, records_dropped, records_upserted,
               status, error_message, executed_at
        FROM etl_audit_log
        ORDER BY executed_at DESC
        LIMIT 25;
    """
    with get_connection() as conn:
        return pd.read_sql_query(query, conn)


def compute_dynamic_performance(df: pd.DataFrame, slippage_bps: float) -> pd.DataFrame:
    """Computes equity curves, benchmark comparisons, and win rates for the selected window."""
    calc = df.copy()
    slippage_dec = slippage_bps / 10000.0

    calc["pred_return"] = (calc["predicted_close"] - calc["prev_actual_close"]) / calc["prev_actual_close"]
    calc["actual_return"] = (calc["actual_close"] - calc["prev_actual_close"]) / calc["prev_actual_close"]
    calc["spy_daily_return"] = calc["spy_daily_return"].fillna(0.0)

    calc["signal"] = np.where(calc["pred_return"] > slippage_dec, 1.0, 0.0)
    calc["trade_turnover"] = calc["signal"].diff().abs().fillna(calc["signal"])
    calc["strategy_net_return"] = (calc["signal"] * calc["actual_return"]) - (calc["trade_turnover"] * slippage_dec)

    calc["strategy_equity"] = (1.0 + calc["strategy_net_return"]).cumprod()
    calc["buy_hold_equity"] = (1.0 + calc["actual_return"]).cumprod()
    calc["spy_equity"] = (1.0 + calc["spy_daily_return"]).cumprod()

    running_peak = calc["strategy_equity"].cummax()
    calc["strategy_drawdown"] = (calc["strategy_equity"] - running_peak) / running_peak
    calc["directional_hit"] = np.sign(calc["pred_return"]) == np.sign(calc["actual_return"])
    return calc


def main():
    st.title("Enterprise Equity Analytics & Walk-Forward Forecasting Portal")
    st.caption(
        "Out-of-sample walk-forward validation, benchmark risk telemetry, and SQL pipeline monitoring."
    )

    tickers_df = load_active_tickers()
    if tickers_df.empty:
        st.error("No active tickers found in PostgreSQL. Run etl_ingest.py and backtest_engine.py first.")
        return

    with st.sidebar:
        st.subheader("Report Parameters")
        selected_symbol = st.selectbox(
            "Select Ticker Symbol",
            options=tickers_df["symbol"].tolist(),
            index=0,
        )
        ticker_meta = tickers_df[tickers_df["symbol"] == selected_symbol].iloc[0]
        st.markdown(f"**Company:** {ticker_meta['company_name']}")
        st.markdown(f"**Sector:** {ticker_meta['sector']} ({ticker_meta['exchange']})")

        raw_report_df = load_prediction_report(selected_symbol, MODEL_VERSION)
        if raw_report_df.empty:
            st.warning(f"No backtest records found for {selected_symbol}.")
            return

        min_date = raw_report_df["target_date"].min().date()
        max_date = raw_report_df["target_date"].max().date()

        date_range = st.date_input(
            "Evaluation Date Window",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )

        slippage_bps = st.slider(
            "Simulated Transaction Slippage (bps)",
            min_value=0.0,
            max_value=25.0,
            value=5.0,
            step=1.0,
        )

    if len(date_range) != 2:
        st.info("Select both a start and end date to render the report.")
        return

    start_dt, end_dt = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
    filtered_df = raw_report_df[
        (raw_report_df["target_date"] >= start_dt) & (raw_report_df["target_date"] <= end_dt)
    ]

    if filtered_df.empty:
        st.warning("No records match the selected date filter.")
        return

    perf_df = compute_dynamic_performance(filtered_df, slippage_bps)

    # Calculate window KPIs
    strat_ret = (perf_df["strategy_equity"].iloc[-1] - 1.0) * 100
    spy_ret = (perf_df["spy_equity"].iloc[-1] - 1.0) * 100
    ret_std = perf_df["strategy_net_return"].std()
    window_sharpe = (
        (perf_df["strategy_net_return"].mean() / ret_std) * np.sqrt(252) if ret_std > 0 else 0.0
    )
    window_max_dd = perf_df["strategy_drawdown"].min() * 100
    latest_rmse = perf_df["rolling_rmse"].iloc[-1]
    win_rate = perf_df["directional_hit"].mean() * 100

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Strategy Net Return", f"{strat_ret:+.2f}%", f"{strat_ret - spy_ret:+.2f}% vs SPY")
    col2.metric("Annualized Sharpe Ratio", f"{window_sharpe:.2f}")
    col3.metric("Maximum Drawdown", f"{window_max_dd:.2f}%")
    col4.metric("20-Day Rolling RMSE", f"${latest_rmse:.2f}")
    col5.metric("Directional Hit Rate", f"{win_rate:.1f}%")

    with st.sidebar:
        st.divider()
        csv_bytes = perf_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Export Filtered Report (CSV)",
            data=csv_bytes,
            file_name=f"{selected_symbol}_walkforward_report.csv",
            mime="text/csv",
        )

    tab_forecast, tab_equity, tab_explain, tab_health = st.tabs([
        "Price Forecast & RMSE",
        "Benchmark & Drawdown Analysis",
        "Model Explainability",
        "ETL Pipeline Health",
    ])

    with tab_forecast:
        fig_price = make_subplots(
            rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
            row_heights=[0.7, 0.3],
            subplot_titles=(
                f"{selected_symbol} Actual vs. Walk-Forward Predicted Close",
                "Rolling 20-Day Prediction Error (RMSE $)",
            ),
        )
        fig_price.add_trace(
            go.Scatter(
                x=perf_df["target_date"], y=perf_df["actual_close"],
                name="Actual Adjusted Close", line=dict(color="#1f77b4", width=2),
            ),
            row=1, col=1,
        )
        fig_price.add_trace(
            go.Scatter(
                x=perf_df["target_date"], y=perf_df["predicted_close"],
                name="Walk-Forward Predicted Close", line=dict(color="#ff7f0e", width=1.5, dash="dot"),
            ),
            row=1, col=1,
        )
        fig_price.add_trace(
            go.Scatter(
                x=perf_df["target_date"], y=perf_df["sma_50"],
                name="50-Day SMA (SQL View)", line=dict(color="#2ca02c", width=1),
            ),
            row=1, col=1,
        )
        fig_price.add_trace(
            go.Bar(
                x=perf_df["target_date"], y=perf_df["rolling_rmse"],
                name="Rolling 20D RMSE", marker_color="#d62728",
            ),
            row=2, col=1,
        )
        fig_price.update_layout(height=600, hovermode="x unified", margin=dict(t=40, b=20))
        st.plotly_chart(fig_price, use_container_width=True)

    with tab_equity:
        fig_eq = make_subplots(
            rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
            row_heights=[0.65, 0.35],
            subplot_titles=(
                "Cumulative Growth of $1.00 (Net of Slippage)",
                "Strategy Underwater Drawdown (%)",
            ),
        )
        fig_eq.add_trace(
            go.Scatter(
                x=perf_df["target_date"], y=perf_df["strategy_equity"],
                name="Walk-Forward Strategy (Net)", line=dict(color="#00cc96", width=2.5),
            ),
            row=1, col=1,
        )
        fig_eq.add_trace(
            go.Scatter(
                x=perf_df["target_date"], y=perf_df["buy_hold_equity"],
                name=f"{selected_symbol} Buy & Hold", line=dict(color="#636efa", width=1.5),
            ),
            row=1, col=1,
        )
        fig_eq.add_trace(
            go.Scatter(
                x=perf_df["target_date"], y=perf_df["spy_equity"],
                name="SPY Market Benchmark", line=dict(color="#ab63fa", width=1.5, dash="dash"),
            ),
            row=1, col=1,
        )
        fig_eq.add_trace(
            go.Scatter(
                x=perf_df["target_date"], y=perf_df["strategy_drawdown"] * 100,
                name="Drawdown (%)", fill="tozeroy", line=dict(color="#ef553b", width=1.5),
            ),
            row=2, col=1,
        )
        fig_eq.update_layout(height=600, hovermode="x unified", margin=dict(t=40, b=20))
        st.plotly_chart(fig_eq, use_container_width=True)

    with tab_explain:
        st.markdown(
            "Feature importance weights quantify how strongly each technical indicator influenced "
            "the latest walk-forward retraining window."
        )
        imp_df = load_feature_importances(selected_symbol, MODEL_VERSION)
        if not imp_df.empty:
            imp_df["readable_label"] = imp_df["feature_name"].map(
                lambda x: FEATURE_LABELS.get(x, x)
            )
            fig_imp = go.Figure(
                go.Bar(
                    x=imp_df["importance_weight"] * 100,
                    y=imp_df["readable_label"],
                    orientation="h",
                    marker_color="#1f77b4",
                    text=[f"{val * 100:.1f}%" for val in imp_df["importance_weight"]],
                    textposition="outside",
                )
            )
            fig_imp.update_layout(
                title=f"Model Feature Drivers ({selected_symbol})",
                xaxis_title="Relative Importance Weight (%)",
                yaxis_title="Engineered Feature",
                height=420,
                margin=dict(l=20, r=40, t=40, b=20),
            )
            st.plotly_chart(fig_imp, use_container_width=True)

    with tab_health:
        st.markdown(
            "Operational telemetry from the PostgreSQL audit log tracks ingestion completeness, "
            "filtered rows, and pipeline defects."
        )
        audit_df = load_etl_health_log()
        st.dataframe(audit_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()