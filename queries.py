"""Analytical SQL (MySQL 8.0+: CTEs and window functions) and dashboard queries.

All queries read from the vw_sales view defined in schema.sql.
"""

# ----------------------------------------------------------------------------
# Query A - Month-over-Month revenue and profit growth
# ----------------------------------------------------------------------------
QUERY_A_MOM_GROWTH = """
WITH monthly AS (
    SELECT
        DATE_SUB(order_date, INTERVAL DAYOFMONTH(order_date) - 1 DAY) AS month_start,
        ROUND(SUM(total_sales), 2) AS revenue,
        ROUND(SUM(profit), 2)      AS profit,
        COUNT(*)                   AS orders
    FROM vw_sales
    GROUP BY DATE_SUB(order_date, INTERVAL DAYOFMONTH(order_date) - 1 DAY)
),
lagged AS (
    SELECT
        month_start, revenue, profit, orders,
        LAG(revenue) OVER (ORDER BY month_start) AS prev_revenue,
        LAG(profit)  OVER (ORDER BY month_start) AS prev_profit
    FROM monthly
)
SELECT
    month_start,
    orders,
    revenue,
    prev_revenue,
    ROUND(100 * (revenue - prev_revenue) / NULLIF(prev_revenue, 0), 2) AS revenue_mom_growth_pct,
    profit,
    prev_profit,
    ROUND(100 * (profit - prev_profit) / NULLIF(ABS(prev_profit), 0), 2) AS profit_mom_growth_pct
FROM lagged
ORDER BY month_start
"""

# ----------------------------------------------------------------------------
# Query B - RFM segmentation using NTILE(5) scores
# Score 5 is best: most recent, most frequent, highest spend.
# ----------------------------------------------------------------------------
QUERY_B_RFM = """
WITH ref AS (
    SELECT MAX(order_date) AS max_date FROM vw_sales
),
customer_metrics AS (
    SELECT
        s.customer_id,
        s.customer_name,
        s.segment,
        s.region,
        DATEDIFF(r.max_date, MAX(s.order_date)) AS recency_days,
        COUNT(*)                                AS frequency,
        ROUND(SUM(s.total_sales), 2)            AS monetary
    FROM vw_sales s
    CROSS JOIN ref r
    GROUP BY s.customer_id, s.customer_name, s.segment, s.region, r.max_date
),
scored AS (
    SELECT
        customer_id, customer_name, segment, region,
        recency_days, frequency, monetary,
        NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
        NTILE(5) OVER (ORDER BY frequency ASC)     AS f_score,
        NTILE(5) OVER (ORDER BY monetary ASC)      AS m_score
    FROM customer_metrics
)
SELECT
    customer_id, customer_name, segment, region,
    recency_days, frequency, monetary,
    r_score, f_score, m_score,
    CONCAT(r_score, f_score, m_score) AS rfm_code,
    r_score + f_score + m_score       AS rfm_total,
    CASE
        WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN 'Champions'
        WHEN r_score >= 3 AND f_score >= 3                  THEN 'Loyal Customers'
        WHEN r_score >= 4 AND f_score <= 2                  THEN 'New / Promising'
        WHEN r_score <= 2 AND f_score >= 3                  THEN 'At Risk'
        WHEN r_score <= 2 AND f_score <= 2                  THEN 'Hibernating'
        ELSE 'Needs Attention'
    END AS rfm_segment
FROM scored
ORDER BY rfm_total DESC, monetary DESC
"""

# ----------------------------------------------------------------------------
# Query C1 - 30-day rolling average of daily quantity sold per category
# ----------------------------------------------------------------------------
QUERY_C_ROLLING = """
WITH daily AS (
    SELECT category, order_date, CAST(SUM(quantity) AS SIGNED) AS qty_sold
    FROM vw_sales
    GROUP BY category, order_date
)
SELECT
    category,
    order_date,
    qty_sold,
    ROUND(AVG(qty_sold) OVER w, 2) AS rolling_30d_avg_qty,
    CAST(SUM(qty_sold) OVER w AS SIGNED) AS rolling_30d_total_qty
FROM daily
WINDOW w AS (
    PARTITION BY category
    ORDER BY order_date
    RANGE BETWEEN INTERVAL 29 DAY PRECEDING AND CURRENT ROW
)
ORDER BY category, order_date
"""

