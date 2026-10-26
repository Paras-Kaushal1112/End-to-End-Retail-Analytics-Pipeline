"""Database engine, ETL and query helpers (SQLAlchemy + PyMySQL)."""
from __future__ import annotations

import argparse
import logging
import re
import time
from decimal import Decimal
from typing import Callable, Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

from config import DATA_DIR, DB_CONFIG, SCHEMA_PATH

logger = logging.getLogger("retail_etl")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _console = logging.StreamHandler()
    _console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
    logger.addHandler(_console)


class CallbackHandler(logging.Handler):
    """Forwards formatted log lines to a callback (used to stream logs into Streamlit)."""

    def __init__(self, callback: Callable[[str], None]):
        super().__init__(level=logging.INFO)
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.callback(self.format(record))


# --------------------------------------------------------------------------- engines
_ENGINES: dict[tuple, Engine] = {}


def get_engine(cfg: Optional[dict] = None, with_database: bool = True) -> Engine:
    cfg = cfg or DB_CONFIG
    key = (cfg["host"], int(cfg["port"]), cfg["user"], cfg["password"],
           cfg["database"] if with_database else None)
    if key not in _ENGINES:
        url = URL.create(
            "mysql+pymysql",
            username=cfg["user"],
            password=cfg["password"] or None,
            host=cfg["host"],
            port=int(cfg["port"]),
            database=cfg["database"] if with_database else None,
            query={"charset": "utf8mb4"},
        )
        _ENGINES[key] = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    return _ENGINES[key]


def test_connection(cfg: Optional[dict] = None) -> tuple[bool, str]:
    cfg = cfg or DB_CONFIG
    try:
        with get_engine(cfg, with_database=False).connect() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar()
        return True, f"Connected to MySQL {version}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Connection failed: {exc}"


# --------------------------------------------------------------------------- schema
def _split_sql(script: str) -> list[str]:
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def initialize_schema(cfg: Optional[dict] = None) -> None:
    """Drop and recreate the database objects defined in schema.sql."""
    cfg = cfg or DB_CONFIG
    if not re.fullmatch(r"[A-Za-z0-9_]+", cfg["database"]):
        raise ValueError("Database name may only contain letters, digits and underscores.")
    script = SCHEMA_PATH.read_text(encoding="utf-8").replace("retail_analytics", cfg["database"])
    statements = _split_sql(script)
    logger.info("Applying schema (%d statements) to database '%s'", len(statements), cfg["database"])
    with get_engine(cfg, with_database=False).connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()
    logger.info("Schema ready: customers, products, transactions, vw_sales")


# --------------------------------------------------------------------------- validation
REQUIRED = {
    "customers": ["customer_id", "customer_name", "segment", "region", "signup_date"],
    "products": ["product_id", "product_name", "category", "unit_price", "cost_price"],
    "transactions": ["transaction_id", "order_date", "customer_id", "product_id",
                     "quantity", "discount", "payment_method"],
}
DOMAINS = {
    ("customers", "segment"): {"Corporate", "Consumer", "Home Office"},
    ("customers", "region"): {"North", "South", "East", "West"},
    ("products", "category"): {"Electronics", "Furniture", "Office Supplies"},
    ("transactions", "payment_method"): {"Credit Card", "UPI", "Net Banking", "Cash"},
}


