"""Streamlit front-end for the Retail Analytics Pipeline.

Run with:  streamlit run app.py --server.port 8501
"""
from __future__ import annotations

import math

import pandas as pd
import plotly.express as px
import streamlit as st

import database as db
import queries as q
from config import DATA_DIR, DB_CONFIG  # central config (environment variables override defaults)

st.set_page_config(page_title="Retail Analytics Pipeline", page_icon="📊", layout="wide")

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem;}
    div[data-testid="stMetric"] {
        background: rgba(120, 120, 140, 0.10);
        border: 1px solid rgba(120, 120, 140, 0.25);
        border-radius: 12px; padding: 14px 18px;
    }
    .success-card {
        background: linear-gradient(135deg, #0f9d58 0%, #1b7f5c 100%);
        color: #fff; padding: 18px 22px; border-radius: 12px; margin: 12px 0;
    }
    .success-card h4 {margin: 0 0 6px 0; color: #fff;}
    .success-card p {margin: 0; color: #eafff4;}
    </style>
    """,
    unsafe_allow_html=True,
)

PAGES = ["Data Orchestration & Ingestion", "Executive Dashboard Insights", "Deep-Dive SQL Analytics"]
TABLES = [("customers", "Customers"), ("products", "Products"), ("transactions", "Transactions")]
REGION_OPTIONS = ["All", "North", "South", "East", "West"]
CATEGORY_OPTIONS = ["All", "Electronics", "Furniture", "Office Supplies"]

if "db_cfg" not in st.session_state:
    st.session_state.db_cfg = dict(DB_CONFIG)


def money(value: float) -> str:
    return f"₹{value:,.0f}"


@st.cache_data(show_spinner=False, ttl=300)
def cached_query(sql: str, params: dict | None, cfg: dict) -> pd.DataFrame:
    return db.fetch_dataframe(sql, params, cfg)


# --------------------------------------------------------------------------- sidebar
def render_sidebar() -> str:
    st.sidebar.title("📊 Retail Analytics")
    page = st.sidebar.radio("Navigation", PAGES, label_visibility="collapsed")
    cfg = st.session_state.db_cfg
    with st.sidebar.expander("🔌 MySQL connection"):
        cfg["host"] = st.text_input("Host", cfg["host"])
        cfg["port"] = int(st.number_input("Port", min_value=1, max_value=65535, value=int(cfg["port"]), step=1))
        cfg["user"] = st.text_input("User", cfg["user"])
        cfg["password"] = st.text_input("Password", cfg["password"], type="password")
        cfg["database"] = st.text_input("Database", cfg["database"])
    if st.sidebar.button("Test connection"):
        ok, msg = db.test_connection(cfg)
        (st.sidebar.success if ok else st.sidebar.error)(msg)
    return page


# --------------------------------------------------------------------------- tab 1
def page_ingestion() -> None:
    st.title("Data Orchestration & Ingestion")
    st.caption("Upload the three CSV files (or reuse the ones in ./data), then run the ETL pipeline.")

    cols = st.columns(3)
    uploads = {
        name: col.file_uploader(f"{label} CSV", type=["csv"], key=f"up_{name}")
        for col, (name, label) in zip(cols, TABLES)
    }
    local_ok = all((DATA_DIR / f"{n}.csv").exists() for n, _ in TABLES)
    use_local = st.checkbox(
        "Use CSVs from ./data (generate_mock_data.py output) for any file not uploaded",
        value=local_ok, disabled=not local_ok,
    )

    frames: dict[str, pd.DataFrame] = {}
    for name, label in TABLES:
        try:
            if uploads[name] is not None:
                frames[name] = pd.read_csv(uploads[name])
            elif use_local and local_ok:
                frames[name] = pd.read_csv(DATA_DIR / f"{name}.csv")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read {name}.csv: {exc}")

    if frames:
        st.subheader("Input preview")
        for name, label in TABLES:
            if name in frames:
                with st.expander(f"{label}: {len(frames[name]):,} rows"):
                    st.dataframe(frames[name].head(50), hide_index=True)

    ready = len(frames) == 3
    if not ready:
        st.info("Provide all three CSV files to enable the pipeline.")

    just_ran = False
    if st.button("🚀 Run Pipeline", type="primary", disabled=not ready):
        just_ran = True
        st.subheader("Pipeline log")
        log_box = st.empty()
        lines: list[str] = []

        def on_log(line: str) -> None:
            lines.append(line)
            log_box.code("\n".join(lines), language="text")

        with st.spinner("Running ETL..."):
            result = db.run_pipeline(frames["customers"], frames["products"], frames["transactions"],
                                     cfg=st.session_state.db_cfg, on_log=on_log)
        st.session_state.last_run = result
        if result["success"]:
            st.cache_data.clear()
            rows = result["rows"]
            st.markdown(
                f"""<div class="success-card"><h4>✅ Pipeline completed successfully</h4>
                <p>{rows['customers']:,} customers · {rows['products']:,} products ·
                {rows['transactions']:,} transactions loaded in {result['elapsed']:.1f}s.
                Open the Executive Dashboard from the sidebar.</p></div>""",
                unsafe_allow_html=True,
            )
            st.toast("Data loaded into MySQL", icon="✅")
        else:
            st.error(f"Pipeline failed: {result['error']}")

    last = st.session_state.get("last_run")
    if last and not just_ran:
        with st.expander("Last pipeline run log"):
            st.code("\n".join(last["logs"]), language="text")


# --------------------------------------------------------------------------- tab 2
def page_dashboard() -> None:
    st.title("Executive Dashboard Insights")
    cfg = st.session_state.db_cfg
    try:
        bounds = cached_query(q.DATE_BOUNDS_SQL, None, cfg)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Cannot query MySQL. Check the connection and run the pipeline first.\n\n{exc}")
        return
    if bounds.empty or pd.isna(bounds.loc[0, "min_date"]):
        st.warning("No data found. Run the pipeline in the first tab.")
        return

    min_d = pd.to_datetime(bounds.loc[0, "min_date"]).date()
    max_d = pd.to_datetime(bounds.loc[0, "max_date"]).date()

    c1, c2, c3 = st.columns([1, 1, 2])
    region = c1.selectbox("Region", REGION_OPTIONS)
    category = c2.selectbox("Category", CATEGORY_OPTIONS)
    start, end = c3.slider("Date range", min_value=min_d, max_value=max_d,
                           value=(min_d, max_d), format="YYYY-MM-DD")
    params = {"start": start, "end": end, "region": region, "category": category}

    try:
        kpi = cached_query(q.KPI_SQL, params, cfg).iloc[0]
        if int(kpi["orders"]) == 0:
            st.info("No transactions match the selected filters.")
            return
        trend = cached_query(q.MONTHLY_TREND_SQL, params, cfg)
        pay = cached_query(q.PAYMENT_SHARE_SQL, params, cfg)
        cat = cached_query(q.CATEGORY_SQL, params, cfg)
        reg = cached_query(q.REGION_SQL, params, cfg)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Query failed: {exc}")
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Sales", money(float(kpi["total_sales"])))
    k2.metric("Net Profit", money(float(kpi["net_profit"])))
    k3.metric("Average Margin", f"{float(kpi['avg_margin_pct']):.2f}%")
    k4.metric("Order Lines", f"{int(kpi['orders']):,}")

    trend["month_start"] = pd.to_datetime(trend["month_start"])
    fig = px.line(trend, x="month_start", y=["revenue", "profit"], markers=True,
                  labels={"month_start": "Month", "value": "Amount (₹)", "variable": "Metric"},
                  title="Monthly revenue and profit trend")
    st.plotly_chart(fig)

    left, right = st.columns(2)
    left.plotly_chart(px.pie(pay, names="payment_method", values="revenue", hole=0.45,
                             title="Payment method share of revenue"))
    right.plotly_chart(px.bar(cat, x="category", y=["revenue", "profit"], barmode="group",
                              labels={"value": "Amount (₹)", "variable": "Metric"},
                              title="Revenue and profit by category"))
    st.plotly_chart(px.bar(reg, x="region", y="revenue", color="region",
                           labels={"revenue": "Revenue (₹)"}, title="Revenue by region"))


# --------------------------------------------------------------------------- tab 3
def render_table(df: pd.DataFrame, label: str, filename: str, key: str) -> None:
    st.subheader(label)
    c1, c2, c3 = st.columns([3, 1, 1])
    term = c1.text_input("🔍 Search", key=f"{key}_search")
    size = c2.selectbox("Rows per page", [10, 25, 50, 100], index=1, key=f"{key}_size")

    view = df
    if term:
        mask = df.astype(str).apply(
            lambda col: col.str.contains(term, case=False, regex=False, na=False)).any(axis=1)
        view = df[mask]

    pages = max(1, math.ceil(len(view) / size))
    page_key = f"{key}_page"
    if st.session_state.get(page_key, 1) > pages:
        st.session_state[page_key] = pages
    page = int(c3.number_input("Page", min_value=1, max_value=pages, value=1, step=1, key=page_key))

    lo, hi = (page - 1) * size, min(page * size, len(view))
    st.dataframe(view.iloc[lo:hi], hide_index=True)
    st.caption(f"Showing rows {lo + 1 if len(view) else 0}-{hi} of {len(view):,} (page {page}/{pages})")
    st.download_button("⬇️ Download Report as CSV", view.to_csv(index=False).encode("utf-8"),
                       file_name=f"{filename}.csv", mime="text/csv", key=f"{key}_dl")


def page_deep_dive() -> None:
    st.title("Deep-Dive SQL Analytics")
    st.caption("Runs the advanced CTE and window-function queries live against MySQL.")
    cfg = st.session_state.db_cfg

    cols = st.columns(len(q.PORTFOLIO_QUERIES))
    for col, (qid, spec) in zip(cols, q.PORTFOLIO_QUERIES.items()):
        if col.button(spec["button"], key=f"btn_{qid}"):
            try:
                with st.spinner(f"Running {spec['title']}..."):
                    frames = [(label, fname, db.fetch_dataframe(sql, None, cfg))
                              for label, sql, fname in spec["sections"]]
                st.session_state.deep = {"id": qid, "frames": frames}
            except Exception as exc:  # noqa: BLE001
                st.session_state.pop("deep", None)
                st.error(f"Query failed. Has the pipeline been run? Details: {exc}")

    deep = st.session_state.get("deep")
    if not deep:
        st.info("Choose a query above.")
        return
    spec = q.PORTFOLIO_QUERIES[deep["id"]]
    st.markdown(f"### {spec['title']}")
    st.caption(spec["description"])
    with st.expander("View SQL"):
        for _, sql, _ in spec["sections"]:
            st.code(sql.strip(), language="sql")
    for i, (label, fname, df) in enumerate(deep["frames"]):
        render_table(df, label, fname, key=f"{deep['id']}_{i}")


# --------------------------------------------------------------------------- router
selected = render_sidebar()
if selected == PAGES[0]:
    page_ingestion()
elif selected == PAGES[1]:
    page_dashboard()
else:
    page_deep_dive()
