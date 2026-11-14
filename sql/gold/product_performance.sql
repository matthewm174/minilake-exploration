-- Units, revenue and return rate per product over all non-cancelled orders.
SELECT
    p.sku,
    p.product_name,
    p.category,
    CAST(coalesce(sum(i.quantity) FILTER (WHERE o.status = 'completed'), 0) AS BIGINT) AS units_sold,
    CAST(coalesce(sum(i.line_amount) FILTER (WHERE o.status = 'completed'), 0) AS DECIMAL(18, 2)) AS revenue,
    CAST(coalesce(sum(i.quantity) FILTER (WHERE o.status = 'returned'), 0) AS BIGINT) AS units_returned,
    CAST(
        coalesce(sum(i.quantity) FILTER (WHERE o.status = 'returned'), 0)
        / nullif(sum(i.quantity) FILTER (WHERE o.status IN ('completed', 'returned')), 0) AS DECIMAL(5, 4)
    ) AS return_rate
FROM lakehouse.silver.products AS p
LEFT JOIN lakehouse.silver.order_items AS i USING (sku)
LEFT JOIN lakehouse.silver.orders AS o ON o.order_id = i.order_id AND o.status <> 'cancelled'
GROUP BY ALL
ORDER BY revenue DESC