def validate_frames(customers: pd.DataFrame, products: pd.DataFrame,
                    transactions: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Check schema, types, domains and referential integrity. Returns cleaned copies."""
    frames = {"customers": customers.copy(), "products": products.copy(), "transactions": transactions.copy()}
    for name, df in frames.items():
        missing = [c for c in REQUIRED[name] if c not in df.columns]
        if missing:
            raise ValueError(f"{name}.csv is missing columns: {missing}")
        df = df[REQUIRED[name]]
        if df.isnull().any().any():
            bad = df.columns[df.isnull().any()].tolist()
            raise ValueError(f"{name}.csv has empty values in columns: {bad}")
        pk = REQUIRED[name][0]
        if df[pk].duplicated().any():
            raise ValueError(f"{name}.csv has duplicate primary keys in '{pk}'")
        for (tbl, col), allowed in DOMAINS.items():
            if tbl == name:
                invalid = set(df[col].unique()) - allowed
                if invalid:
                    raise ValueError(f"{name}.{col} has invalid values: {sorted(invalid)}")
        frames[name] = df

    c, p, t = frames["customers"], frames["products"], frames["transactions"]
    c["signup_date"] = pd.to_datetime(c["signup_date"]).dt.date
    t["order_date"] = pd.to_datetime(t["order_date"]).dt.date
    for col in ("unit_price", "cost_price"):
        p[col] = pd.to_numeric(p[col])
    t["quantity"] = pd.to_numeric(t["quantity"]).astype(int)
    t["discount"] = pd.to_numeric(t["discount"]).round(2)

    if not t["discount"].between(0.0, 0.3).all():
        raise ValueError("transactions.discount must be between 0.0 and 0.3")
    if (t["quantity"] <= 0).any():
        raise ValueError("transactions.quantity must be positive")
    if (p["cost_price"] > p["unit_price"]).any():
        raise ValueError("products.cost_price must not exceed unit_price")
    if not t["customer_id"].isin(c["customer_id"]).all():
        raise ValueError("transactions.customer_id contains IDs not present in customers.csv")
    if not t["product_id"].isin(p["product_id"]).all():
        raise ValueError("transactions.product_id contains IDs not present in products.csv")
    return {"customers": c, "products": p, "transactions": t}


# --------------------------------------------------------------------------- ETL
def run_pipeline(customers: pd.DataFrame, products: pd.DataFrame, transactions: pd.DataFrame,
                 cfg: Optional[dict] = None, on_log: Optional[Callable[[str], None]] = None) -> dict:
    """Validate -> drop/recreate schema -> bulk load (single transaction) -> verify."""
    cfg = cfg or DB_CONFIG
    lines: list[str] = []

    def sink(line: str) -> None:
        lines.append(line)
        if on_log:
            on_log(line)

    handler = CallbackHandler(sink)
    logger.addHandler(handler)
    started = time.time()
    result = {"success": False, "rows": {}, "elapsed": 0.0, "logs": lines, "error": None}
    try:
        logger.info("Pipeline started (target %s:%s/%s)", cfg["host"], cfg["port"], cfg["database"])
        logger.info("Validating input: %d customers, %d products, %d transactions",
                    len(customers), len(products), len(transactions))
        frames = validate_frames(customers, products, transactions)
        logger.info("Validation passed (columns, domains, keys, referential integrity)")

        initialize_schema(cfg)

        engine = get_engine(cfg)
        with engine.begin() as conn:  # one transaction: all tables load or none do
            for name in ("customers", "products", "transactions"):
                t0 = time.time()
                frames[name].to_sql(name, conn, if_exists="append", index=False,
                                    chunksize=2000, method="multi")
                logger.info("Loaded %-12s %8d rows in %.2fs", name, len(frames[name]), time.time() - t0)
        logger.info("Transaction committed")

        with engine.connect() as conn:
            for name in ("customers", "products", "transactions"):
                n = conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
                result["rows"][name] = int(n)
                if n != len(frames[name]):
                    raise RuntimeError(f"Row count mismatch in {name}: expected {len(frames[name])}, found {n}")
            n_view = conn.execute(text("SELECT COUNT(*) FROM vw_sales")).scalar()
        logger.info("Verification passed. vw_sales exposes %d rows", n_view)
        result["success"] = True
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        logger.error("Pipeline failed: %s", exc)
        logger.error("Any open data-load transaction was rolled back")
    finally:
        result["elapsed"] = time.time() - started
        logger.info("Pipeline finished in %.2fs (%s)", result["elapsed"],
                    "SUCCESS" if result["success"] else "FAILED")
        logger.removeHandler(handler)
    return result


# --------------------------------------------------------------------------- reads
def _decimals_to_float(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna()
            if not sample.empty and isinstance(sample.iloc[0], Decimal):
                df[col] = df[col].astype(float)
    return df


def fetch_dataframe(sql: str, params: Optional[dict] = None, cfg: Optional[dict] = None) -> pd.DataFrame:
    engine = get_engine(cfg or DB_CONFIG)
    with engine.connect() as conn:
        df = pd.read_sql_query(text(sql), conn, params=params or {})
    return _decimals_to_float(df)


# --------------------------------------------------------------------------- CLI
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load CSVs into MySQL without the web UI")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    args = parser.parse_args()
    d = args.data_dir
    res = run_pipeline(pd.read_csv(f"{d}/customers.csv"), pd.read_csv(f"{d}/products.csv"),
                       pd.read_csv(f"{d}/transactions.csv"))
    raise SystemExit(0 if res["success"] else 1)
