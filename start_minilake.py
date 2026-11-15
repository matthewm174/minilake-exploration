#!/usr/bin/env python3
"""Start minilake with persistent Unity Catalog state rooted in this folder.

The embedded JupyterLab (UI > Notebooks) is on unless --no-notebook is given; it needs
the notebook extra: pip install "minilake[notebook]".

    python start_minilake.py                  # port 8000, notebooks on
    python start_minilake.py --port 8001 --no-notebook
"""
import argparse
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def find_minilake() -> str:
    exe = shutil.which("minilake")
    if exe:
        return exe
    # pip --user installs scripts into a folder that is often not on PATH.
    scheme = "nt_user" if os.name == "nt" else "posix_user"
    scripts = Path(sysconfig.get_path("scripts", scheme))
    for name in ("minilake.exe", "minilake"):
        if (scripts / name).is_file():
            return str(scripts / name)
    sys.exit("minilake not found. Run: pip install minilake")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-notebook", action="store_true", help="start without the embedded JupyterLab")
    args = ap.parse_args()

    env = os.environ.copy()
    env["MINILAKE_DATA_DIR"] = str(ROOT / "data")
    env["MINILAKE_PERSIST"] = "1"
    env["MINILAKE_NOTEBOOK"] = "0" if args.no_notebook else "1"

    if not args.no_notebook and importlib.util.find_spec("jupyterlab") is None:
        print('WARNING: jupyterlab is not installed, so Notebooks will be unavailable. '
              'Run: pip install "minilake[notebook]"', file=sys.stderr)

    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", args.port)) == 0:
            sys.exit(f"Port {args.port} is already in use; minilake is probably already running. "
                     f"Stop it with Ctrl+C in its terminal, or use --port.")

    exe = find_minilake()
    print(f"minilake data dir: {env['MINILAKE_DATA_DIR']}")
    print(f"API: http://localhost:{args.port}   SQL editor: http://localhost:{args.port}/ui/")
    if not args.no_notebook:
        print(f"Notebooks: http://localhost:{args.port}/ui/notebooks/  (files in {Path('data', 'notebooks')})")

    proc = subprocess.Popen([exe, "--port", str(args.port)], env=env)
    # Ctrl+C reaches minilake too; keep waiting so it can save the UC registry on shutdown.
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            continue


if __name__ == "__main__":
    sys.exit(main())
