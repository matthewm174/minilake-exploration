"""Local S3 medallion lakehouse on minilake, feeding a local Redshift stand-in.

    python pipeline/lake.py all        # setup -> land -> bronze -> silver -> gold -> redshift -> verify
    python pipeline/lake.py <step>     # run one step; see --help

See README.md for the architecture and how each piece maps to AWS/Databricks.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import httpx
from deltalake import DeltaTable, write_deltalake

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
S3_ROOT = Path(os.getenv("LAKE_S3_ROOT", ROOT / "s3"))
DATA_DIR = Path(os.getenv("MINILAKE_DATA_DIR", ROOT / "data"))
HOST = os.getenv("MINILAKE_HOST", "http://localhost:8000")

CATALOG = "lakehouse"
LAKE_BUCKET = "acme-lakehouse"
LANDING_SCHEMA, LANDING_VOLUME = "landing", "raw"
LANDING_URI = "s3://acme-landing/raw"
LAYERS = ("bronze", "silver", "gold")
WAREHOUSE_NAME = "lakehouse-wh"

REDSHIFT_DSN = os.getenv(
    "REDSHIFT_DSN",
    f"host=localhost port=5439 dbname=dev user=awsuser password={os.getenv('REDSHIFT_PASSWORD', 'localdev')}",
)
REDSHIFT_SCHEMA = "analytics"

UC = "/api/2.1/unity-catalog"

SCHEMA_COMMENTS = {
    LANDING_SCHEMA: f"Raw drop zone. Volume {LANDING_VOLUME} emulates {LANDING_URI}",
    "bronze": f"Raw copies of landed files plus ingestion metadata. s3://{LAKE_BUCKET}/bronze/",
    "silver": f"Cleaned, typed, de-duplicated entities. s3://{LAKE_BUCKET}/silver/",
    "gold": f"Business aggregates; the contract loaded into Redshift. s3://{LAKE_BUCKET}/gold/",
}


def s3_to_local(uri: str) -> str:
    """Map an emulated s3://bucket/key URI to its folder under ./s3."""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3 uri: {uri}")
    return (S3_ROOT / uri[len("s3://") :]).as_posix()


def table_uri(layer: str, table: str) -> str:
    return f"s3://{LAKE_BUCKET}/{layer}/{table}"


def landing_dir() -> str:
    return (DATA_DIR / "volumes" / CATALOG / LANDING_SCHEMA / LANDING_VOLUME).as_posix()


