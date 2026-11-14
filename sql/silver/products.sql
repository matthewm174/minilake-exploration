-- Conformed products with typed list price, one row per SKU.
SELECT
    sku,
    trim(product_name) AS product_name,
    lower(trim(category)) AS category,
    CAST(list_price AS DECIMAL(10, 2)) AS list_price
FROM lakehouse.bronze.products
QUALIFY row_number() OVER (PARTITION BY sku ORDER BY _source_file DESC) = 1
