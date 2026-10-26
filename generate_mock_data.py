"""Generate three linked retail CSV datasets (customers, products, transactions).

Usage:
    python generate_mock_data.py [--out data] [--seed 42] [--customers 1200] [--transactions 60000]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2025-12-31")

SEGMENTS = ["Corporate", "Consumer", "Home Office"]
REGIONS = ["North", "South", "East", "West"]
PAYMENT_METHODS = ["Credit Card", "UPI", "Net Banking", "Cash"]

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Karan", "Ishaan", "Rahul", "Amit", "Vikram",
    "Ananya", "Diya", "Isha", "Kavya", "Meera", "Neha", "Priya", "Riya", "Sneha", "Tanvi",
    "Emma", "Liam", "Olivia", "Noah", "Sophia", "James", "Mia", "Lucas", "Zara", "Omar",
]
LAST_NAMES = [
    "Sharma", "Verma", "Singh", "Gupta", "Patel", "Mehta", "Kapoor", "Malhotra", "Reddy", "Nair",
    "Iyer", "Chopra", "Bansal", "Joshi", "Khanna", "Bhatia", "Sethi", "Arora", "Das", "Roy",
    "Smith", "Brown", "Wilson", "Taylor", "Khan", "Ali", "Fernandes", "D'Souza", "Thomas", "Walker",
]

# category -> (base product names, (min price, max price), quantity range per order line, popularity weight)
CATALOG = {
    "Electronics": (
        ["Laptop", "Smartphone", "Tablet", "Wireless Earbuds", "Smart Watch", "Bluetooth Speaker",
         "Monitor", "Webcam", "Power Bank", "Mechanical Keyboard"],
        (1500, 85000), (1, 3), 0.8,
    ),
    "Furniture": (
        ["Office Chair", "Standing Desk", "Bookshelf", "Filing Cabinet", "Sofa", "Coffee Table",
         "Dining Table", "Wardrobe", "Bed Frame", "TV Unit"],
        (3000, 45000), (1, 4), 0.45,
    ),
    "Office Supplies": (
        ["Notebook Pack", "Ballpoint Pens", "Stapler", "Printer Paper Ream", "Whiteboard",
         "Desk Organizer", "Highlighters", "Sticky Notes", "Binder Set", "Label Printer"],
        (50, 3000), (1, 20), 1.6,
    ),
}
TIERS = [("Standard", 1.0), ("Premium", 1.35)]

DISCOUNT_LEVELS = np.array([0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30])
DISCOUNT_P_NORMAL = np.array([0.40, 0.15, 0.15, 0.12, 0.08, 0.06, 0.04])
DISCOUNT_P_HOLIDAY = np.array([0.20, 0.10, 0.15, 0.17, 0.15, 0.13, 0.10])


def build_customers(rng: np.random.Generator, n: int) -> pd.DataFrame:
    ids = np.arange(1, n + 1)
    names = [f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}" for _ in ids]
    early = rng.random(n) < 0.6  # 60% signed up in 2023, the rest 2024-01-01 .. 2025-09-30
    late_days = (pd.Timestamp("2025-09-30") - pd.Timestamp("2024-01-01")).days + 1
    offsets = np.where(early, rng.integers(0, 365, n), rng.integers(365, 365 + late_days, n))
    signup = pd.Timestamp("2023-01-01") + pd.to_timedelta(offsets, unit="D")
    return pd.DataFrame({
        "customer_id": ids,
        "customer_name": names,
        "segment": rng.choice(SEGMENTS, size=n, p=[0.30, 0.50, 0.20]),
        "region": rng.choice(REGIONS, size=n, p=[0.28, 0.22, 0.26, 0.24]),
        "signup_date": signup,
    })


def build_products(rng: np.random.Generator) -> pd.DataFrame:
    rows, pid = [], 1
    for category, (bases, (lo, hi), _, _) in CATALOG.items():
        for base in bases:
            base_price = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
            for tier, mult in TIERS:
                unit_price = round(base_price * mult, 2)
                cost_price = round(unit_price * rng.uniform(0.50, 0.75), 2)
                rows.append((pid, f"{base} {tier}", category, unit_price, cost_price))
                pid += 1
    return pd.DataFrame(rows, columns=["product_id", "product_name", "category", "unit_price", "cost_price"])


def build_transactions(rng: np.random.Generator, customers: pd.DataFrame,
                       products: pd.DataFrame, n: int) -> pd.DataFrame:
    # --- order dates: seasonal Q4 surge (Nov x1.7, Dec x2.1), mild weekend lift, steady growth trend
    days = pd.date_range(START, END, freq="D")
    month = days.month.to_numpy()
    seasonal = np.select([month == 12, month == 11, month == 10], [2.1, 1.7, 1.15], default=1.0)
    weekend = np.where(days.dayofweek >= 5, 1.15, 1.0)
    trend = 1 + 0.25 * np.arange(len(days)) / (len(days) - 1)
    weights = seasonal * weekend * trend
    weights = weights / weights.sum()
    order_np = days.to_numpy().astype("datetime64[D]")[rng.choice(len(days), size=n, p=weights)]

    # --- customers (heavy-tailed activity) and no orders before a customer's signup date
    activity = rng.gamma(2.0, 1.0, len(customers))
    cust_idx = rng.choice(len(customers), size=n, p=activity / activity.sum())
    signup_np = customers["signup_date"].to_numpy().astype("datetime64[D]")[cust_idx]
    span = (np.datetime64(END.date()) - signup_np).astype(int)
    fixed = signup_np + np.floor(rng.random(n) * (span + 1)).astype(int).astype("timedelta64[D]")
    order_np = np.where(order_np < signup_np, fixed, order_np)

    # --- products with Zipf-like popularity inside each category
    pop = np.zeros(len(products))
    for category, (_, _, _, cat_w) in CATALOG.items():
        mask = (products["category"] == category).to_numpy()
        zipf = 1.0 / np.arange(1, mask.sum() + 1) ** 0.9
        pop[mask] = rng.permutation(zipf) * cat_w
    prod_idx = rng.choice(len(products), size=n, p=pop / pop.sum())
    cats = products["category"].to_numpy()[prod_idx]

    quantity = np.ones(n, dtype=int)
    for category, (_, _, (q_lo, q_hi), _) in CATALOG.items():
        m = cats == category
        quantity[m] = rng.integers(q_lo, q_hi + 1, m.sum())

    # --- discounts: deeper promotions in Nov/Dec
    months = order_np.astype("datetime64[M]").astype(int) % 12 + 1
    holiday = months >= 11
    discount = np.empty(n)
    discount[holiday] = rng.choice(DISCOUNT_LEVELS, size=holiday.sum(), p=DISCOUNT_P_HOLIDAY)
    discount[~holiday] = rng.choice(DISCOUNT_LEVELS, size=(~holiday).sum(), p=DISCOUNT_P_NORMAL)

    df = pd.DataFrame({
        "order_date": pd.to_datetime(order_np),
        "customer_id": customers["customer_id"].to_numpy()[cust_idx],
        "product_id": products["product_id"].to_numpy()[prod_idx],
        "quantity": quantity,
        "discount": np.round(discount, 2),
        "payment_method": rng.choice(PAYMENT_METHODS, size=n, p=[0.35, 0.35, 0.18, 0.12]),
    })
    df = df.sort_values("order_date", kind="stable").reset_index(drop=True)
    df.insert(0, "transaction_id", np.arange(1, n + 1))
    return df


def validate_and_report(customers, products, transactions) -> None:
    assert transactions["customer_id"].isin(customers["customer_id"]).all(), "FK customer_id broken"
    assert transactions["product_id"].isin(products["product_id"]).all(), "FK product_id broken"
    assert transactions["discount"].between(0.0, 0.3).all(), "discount out of range"
    assert (transactions["order_date"] >= START).all() and (transactions["order_date"] <= END).all()
    merged = (transactions.merge(products, on="product_id").merge(customers, on="customer_id"))
    assert (merged["order_date"] >= merged["signup_date"]).all(), "order before signup"
    merged["total_sales"] = merged["quantity"] * merged["unit_price"] * (1 - merged["discount"])
    merged["profit"] = merged["total_sales"] - merged["quantity"] * merged["cost_price"]
    monthly = merged.groupby(merged["order_date"].dt.month)["total_sales"].sum()
    avg_other = monthly.drop([11, 12]).mean()
    print(f"Total sales  : {merged['total_sales'].sum():,.2f}")
    print(f"Total profit : {merged['profit'].sum():,.2f}")
    print(f"Nov revenue is {monthly[11] / avg_other:.2f}x and Dec is {monthly[12] / avg_other:.2f}x "
          f"the Jan-Oct monthly average (Q4 surge)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate mock retail CSV data")
    parser.add_argument("--out", default="data", help="output directory")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--customers", type=int, default=1200)
    parser.add_argument("--transactions", type=int, default=60000)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    customers = build_customers(rng, args.customers)
    products = build_products(rng)
    transactions = build_transactions(rng, customers, products, args.transactions)
    validate_and_report(customers, products, transactions)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    customers_out = customers.assign(signup_date=customers["signup_date"].dt.strftime("%Y-%m-%d"))
    transactions_out = transactions.assign(order_date=transactions["order_date"].dt.strftime("%Y-%m-%d"))
    customers_out.to_csv(out / "customers.csv", index=False)
    products.to_csv(out / "products.csv", index=False)
    transactions_out.to_csv(out / "transactions.csv", index=False)
    print(f"Wrote {len(customers):,} customers, {len(products):,} products, "
          f"{len(transactions):,} transactions to {out.resolve()}")


if __name__ == "__main__":
    main()