class Minilake:
    """Thin client over the Databricks REST API that minilake emulates."""

    def __init__(self, host: str = HOST):
        self.http = httpx.Client(base_url=host, headers={"Authorization": "Bearer dev"}, timeout=120)
        self._warehouse_id: str | None = None

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            r = self.http.request(method, path, **kwargs)
        except httpx.ConnectError:
            sys.exit(f"Cannot reach minilake at {HOST}. Start it with .\\start-minilake.ps1")
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
        return r

    def get_or_none(self, path: str) -> dict | None:
        r = self._request_allow_404(path)
        return None if r is None else r.json()

    def _request_allow_404(self, path: str) -> httpx.Response | None:
        try:
            return self._request("GET", path)
        except RuntimeError as e:
            if "-> 404" in str(e):
                return None
            raise

    def ensure_catalog(self, name: str, comment: str) -> None:
        if not self.get_or_none(f"{UC}/catalogs/{name}"):
            self._request("POST", f"{UC}/catalogs", json={"name": name, "comment": comment})

    def ensure_schema(self, catalog: str, name: str, comment: str) -> None:
        if not self.get_or_none(f"{UC}/schemas/{catalog}.{name}"):
            self._request("POST", f"{UC}/schemas", json={"name": name, "catalog_name": catalog, "comment": comment})

    def ensure_volume(self, catalog: str, schema: str, name: str, comment: str) -> None:
        if not self.get_or_none(f"{UC}/volumes/{catalog}.{schema}.{name}"):
            body = {"name": name, "catalog_name": catalog, "schema_name": schema, "comment": comment}
            self._request("POST", f"{UC}/volumes", json=body)

    def list_tables(self, catalog: str, schema: str) -> list[dict]:
        r = self._request("GET", f"{UC}/tables", params={"catalog_name": catalog, "schema_name": schema})
        return r.json().get("tables") or []

    def register_external_table(self, catalog, schema, name, location, comment, properties) -> None:
        full_name = f"{catalog}.{schema}.{name}"
        existing = self.get_or_none(f"{UC}/tables/{full_name}")
        if existing and existing.get("storage_location") == location and existing.get("comment") == comment:
            return  # UC reads columns from the Delta log, so a rewrite needs no re-registration
        if existing:
            self._request("DELETE", f"{UC}/tables/{full_name}")
        body = {
            "name": name,
            "catalog_name": catalog,
            "schema_name": schema,
            "table_type": "EXTERNAL",
            "data_source_format": "DELTA",
            "storage_location": location,
            "comment": comment,
            "properties": properties,
        }
        self._request("POST", f"{UC}/tables", json=body)

    def upload(self, volume_path: str, content: bytes) -> None:
        self._request("PUT", f"/api/2.0/fs/files{volume_path}", params={"overwrite": "true"}, content=content)

    def warehouse_id(self) -> str:
        if self._warehouse_id:
            return self._warehouse_id
        for wh in self._request("GET", "/api/2.0/sql/warehouses").json().get("warehouses") or []:
            if wh.get("name") == WAREHOUSE_NAME:
                self._warehouse_id = wh["id"]
                return wh["id"]
        wh = self._request("POST", "/api/2.0/sql/warehouses", json={"name": WAREHOUSE_NAME, "cluster_size": "2X-Small"})
        self._warehouse_id = wh.json()["id"]
        return self._warehouse_id

    def sql(self, statement: str) -> tuple[list[str], list[list]]:
        body = {"warehouse_id": self.warehouse_id(), "statement": statement, "wait_timeout": "50s"}
        resp = self._request("POST", "/api/2.0/sql/statements", json=body).json()
        while resp["status"]["state"] in ("PENDING", "RUNNING"):
            time.sleep(0.5)
            resp = self._request("GET", f"/api/2.0/sql/statements/{resp['statement_id']}").json()
        if resp["status"]["state"] != "SUCCEEDED":
            raise RuntimeError(f"SQL failed: {resp['status']}")
        columns = [c["name"] for c in resp["manifest"]["schema"]["columns"]]
        return columns, (resp.get("result") or {}).get("data_array") or []


# ---------------------------------------------------------------- steps


def step_setup(ml: Minilake) -> None:
    """Create the Unity Catalog layout: catalog, medallion schemas, landing volume, warehouse."""
    ml.ensure_catalog(CATALOG, f"Medallion lakehouse emulating s3://{LAKE_BUCKET}")
    for schema in (LANDING_SCHEMA, *LAYERS):
        ml.ensure_schema(CATALOG, schema, SCHEMA_COMMENTS[schema])
    ml.ensure_volume(CATALOG, LANDING_SCHEMA, LANDING_VOLUME, f"Emulates {LANDING_URI}")
    for layer in LAYERS:
        Path(s3_to_local(f"s3://{LAKE_BUCKET}/{layer}")).mkdir(parents=True, exist_ok=True)
    print(f"  catalog {CATALOG}: schemas {LANDING_SCHEMA}, {', '.join(LAYERS)}")
    print(f"  volume  /Volumes/{CATALOG}/{LANDING_SCHEMA}/{LANDING_VOLUME}  ({LANDING_URI})")
    print(f"  warehouse {WAREHOUSE_NAME} id={ml.warehouse_id()}")
    # minilake only snapshots UC on graceful shutdown, so re-adopt any Delta tables already in the buckets.
    adopted = [
        _register(ml, layer, table_dir.name)
        for layer in LAYERS
        for table_dir in sorted(Path(s3_to_local(f"s3://{LAKE_BUCKET}/{layer}")).iterdir())
        if (table_dir / "_delta_log").is_dir()
    ]
    if adopted:
        print(f"  registered {len(adopted)} existing Delta tables from s3://{LAKE_BUCKET}/")