# ----------------------------------------------------------------------------
# Query C2 - Pareto 80/20: top 3 products per category inside the 80% revenue band
# ----------------------------------------------------------------------------
QUERY_C_PARETO = """
WITH product_revenue AS (
    SELECT category, product_id, product_name, ROUND(SUM(total_sales), 2) AS revenue
    FROM vw_sales
    GROUP BY category, product_id, product_name
),
ranked AS (
    SELECT
        category, product_id, product_name, revenue,
        ROW_NUMBER() OVER (PARTITION BY category ORDER BY revenue DESC, product_id) AS revenue_rank,
        SUM(revenue) OVER (PARTITION BY category) AS category_revenue,
        SUM(revenue) OVER (
            PARTITION BY category
            ORDER BY revenue DESC, product_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cumulative_revenue
    FROM product_revenue
)
SELECT
    category,
    revenue_rank,
    product_id,
    product_name,
    revenue,
    ROUND(100 * revenue / category_revenue, 2)            AS revenue_share_pct,
    ROUND(100 * cumulative_revenue / category_revenue, 2) AS cumulative_share_pct
FROM ranked
WHERE revenue_rank <= 3
  AND 100 * (cumulative_revenue - revenue) / category_revenue < 80
ORDER BY category, revenue_rank
"""

PORTFOLIO_QUERIES = {
    "A": {
        "button": "A - MoM Growth",
        "title": "Query A: Month-over-Month Revenue and Profit Growth",
        "description": "CTEs + LAG() across the 24-month horizon.",
        "sections": [("Monthly growth", QUERY_A_MOM_GROWTH, "mom_growth")],
    },
    "B": {
        "button": "B - RFM Segmentation",
        "title": "Query B: RFM Customer Segmentation",
        "description": "Recency / Frequency / Monetary scored with NTILE(5) window functions.",
        "sections": [("Customer RFM scores", QUERY_B_RFM, "rfm_segmentation")],
    },
    "C": {
        "button": "C - Rolling Avg and Pareto",
        "title": "Query C: Moving Metrics and Pareto 80/20",
        "description": "30-day rolling quantity average per category, plus top-3 products within the 80% revenue band.",
        "sections": [
            ("Top products (Pareto 80/20)", QUERY_C_PARETO, "pareto_top_products"),
            ("30-day rolling average of quantity sold", QUERY_C_ROLLING, "rolling_30d_quantity"),
        ],
    },
}

# ----------------------------------------------------------------------------
# Dashboard queries (parameters: :start, :end, :region, :category)
# ----------------------------------------------------------------------------
_FILTER = """
WHERE order_date BETWEEN :start AND :end
  AND (:region = 'All' OR region = :region)
  AND (:category = 'All' OR category = :category)
"""

DATE_BOUNDS_SQL = "SELECT MIN(order_date) AS min_date, MAX(order_date) AS max_date FROM vw_sales"

KPI_SQL = f"""
SELECT
    COUNT(*) AS orders,
    ROUND(SUM(total_sales), 2) AS total_sales,
    ROUND(SUM(profit), 2) AS net_profit,
    ROUND(100 * SUM(profit) / NULLIF(SUM(total_sales), 0), 2) AS avg_margin_pct
FROM vw_sales
{_FILTER}
"""

MONTHLY_TREND_SQL = f"""
SELECT
    DATE_SUB(order_date, INTERVAL DAYOFMONTH(order_date) - 1 DAY) AS month_start,
    ROUND(SUM(total_sales), 2) AS revenue,
    ROUND(SUM(profit), 2) AS profit
FROM vw_sales
{_FILTER}
GROUP BY DATE_SUB(order_date, INTERVAL DAYOFMONTH(order_date) - 1 DAY)
ORDER BY month_start
"""

PAYMENT_SHARE_SQL = f"""
SELECT payment_method, ROUND(SUM(total_sales), 2) AS revenue, COUNT(*) AS orders
FROM vw_sales
{_FILTER}
GROUP BY payment_method
ORDER BY revenue DESC
"""

CATEGORY_SQL = f"""
SELECT category, ROUND(SUM(total_sales), 2) AS revenue, ROUND(SUM(profit), 2) AS profit
FROM vw_sales
{_FILTER}
GROUP BY category
ORDER BY revenue DESC
"""

REGION_SQL = f"""
SELECT region, ROUND(SUM(total_sales), 2) AS revenue
FROM vw_sales
{_FILTER}
GROUP BY region
ORDER BY revenue DESC
"""
