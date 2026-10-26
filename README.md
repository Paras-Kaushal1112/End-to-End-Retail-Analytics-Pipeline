# Retail Analytics Pipeline (Python + MySQL + Streamlit)

## Requirements
- Python 3.10+
- MySQL 8.0+ (CTEs, window functions and CHECK constraints need 8.0.16 or newer)

## Quick start
```bash
cd retail_analytics_pipeline
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# MySQL credentials (or edit config.py / use the sidebar in the app)
export MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root MYSQL_PASSWORD=your_password
# Windows PowerShell: $env:MYSQL_PASSWORD="your_password"

python generate_mock_data.py     # writes ./data/*.csv (already included)
streamlit run app.py --server.port 8501
```
Open http://localhost:8501, go to "Data Orchestration & Ingestion" and click "Run Pipeline".
The account needs privileges to CREATE/DROP the `retail_analytics` database.

Headless load without the UI: `python database.py --data-dir data`

## Files
| File | Purpose |
|---|---|
| generate_mock_data.py | Linked customers/products/transactions CSVs, Q4 surge |
| schema.sql | DDL: PK/FK CASCADE, indexes, CHECKs, `vw_sales` view |
| database.py | Engine, validation, transactional ETL, logging |
| queries.py | MoM growth, RFM, rolling average + Pareto, dashboard SQL |
| app.py | Streamlit UI |
| config.py | Central config with environment fallback |
