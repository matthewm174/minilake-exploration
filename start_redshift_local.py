#!/usr/bin/env python3
"""Docker-free Redshift stand-in: a native PostgreSQL 16 (from the `pgserver` wheel)
on Redshift's port 5439, with Redshift's default database/user (dev / awsuser).

    python start_redshift_local.py          # init (first run) + start
    python start_redshift_local.py --stop   # stop
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "redshift-data"
LOG = DATA / "server.log"


def pg_bin() -> Path:
    try:
        import pgserver
    except ImportError:
        sys.exit("pgserver not installed. Run: pip install pgserver")
    bin_dir = Path(pgserver.__file__).parent / "pginstall" / "bin"
    if not bin_dir.is_dir():
        sys.exit(f"pgserver binaries not found in {bin_dir}")
    return bin_dir


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stop", action="store_true", help="stop the server")
    ap.add_argument("--port", type=int, default=5439)
    ap.add_argument("--password", default=os.environ.get("REDSHIFT_PASSWORD", "localdev"))
    args = ap.parse_args()

    bin_dir = pg_bin()
    suffix = ".exe" if os.name == "nt" else ""

    def tool(name: str) -> str:
        return str(bin_dir / (name + suffix))

    if args.stop:
        return subprocess.run([tool("pg_ctl"), "-D", str(DATA), "stop", "-m", "fast"]).returncode

    if not (DATA / "PG_VERSION").is_file():
        fd, pwfile = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "w") as f:
                f.write(args.password)
            subprocess.run([tool("initdb"), "-D", str(DATA), "-U", "awsuser", f"--pwfile={pwfile}",
                            "-A", "scram-sha-256", "-E", "UTF8", "--no-locale"],
                           check=True, stdout=subprocess.DEVNULL)
        finally:
            os.remove(pwfile)

    ready = subprocess.run([tool("pg_isready"), "-h", "127.0.0.1", "-p", str(args.port), "-q"])
    if ready.returncode != 0:
        # Don't pipe pg_ctl's output: the server would inherit the pipe and this script would hang.
        # On Windows, CREATE_NO_WINDOW also keeps the server out of this console's Ctrl+C.
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        start = subprocess.run(
            [tool("pg_ctl"), "-D", str(DATA), "-l", str(LOG),
             "-o", f"-p {args.port} -c listen_addresses=localhost", "-w", "start"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags)
        if start.returncode != 0:
            sys.exit(f"pg_ctl start failed; see {LOG}")

    env = {**os.environ, "PGPASSWORD": args.password}
    common = ["-h", "localhost", "-p", str(args.port), "-U", "awsuser"]
    exists = subprocess.run([tool("psql"), *common, "-d", "postgres", "-tAc",
                             "select 1 from pg_database where datname='dev'"],
                            env=env, capture_output=True, text=True, check=True).stdout.strip()
    if exists != "1":
        subprocess.run([tool("createdb"), *common, "dev"], env=env, check=True)

    print(f"Redshift stand-in ready: host=localhost port={args.port} dbname=dev user=awsuser")
    print(f'psql: "{tool("psql")}" -h localhost -p {args.port} -U awsuser -d dev')
    return 0


if __name__ == "__main__":
    sys.exit(main())
