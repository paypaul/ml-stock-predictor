import os
import sys
import logging
import pandas as pd
from db_config import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", 5432)),
    "dbname": os.environ.get("DB_NAME", "stock_analytics"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", "pass1234"),
}

MAX_STALE_DAYS = 5
MAX_DAILY_MOVE_PCT = 0.35


def check_recent_etl_failures(conn) -> list[str]:
    """Flags any ETL execution failures recorded in the last 24 hours."""
    query = """
        SELECT symbol, error_message, executed_at
        FROM etl_audit_log
        WHERE status = 'FAILED'
          AND executed_at >= NOW() - INTERVAL '24 hours';
    """
    df = pd.read_sql_query(query, conn)
    issues = []
    for _, row in df.iterrows():
        issues.append(
            f"ETL failure for {row['symbol']} at {row['executed_at']}: {row['error_message']}"
        )
    return issues


def check_data_freshness(conn) -> list[str]:
    """Verifies that all active tickers have updated price records within the staleness threshold."""
    query = """
        SELECT t.symbol, MAX(p.trade_date) AS latest_date,
               CURRENT_DATE - MAX(p.trade_date) AS days_stale
        FROM tickers t
        LEFT JOIN daily_prices p ON t.ticker_id = p.ticker_id
        WHERE t.is_active = TRUE
        GROUP BY t.symbol;
    """
    df = pd.read_sql_query(query, conn)
    issues = []
    for _, row in df.iterrows():
        if pd.isna(row["latest_date"]):
            issues.append(f"Ticker {row['symbol']} has zero price records in daily_prices.")
        elif int(row["days_stale"]) > MAX_STALE_DAYS:
            issues.append(
                f"Stale feed for {row['symbol']}: latest record is {row['latest_date']} "
                f"({row['days_stale']} days old)."
            )
    return issues


def check_anomalous_price_spikes(conn) -> list[str]:
    """Flags extreme single-day return spikes that may indicate unadjusted stock splits or bad data."""
    query = """
        SELECT symbol, trade_date, daily_return
        FROM vw_ticker_analytics
        WHERE ABS(daily_return) > %s
          AND trade_date >= CURRENT_DATE - INTERVAL '30 days';
    """
    df = pd.read_sql_query(query, conn, params=(MAX_DAILY_MOVE_PCT,))
    issues = []
    for _, row in df.iterrows():
        move_pct = float(row["daily_return"]) * 100
        issues.append(
            f"Anomalous daily move for {row['symbol']} on {row['trade_date']}: {move_pct:+.2f}%."
        )
    return issues


def main():
    logging.info("Starting automated data-health and operational verification...")
    conn = get_connection

    try:
        all_issues = (
            check_recent_etl_failures(conn)
            + check_data_freshness(conn)
            + check_anomalous_price_spikes(conn)
        )
        if all_issues:
            for issue in all_issues:
                logging.warning(f"HEALTH ALERT: {issue}")
            logging.error(f"Health check failed with {len(all_issues)} operational issue(s).")
            sys.exit(1)

        logging.info("All database schemas, ETL feeds, and price views passed verification.")
        sys.exit(0)
    finally:
        conn.close()


if __name__ == "__main__":
    main()