-- Raw order events (JSON lines, nested items kept as-is), duplicates and bad records included.
SELECT
    order_id,
    customer_id,
    order_ts,
    status,
    channel,
    items,
    '${LANDING_URI}/orders/' || parse_filename(filename) AS _source_file,
    now() AS _ingested_at
FROM read_json(
    '${LANDING}/orders/*.jsonl',
    format = 'newline_delimited',
    filename = true,
    columns = {
        order_id: 'VARCHAR',
        customer_id: 'VARCHAR',
        order_ts: 'VARCHAR',
        status: 'VARCHAR',
        channel: 'VARCHAR',
        items: 'STRUCT(sku VARCHAR, qty VARCHAR, unit_price VARCHAR)[]'
    }
)