def _register(ml: Minilake, layer: str, table: str) -> str:
    """Register s3://<lake>/<layer>/<table> as an EXTERNAL Delta table in Unity Catalog."""
    uri = table_uri(layer, table)
    sql_file = SQL_DIR / layer / f"{table}.sql"
    comment = _comment(sql_file.read_text(encoding="utf-8")) if sql_file.exists() else ""
    ml.register_external_table(
        CATALOG, layer, table, s3_to_local(uri), comment,
        {"emulated_s3_uri": uri, "medallion_layer": layer, "source_sql": f"sql/{layer}/{sql_file.name}"},
    )
    return f"{CATALOG}.{layer}.{table}"


def _sample_files(day: date, seed: int) -> dict[str, bytes]:
    """Deterministic e-commerce extracts with deliberate data-quality problems."""
    rng = random.Random(seed)
    first = ["Ana", "Ben", "Chloe", "Dev", "Elif", "Femi", "Gus", "Hana", "Ivan", "Jade", "Kai", "Lena"]
    last = ["Silva", "Okafor", "Nguyen", "Patel", "Muller", "Rossi", "Kim", "Smith", "Dubois", "Sato"]
    countries = ["US", "CA", "GB", "DE", "FR", "BR", "JP", "AU"]
    now = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)

    customers = []
    for i in range(1, 51):
        fn, ln = rng.choice(first), rng.choice(last)
        customers.append({
            "customer_id": f"C{i:04d}",
            "first_name": fn,
            "last_name": ln,
            "email": f"{fn}.{ln}{i}@example.com".lower(),
            "country": rng.choice(countries),
            "signup_date": (day - timedelta(days=rng.randint(60, 900))).isoformat(),
            "updated_at": (now - timedelta(days=rng.randint(31, 60))).isoformat(),
        })
    customers[3]["email"] = f"  {customers[3]['email'].upper()} "  # messy whitespace/casing
    customers[7]["country"] = " de"
    moved = dict(customers[10], country="CA", updated_at=(now - timedelta(days=1)).isoformat())
    customers.append(moved)  # CDC-style duplicate: newer record must win in silver

    products = [
        ("SKU-100", "Trail Runner", "footwear", "89.00"), ("SKU-101", "Road Racer", "footwear", "129.00"),
        ("SKU-200", "Rain Shell", "apparel", "149.00"), ("SKU-201", "Base Layer", "apparel", "45.00"),
        ("SKU-202", "Wool Socks", "apparel", "14.50"), ("SKU-300", "Daypack 20L", "gear", "79.00"),
        ("SKU-301", "Headlamp", "gear", "35.00"), ("SKU-302", "Water Filter", "gear", "42.00"),
        ("SKU-400", "Energy Gel 6pk", "nutrition", "12.00"), ("SKU-401", "Electrolyte Tabs", "nutrition", "9.50"),
    ]

    orders = []
    statuses = ["completed"] * 8 + ["cancelled", "returned"]
    for n in range(1, 301):
        items = [
            {"sku": sku, "qty": rng.randint(1, 3), "unit_price": float(price)}
            for sku, _, _, price in rng.sample(products, rng.randint(1, 4))
        ]
        ts = now - timedelta(days=rng.randint(1, 30), minutes=rng.randint(0, 1439))
        orders.append({
            "order_id": f"O{day:%Y%m%d}-{n:04d}",
            "customer_id": f"C{rng.randint(1, 50):04d}",
            "order_ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": rng.choice(statuses),
            "channel": rng.choice(["web", "web", "mobile", "store"]),
            "items": items,
        })
    orders[5]["status"] = "COMPLETED"  # inconsistent casing
    orders[12]["customer_id"] = None  # orphan order: dropped in silver
    orders[20]["items"][0]["qty"] = 0  # invalid line: dropped in silver
    orders.append(json.loads(json.dumps(orders[40])))  # at-least-once delivery duplicate

    def to_csv(rows: list[dict]) -> bytes:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue().encode()

    product_rows = [dict(zip(("sku", "product_name", "category", "list_price"), p)) for p in products]
    stamp = day.isoformat()
    return {
        f"customers/customers_{stamp}.csv": to_csv(customers),
        f"products/products_{stamp}.csv": to_csv(product_rows),
        f"orders/orders_{stamp}.jsonl": "\n".join(json.dumps(o) for o in orders).encode() + b"\n",
    }


