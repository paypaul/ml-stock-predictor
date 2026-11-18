import os
import logging
from datetime import datetime
import pandas as pd
import yfinance as yf
from psycopg2.extras import execute_values
from db_config import get_connection
import warnings
import json

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

DEMO_TICKERS = [
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "sector": "Index ETF", "exchange": "NYSE"},
    {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology", "exchange": "NASDAQ"},
    {"symbol": "MSFT", "name": "Microsoft Corp.", "sector": "Technology", "exchange": "NASDAQ"},
    {"symbol": "LMT", "name": "Lockheed Martin Corp.", "sector": "Aerospace & Defense", "exchange": "NYSE"},
    {"symbol": "GD", "name": "General Dynamics Corp.", "sector": "Aerospace & Defense", "exchange": "NYSE"},
]

with open("sp500_tickers.json", "r", encoding="utf-8") as file:
    sp500 = json.load(file)

TARGET_TICKERS = sp500 or DEMO_TICKERS

def upsert_ticker_metadata(conn, ticker_info: dict) -> int:
    """Upserts ticker metadata and returns the primary key ticker_id."""
    query = """
        INSERT INTO tickers (symbol, company_name, sector, exchange, updated_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (symbol) DO UPDATE SET
            company_name = EXCLUDED.company_name,
            sector = EXCLUDED.sector,
            exchange = EXCLUDED.exchange,
            updated_at = CURRENT_TIMESTAMP
        RETURNING ticker_id;
    """
    with conn.cursor() as cur:
        cur.execute(query, (
            ticker_info["symbol"],
            ticker_info["name"],
            ticker_info["sector"],
            ticker_info["exchange"],
        ))
        ticker_id = cur.fetchone()[0]
    conn.commit()
    return ticker_id


def validate_and_filter_prices(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Applies documented enterprise data filters:
    1. Removes rows with NULL OHLCV values.
    2. Enforces positive prices and non-negative volume.
    3. Ensures High >= Low integrity.
    4. Deduplicates on trade_date keeping the latest record.
    """
    initial_count = len(df)
    if initial_count == 0:
        return df, 0

    required_cols = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    clean_df = df.dropna(subset=required_cols).copy()

    valid_mask = (
        (clean_df["Open"] > 0)
        & (clean_df["High"] > 0)
        & (clean_df["Low"] > 0)
        & (clean_df["Close"] > 0)
        & (clean_df["Adj Close"] > 0)
        & (clean_df["Volume"] >= 0)
        & (clean_df["High"] >= clean_df["Low"])
    )
    clean_df = clean_df[valid_mask].copy()
    clean_df = clean_df[~clean_df.index.duplicated(keep="last")]

    dropped_count = initial_count - len(clean_df)
    return clean_df, dropped_count


def log_etl_run(conn, symbol: str, fetched: int, dropped: int, upserted: int, status: str, error_msg: str = None):
    """Records ETL execution metrics for operational monitoring and defect triage."""
    query = """
        INSERT INTO etl_audit_log (symbol, records_fetched, records_dropped, records_upserted, status, error_message)
        VALUES (%s, %s, %s, %s, %s, %s);
    """
    with conn.cursor() as cur:
        cur.execute(query, (symbol, fetched, dropped, upserted, status, error_msg))
    conn.commit()


def ingest_ticker_history(conn, ticker_info: dict, start_date: str = "2020-01-01"):
    symbol = ticker_info["symbol"]
    logging.info(f"Starting ETL ingestion for {symbol} from {start_date}...")

    try:
        ticker_id = upsert_ticker_metadata(conn, ticker_info)
        raw_df = yf.download(symbol, start=start_date, auto_adjust=False, progress=False)

        if isinstance(raw_df.columns, pd.MultiIndex):
            raw_df.columns = raw_df.columns.get_level_values(0)

        fetched_count = len(raw_df)
        if fetched_count == 0:
            raise ValueError(f"Zero records returned from API for {symbol}.")

        clean_df, dropped_count = validate_and_filter_prices(raw_df)

        records = [
            (
                ticker_id,
                idx.date(),
                round(float(row["Open"]), 4),
                round(float(row["High"]), 4),
                round(float(row["Low"]), 4),
                round(float(row["Close"]), 4),
                round(float(row["Adj Close"]), 4),
                int(row["Volume"]),
            )
            for idx, row in clean_df.iterrows()
        ]

        upsert_query = """
            INSERT INTO daily_prices (
                ticker_id, trade_date, open_price, high_price, low_price, close_price, adj_close, volume
            ) VALUES %s
            ON CONFLICT (ticker_id, trade_date) DO UPDATE SET
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                adj_close = EXCLUDED.adj_close,
                volume = EXCLUDED.volume,
                ingested_at = CURRENT_TIMESTAMP;
        """

        with conn.cursor() as cur:
            execute_values(cur, upsert_query, records, page_size=1000)
        conn.commit()

        log_etl_run(conn, symbol, fetched_count, dropped_count, len(records), "SUCCESS")
        logging.info(f"[{symbol}] Upserted {len(records)} rows ({dropped_count} filtered out).")

    except Exception as e:
        conn.rollback()
        log_etl_run(conn, symbol, 0, 0, 0, "FAILED", str(e))
        logging.error(f"[{symbol}] ETL pipeline failure: {e}")


def main():
    conn = get_connection()
    try:
        for ticker in TARGET_TICKERS:
            ingest_ticker_history(conn, ticker, start_date="2021-01-01")
    finally:
        conn.close()


if __name__ == "__main__":
    main()