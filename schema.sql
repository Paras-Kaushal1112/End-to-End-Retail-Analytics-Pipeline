-- Retail Analytics schema (MySQL 8.0+). Safe to re-run: it drops and recreates everything.
-- Note: keep comments on their own lines and avoid semicolons inside them.

CREATE DATABASE IF NOT EXISTS retail_analytics
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE retail_analytics;

DROP VIEW IF EXISTS vw_sales;
DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id   INT UNSIGNED NOT NULL,
    customer_name VARCHAR(100) NOT NULL,
    segment       ENUM('Corporate', 'Consumer', 'Home Office') NOT NULL,
    region        ENUM('North', 'South', 'East', 'West') NOT NULL,
    signup_date   DATE NOT NULL,
    PRIMARY KEY (customer_id),
    INDEX idx_customers_region (region),
    INDEX idx_customers_segment (segment)
) ENGINE = InnoDB;

CREATE TABLE products (
    product_id   INT UNSIGNED NOT NULL,
    product_name VARCHAR(120) NOT NULL,
    category     ENUM('Electronics', 'Furniture', 'Office Supplies') NOT NULL,
    unit_price   DECIMAL(10, 2) NOT NULL,
    cost_price   DECIMAL(10, 2) NOT NULL,
    PRIMARY KEY (product_id),
    INDEX idx_products_category (category),
    CONSTRAINT chk_products_prices CHECK (unit_price > 0 AND cost_price > 0 AND cost_price <= unit_price)
) ENGINE = InnoDB;

CREATE TABLE transactions (
    transaction_id INT UNSIGNED NOT NULL,
    order_date     DATE NOT NULL,
    customer_id    INT UNSIGNED NOT NULL,
    product_id     INT UNSIGNED NOT NULL,
    quantity       SMALLINT UNSIGNED NOT NULL,
    discount       DECIMAL(3, 2) NOT NULL DEFAULT 0.00,
    payment_method ENUM('Credit Card', 'UPI', 'Net Banking', 'Cash') NOT NULL,
    PRIMARY KEY (transaction_id),
    INDEX idx_txn_order_date (order_date),
    INDEX idx_txn_customer (customer_id, order_date),
    INDEX idx_txn_product (product_id, order_date),
    CONSTRAINT fk_txn_customer FOREIGN KEY (customer_id)
        REFERENCES customers (customer_id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_txn_product FOREIGN KEY (product_id)
        REFERENCES products (product_id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT chk_txn_quantity CHECK (quantity > 0),
    CONSTRAINT chk_txn_discount CHECK (discount >= 0.00 AND discount <= 0.30)
) ENGINE = InnoDB;

-- Denormalised analytics view: Sales = qty * unit_price * (1 - discount), Profit = Sales - qty * cost_price
CREATE VIEW vw_sales AS
SELECT
    t.transaction_id,
    t.order_date,
    t.customer_id,
    c.customer_name,
    c.segment,
    c.region,
    t.product_id,
    p.product_name,
    p.category,
    t.quantity,
    p.unit_price,
    p.cost_price,
    t.discount,
    t.payment_method,
    ROUND(t.quantity * p.unit_price * (1 - t.discount), 2) AS total_sales,
    ROUND(t.quantity * p.unit_price * (1 - t.discount) - t.quantity * p.cost_price, 2) AS profit
FROM transactions t
JOIN customers c ON c.customer_id = t.customer_id
JOIN products p ON p.product_id = t.product_id;