def step_land(ml: Minilake, day: date, seed: int) -> None:
    """Drop source extracts into the landing volume via the Files API (the `aws s3 cp` step)."""
    for rel, content in _sample_files(day, seed).items():
        ml.upload(f"/Volumes/{CATALOG}/{LANDING_SCHEMA}/{LANDING_VOLUME}/{rel}", content)
        print(f"  {LANDING_URI}/{rel:<38} {len(content):>7} bytes")


def _engine(ml: Minilake) -> duckdb.DuckDBPyConnection:
    """A DuckDB session that sees every lakehouse UC table under its three-part name."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute("INSTALL delta; LOAD delta")
    con.execute(f"ATTACH ':memory:' AS {CATALOG}")
    for schema in LAYERS:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{schema}")
        for table in ml.list_tables(CATALOG, schema):
            if table.get("storage_location"):
                _mount(con, schema, table["name"], table["storage_location"])
    con.execute(f"USE {CATALOG}")
    return con


def _mount(con: duckdb.DuckDBPyConnection, schema: str, table: str, location: str) -> None:
    con.execute(f"CREATE OR REPLACE VIEW {CATALOG}.{schema}.{table} AS SELECT * FROM delta_scan('{location}')")


def _render(sql: str) -> str:
    return sql.replace("${LANDING}", landing_dir()).replace("${LANDING_URI}", LANDING_URI)


def _comment(sql: str) -> str:
    first = sql.lstrip().splitlines()[0]
    return first[2:].strip() if first.startswith("--") else ""


def step_layer(ml: Minilake, layer: str) -> None:
    """Run sql/<layer>/*.sql; each file's result becomes the Delta table <layer>.<file stem>."""
    con = _engine(ml)
    for path in sorted((SQL_DIR / layer).glob("*.sql")):
        table, sql = path.stem, path.read_text(encoding="utf-8")
        result = con.execute(_render(sql))
        data = result.to_arrow_table() if hasattr(result, "to_arrow_table") else result.fetch_arrow_table()
        uri = table_uri(layer, table)
        location = s3_to_local(uri)
        write_deltalake(location, data, mode="overwrite", schema_mode="overwrite")
        _register(ml, layer, table)
        _mount(con, layer, table, location)
        print(f"  {CATALOG}.{layer}.{table:<26} {data.num_rows:>6} rows -> {uri}")


def step_redshift(ml: Minilake) -> None:
    """Load every gold table into Redshift, emulating `COPY ... FROM 's3://...' FORMAT AS PARQUET`."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute("INSTALL delta; LOAD delta; INSTALL postgres; LOAD postgres")
    try:
        con.execute(f"ATTACH '{REDSHIFT_DSN}' AS redshift (TYPE postgres)")
    except duckdb.Error as e:
        sys.exit(f"Cannot reach Redshift stand-in ({e}).\nStart it with: docker compose up -d redshift")

    def rs_exec(sql: str) -> None:
        con.execute("CALL postgres_execute('redshift', ?)", [sql])

    rs_exec(f"""
        CREATE SCHEMA IF NOT EXISTS {REDSHIFT_SCHEMA};
        CREATE TABLE IF NOT EXISTS {REDSHIFT_SCHEMA}.load_audit (
            table_name varchar(128), source_uri varchar(512), delta_version bigint,
            row_count bigint, loaded_at timestamptz DEFAULT now())""")

    for table in sorted(ml.list_tables(CATALOG, "gold"), key=lambda t: t["name"]):
        name, location = table["name"], table["storage_location"]
        uri = (table.get("properties") or {}).get("emulated_s3_uri", location)
        version = DeltaTable(location).version()

        # Load into a staging table, then swap in one transaction so readers never see a partial load.
        stage = f"stg_{name}"
        rs_exec(f"DROP TABLE IF EXISTS {REDSHIFT_SCHEMA}.{stage}")
        con.execute("CALL pg_clear_cache()")
        con.execute(f"CREATE TABLE redshift.{REDSHIFT_SCHEMA}.{stage} AS SELECT * FROM delta_scan('{location}')")
        rows = con.execute(f"SELECT count(*) FROM delta_scan('{location}')").fetchone()[0]
        rs_exec(f"""
            BEGIN;
            DROP TABLE IF EXISTS {REDSHIFT_SCHEMA}.{name};
            ALTER TABLE {REDSHIFT_SCHEMA}.{stage} RENAME TO {name};
            INSERT INTO {REDSHIFT_SCHEMA}.load_audit (table_name, source_uri, delta_version, row_count)
                VALUES ('{name}', '{uri}', {version}, {rows});
            COMMIT;""")
        print(f"  {REDSHIFT_SCHEMA}.{name:<26} {rows:>6} rows <- {uri} (delta v{version})")


def step_verify(ml: Minilake) -> None:
    """Show the UC tree and query each table through minilake's SQL Statement API."""
    print(f"  {CATALOG}")
    for schema in (LANDING_SCHEMA, *LAYERS):
        print(f"  +-- {schema}")
        if schema == LANDING_SCHEMA:
            files = [p for p in Path(landing_dir()).rglob("*") if p.is_file()]
            print(f"  |   +-- (volume) {LANDING_VOLUME:<17} {len(files):>6} files {LANDING_URI}/")
            continue
        for table in sorted(ml.list_tables(CATALOG, schema), key=lambda t: t["name"]):
            _, rows = ml.sql(f"SELECT count(*) FROM {CATALOG}.{schema}.{table['name']}")
            uri = (table.get("properties") or {}).get("emulated_s3_uri", "")
            print(f"  |   +-- {table['name']:<26} {int(rows[0][0]):>6} rows  {uri}")
    cols, rows = ml.sql(
        f"SELECT channel, sum(orders) AS orders, sum(revenue) AS revenue "
        f"FROM {CATALOG}.gold.daily_sales GROUP BY channel ORDER BY revenue DESC"
    )
    print("\n  gold.daily_sales by channel (queried through minilake SQL):")
    print("    " + " | ".join(cols))
    for r in rows:
        print("    " + " | ".join(str(v) for v in r))


def step_reset(ml: Minilake) -> None:
    """Drop the catalog and delete the emulated buckets."""
    if ml.get_or_none(f"{UC}/catalogs/{CATALOG}"):
        ml._request("DELETE", f"{UC}/catalogs/{CATALOG}", params={"force": "true"})
    shutil.rmtree(S3_ROOT, ignore_errors=True)
    print(f"  dropped catalog {CATALOG} and removed {S3_ROOT}")


STEPS = ["setup", "land", "bronze", "silver", "gold", "redshift", "verify"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=[*STEPS, "all", "lake", "reset"],
                        help="'all' runs every step; 'lake' runs all but redshift")
    parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 11, 13),
                        help="business date stamped on landed files (default 2026-11-13)")
    parser.add_argument("--seed", type=int, default=7, help="random seed for sample data")
    args = parser.parse_args()

    ml = Minilake()
    selected = {"all": STEPS, "lake": [s for s in STEPS if s != "redshift"]}.get(args.step, [args.step])
    for step in selected:
        print(f"[{step}]")
        if step == "setup":
            step_setup(ml)
        elif step == "land":
            step_land(ml, args.date, args.seed)
        elif step in LAYERS:
            step_layer(ml, step)
        elif step == "redshift":
            step_redshift(ml)
        elif step == "verify":
            step_verify(ml)
        elif step == "reset":
            step_reset(ml)


if __name__ == "__main__":
    main()
