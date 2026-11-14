-- Order headers: de-duplicated by order_id, typed, status normalised, orphans (no customer) removed.
SELECT
    order_id,
    customer_id,
    CAST(order_ts AS TIMESTAMPTZ) AS order_ts,
    CAST(CAST(order_ts AS TIMESTAMPTZ) AS DATE) AS order_date,
    lower(trim(status)) AS status,
    lower(trim(channel)) AS channel
FROM lakehouse.bronze.orders
WHERE customer_id IS NOT NULL
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY _source_file DESC) = 1
