-- Raw product catalogue extract: every landed row, all columns as strings, plus ingestion metadata.
SELECT
    * EXCLUDE (filename),
    '${LANDING_URI}/products/' || parse_filename(filename) AS _source_file,
    now() AS _ingested_at
FROM read_csv('${LANDING}/products/*.csv', header = true, all_varchar = true, filename = true)
