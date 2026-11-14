-- Completed-order revenue by day and sales channel.
SELECT
    o.order_date,
    o.channel,
    CAST(count(DISTINCT o.order_id) AS BIGINT) AS orders,
    CAST(sum(i.quantity) AS BIGINT) AS units,
    CAST(sum(i.line_amount) AS DECIMAL(18, 2)) AS revenue
FROM lakehouse.silver.orders AS o
JOIN lakehouse.silver.order_items AS i USING (order_id)
WHERE o.status = 'completed'
GROUP BY o.order_date, o.channel
ORDER BY o.order_date, o.channel
