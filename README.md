# Local S3 medallion lakehouse with minilake, feeding Redshift

This folder runs a laptop-sized copy of a common AWS data platform:

```
 sources ──► S3 landing ──► bronze ──► silver ──► gold ──► Redshift (analytics schema)
             (raw files)    (Delta)    (Delta)    (Delta)   (COPY from gold)
             └────────────── governed by Unity Catalog ──────────────┘
```

Each piece has a local stand-in:

| Production (AWS + Databricks) | Local emulation | Where it lives |
|---|---|---|
| Databricks workspace, Unity Catalog, SQL warehouse | [minilake](https://github.com/dmux/minilake): Databricks REST API emulator backed by DuckDB | `http://localhost:8000`, state in [data/](./data) |
| `s3://acme-landing/raw/` (landing bucket) | UC volume `/Volumes/lakehouse/landing/raw` | `data/volumes/lakehouse/landing/raw/` |
| `s3://acme-lakehouse/{bronze,silver,gold}/<table>/` | Real Delta Lake tables written by [delta-rs](https://delta-io.github.io/delta-rs/) | [s3/acme-lakehouse/](./s3/acme-lakehouse) |
| UC EXTERNAL tables over S3 locations | minilake EXTERNAL Delta tables (`storage_location` = local folder) | `lakehouse.<layer>.<table>` |
| Databricks jobs / notebooks running Spark SQL | [pipeline/lake.py](./pipeline/lake.py) running the [sql/](./sql) files with DuckDB | |
| Amazon Redshift (`dev` db, `awsuser`, port 5439) | PostgreSQL 16 on port 5439 (Redshift speaks the Postgres wire protocol) | Docker or native, see below |
| `COPY analytics.x FROM 's3://.../gold/x/' FORMAT AS PARQUET` | DuckDB reads the gold Delta table and writes it to Postgres | `python pipeline/lake.py redshift` |

## Unity Catalog layout

Diagrams of every component are in [docs/COMPONENTS.md](./docs/COMPONENTS.md). A screenshot guide to
the minilake web UI is in [docs/USER_GUIDE.md](./docs/USER_GUIDE.md).

```
lakehouse                          catalog   - "Medallion lakehouse emulating s3://acme-lakehouse"
├── landing                        schema
│   └── raw                        volume    - s3://acme-landing/raw/{customers,products,orders}/
├── bronze                         schema    - raw rows + _source_file/_ingested_at
│   ├── customers                  EXTERNAL DELTA  s3://acme-lakehouse/bronze/customers
│   ├── orders                     EXTERNAL DELTA  s3://acme-lakehouse/bronze/orders
│   └── products                   EXTERNAL DELTA  s3://acme-lakehouse/bronze/products
├── silver                         schema    - cleaned, typed, de-duplicated
│   ├── customers                  latest record per customer_id
│   ├── orders                     deduped, orphans removed, status normalised
│   ├── order_items                exploded from nested order JSON, invalid lines dropped
│   └── products
└── gold                           schema    - business aggregates = the Redshift contract
    ├── daily_sales
    ├── customer_lifetime_value
    └── product_performance
```

Every table carries UC properties `emulated_s3_uri`, `medallion_layer` and `source_sql`, and
a comment taken from the first line of its SQL file. The SQL warehouse is `lakehouse-wh`.

## Prerequisites

- Python 3.11+ and `pip install -r requirements.txt` (minilake with its `notebook` extra for
  JupyterLab, deltalake, duckdb, pyarrow, httpx)
- The `minilake` command on PATH. pip installs it into your user Scripts folder; if PowerShell
  says it is not recognised, add that folder to PATH. `start-minilake.ps1` finds it either way.
- For Redshift, one of:
  - Docker Desktop (`docker compose`), or
  - `pip install pgserver`, which provides native PostgreSQL binaries without Docker.

## Quick start

```powershell
cd D:\git\minilake

# 1. Start the Databricks/Unity Catalog emulator (leave this terminal open; stop with Ctrl+C)
.\start-minilake.ps1

# 2. In a second terminal, start the Redshift stand-in (pick one)
docker compose up -d --wait redshift        # Docker
.\start-redshift-local.ps1                  # no Docker

# 3. Build everything: UC objects -> land files -> bronze -> silver -> gold -> Redshift -> verify
python pipeline\lake.py all
```

`verify` prints the catalog tree with row counts. It gets them by querying each table through
minilake's SQL Statement API, the same API Databricks SQL clients use.

## The pipeline, step by step

Run any step on its own with `python pipeline\lake.py <step>`. `lake` runs every step except
`redshift`.

| Step | What it does | AWS/Databricks equivalent |
|---|---|---|
| `setup` | Creates the catalog, schemas, landing volume and SQL warehouse, and re-registers any Delta tables already in `s3/` (all idempotent) | Terraform / UC admin |
| `land` | Generates sample CRM, product and order extracts and uploads them with the **Files API** to `/Volumes/lakehouse/landing/raw/...` | `aws s3 cp`, Fivetran, DMS, Kinesis Firehose |
| `bronze` | Reads every landed file as-is (CSV as all-strings, JSON with nested items kept). Adds `_source_file` and `_ingested_at`, then writes Delta | Auto Loader / `COPY INTO` |
| `silver` | Trims, casts, normalises and de-duplicates (latest-wins on `updated_at`, at-least-once duplicates), drops orphans and bad lines, and explodes nested items | Spark SQL / DLT expectations |
| `gold` | Builds aggregates: revenue by day and channel, customer lifetime value, product performance | Spark SQL jobs |
| `redshift` | Loads each gold table into `analytics.<table>` through a staging table, swaps it in one transaction, and records the Delta version in `analytics.load_audit` | `COPY ... FORMAT AS PARQUET` + staging-table swap |
| `verify` | Prints the UC tree, row counts, and a sample gold query through minilake SQL | Databricks SQL |
| `reset` | Drops the `lakehouse` catalog (including landed files) and deletes `s3/` | |

The sample data has deliberate problems so each layer does visible work. Bronze keeps all 301
order records. Silver drops the duplicate and the record with no customer, leaving 299. The
customer whose country changed yields one current row instead of two.

### Incremental batches

Land another business day, then rebuild the downstream layers:

```powershell
python pipeline\lake.py land --date 2026-11-14 --seed 8
python pipeline\lake.py bronze; python pipeline\lake.py silver; python pipeline\lake.py gold; python pipeline\lake.py redshift
```

Bronze then contains both days' files. Silver resolves overlaps: the newest `_source_file` or
`updated_at` wins. Each gold rewrite creates a new Delta version, and `analytics.load_audit`
shows which version Redshift loaded.

## Adding or changing a table

Each layer folder in [sql/](./sql) holds one SELECT per file. **The file name is the table
name**, so `sql/gold/churn_risk.sql` becomes `lakehouse.gold.churn_risk` at
`s3://acme-lakehouse/gold/churn_risk/`. To add a table:

1. Start the file with a `-- comment` line; it becomes the UC table comment.
2. Reference upstream tables by their three-part UC names (`lakehouse.silver.orders`). The
   runner mounts every registered UC table under that name before running the SQL.
3. In bronze files, `${LANDING}` expands to the landing volume's local folder and
   `${LANDING_URI}` to `s3://acme-landing/raw`.
4. Run the layer. A new gold table is picked up by the `redshift` step automatically.

Files in a layer run alphabetically. A file may read tables from earlier layers, or from a file
earlier in the same layer.

The SQL dialect is **DuckDB**, which is close to Spark SQL for this kind of work (`QUALIFY`,
`unnest`, `FILTER`, `GROUP BY ALL`). Port carefully, and keep transforms ANSI-ish if they must
also run on Databricks.

## Using the lakehouse like Databricks

minilake accepts any token. Point the usual tools at `http://localhost:8000`.

**Web SQL editor:** <http://localhost:8000/ui/>. Try `SELECT * FROM lakehouse.gold.daily_sales`.

**Databricks SDK** (`pip install databricks-sdk`):

```python
from databricks.sdk import WorkspaceClient

w = WorkspaceClient(host="http://localhost:8000", token="dev")
for t in w.tables.list(catalog_name="lakehouse", schema_name="gold"):
    print(t.full_name, t.table_type, t.storage_location, t.properties["emulated_s3_uri"])

wh = next(x for x in w.warehouses.list() if x.name == "lakehouse-wh")
r = w.statement_execution.execute_statement(
    warehouse_id=wh.id,
    statement="SELECT customer_name, lifetime_revenue FROM lakehouse.gold.customer_lifetime_value LIMIT 5",
)
print(r.result.data_array)
```

**Raw REST:**

```powershell
irm "http://localhost:8000/api/2.1/unity-catalog/tables?catalog_name=lakehouse&schema_name=silver" | ConvertTo-Json -Depth 6
irm http://localhost:8000/api/2.0/fs/directories/Volumes/lakehouse/landing/raw/orders
```

**Reading the Delta files directly**, the way Athena, Glue or Redshift Spectrum would read the
bucket:

```python
import duckdb
duckdb.sql("SELECT * FROM delta_scan('s3/acme-lakehouse/gold/daily_sales') LIMIT 5").show()
```

## Querying Redshift

Connect any Postgres or Redshift client to `localhost:5439`, database `dev`, user `awsuser`,
password `localdev` (override with the `REDSHIFT_PASSWORD` env var). This is a local
development password only.

```powershell
# Docker
docker exec -it minilake-redshift psql -U awsuser -d dev
# native (start-redshift-local.ps1 prints the full psql path)
```

```sql
SELECT * FROM analytics.load_audit ORDER BY loaded_at DESC;
SELECT channel, sum(orders) AS orders, sum(revenue) AS revenue
FROM analytics.daily_sales GROUP BY 1 ORDER BY 3 DESC;
```

To check that the two sides agree, compare the channel totals from `python pipeline\lake.py
verify`, which reads the lakehouse through minilake, with the query above, which reads Redshift.

### What the load does in production

In production, the `redshift` step would be this Redshift SQL, run by Airflow, Step Functions or
a Databricks job after gold finishes:

```sql
CREATE TABLE analytics.stg_daily_sales (LIKE analytics.daily_sales);
COPY analytics.stg_daily_sales
FROM 's3://acme-lakehouse/gold/daily_sales/'      -- or a manifest of the current Delta version's files
IAM_ROLE 'arn:aws:iam::<account>:role/redshift-copy'
FORMAT AS PARQUET;
BEGIN;
  DROP TABLE analytics.daily_sales;
  ALTER TABLE analytics.stg_daily_sales RENAME TO daily_sales;
COMMIT;
```

Don't `COPY` a whole Delta folder: it also contains files from older versions. Generate a
manifest (`GENERATE symlink_format_manifest FOR TABLE ...`) or query the table through Redshift
Spectrum. The local loader avoids the problem by reading through `delta_scan`, which reads only
the files in the current version.

## Operating notes and limits

- **Shut minilake down with Ctrl+C.** With `MINILAKE_PERSIST=1`, which `start-minilake.ps1`
  sets, the UC registry is saved only on graceful shutdown. After a crash or a killed process,
  run `python pipeline\lake.py setup`. It recreates the catalog objects and re-registers every
  Delta table under `s3/`; the data is never lost because it lives in the Delta files.
- Run one minilake per data folder. A second copy fails with "unity_catalog.duckdb ... being
  used by another process".
- minilake rejects `s3://` storage locations. Tables are registered with the local folder path,
  and the S3 URI they stand for is kept in the `emulated_s3_uri` property.
- No authentication or grants are enforced. UC grants can be stored but have no effect.
- `INSERT`/`UPDATE`/`MERGE` through minilake SQL against EXTERNAL Delta tables run Spark in
  Docker. This setup avoids that: tables are rebuilt by `lake.py`, and minilake SQL is used
  read-only.
- The Redshift stand-in is plain Postgres. Redshift-only features, such as `DISTKEY`,
  `SORTKEY`, `COPY ... FROM 's3://'`, `UNLOAD`, Spectrum and `SUPER`, are not available. Keep
  that DDL in your deployment code, not in the local loop.

## Files

| Path | Purpose |
|---|---|
| [start-minilake.ps1](./start-minilake.ps1) | Starts minilake on :8000 with data in `./data`, persistence on, and the embedded JupyterLab on (`-NoNotebook` to turn it off) |
| [docker-compose.yml](./docker-compose.yml) | Redshift stand-in (Postgres 16 on :5439) |
| [start-redshift-local.ps1](./start-redshift-local.ps1) | Same stand-in without Docker (`-Stop` to stop); data in `./redshift-data` |
| [pipeline/lake.py](./pipeline/lake.py) | UC setup, landing, layer runner, Redshift loader, verify, reset |
| [sql/bronze](./sql/bronze), [sql/silver](./sql/silver), [sql/gold](./sql/gold) | One SELECT per table |
| [requirements.txt](./requirements.txt) | Python dependencies |

Environment overrides: `MINILAKE_HOST`, `MINILAKE_DATA_DIR`, `LAKE_S3_ROOT`, `REDSHIFT_DSN`,
`REDSHIFT_PASSWORD`.
