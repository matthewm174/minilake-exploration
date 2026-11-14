-- One row per customer: completed-order count, lifetime revenue and first/last order dates.
WITH order_totals AS (
    SELECT o.order_id, o.customer_id, o.order_date, sum(i.line_amount) AS order_amount
    FROM lakehouse.silver.orders AS o
    JOIN lakehouse.silver.order_items AS i USING (order_id)
    WHERE o.status = 'completed'
    GROUP BY ALL
)
SELECT
    c.customer_id,
    c.first_name || ' ' || c.last_name AS customer_name,
    c.country,
    c.signup_date,
    min(t.order_date) AS first_order_date,
    max(t.order_date) AS last_order_date,
    CAST(count(t.order_id) AS BIGINT) AS completed_orders,
    CAST(coalesce(sum(t.order_amount), 0) AS DECIMAL(18, 2)) AS lifetime_revenue
FROM lakehouse.silver.customers AS c
LEFT JOIN order_totals AS t USING (customer_id)
GROUP BY ALL
ORDER BY lifetime_revenue DESC, c.customer_id
