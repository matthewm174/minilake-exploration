-- Conformed customers: trimmed, typed, one row per customer_id (latest updated_at wins).
SELECT
    customer_id,
    trim(first_name) AS first_name,
    trim(last_name) AS last_name,
    lower(trim(email)) AS email,
    upper(trim(country)) AS country,
    CAST(signup_date AS DATE) AS signup_date,
    CAST(updated_at AS TIMESTAMPTZ) AS updated_at
FROM lakehouse.bronze.customers
WHERE customer_id IS NOT NULL
QUALIFY row_number() OVER (
    PARTITION BY customer_id ORDER BY CAST(updated_at AS TIMESTAMPTZ) DESC, _source_file DESC
) = 1
