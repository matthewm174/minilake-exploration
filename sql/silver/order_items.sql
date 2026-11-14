-- Order lines exploded from bronze order items; same order filters as silver.orders, zero-qty lines removed.
WITH orders AS (
    SELECT order_id, items
    FROM lakehouse.bronze.orders
    WHERE customer_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY _source_file DESC) = 1
),
lines AS (
    SELECT order_id, unnest(items) AS item, unnest(range(1, len(items) + 1)) AS line_number
    FROM orders
)
SELECT
    order_id,
    CAST(line_number AS INTEGER) AS line_number,
    item.sku AS sku,
    CAST(item.qty AS INTEGER) AS quantity,
    CAST(item.unit_price AS DECIMAL(10, 2)) AS unit_price,
    CAST(CAST(item.qty AS INTEGER) * CAST(item.unit_price AS DECIMAL(10, 2)) AS DECIMAL(12, 2)) AS line_amount
FROM lines
WHERE CAST(item.qty AS INTEGER) > 0
