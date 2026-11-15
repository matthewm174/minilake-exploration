#!/usr/bin/env python3
"""Render every docs/diagrams/*.html to docs/img/<name>.png with headless Chrome, Chromium or Edge.

Each page declares its canvas size in <meta name="viewport-size" content="WIDTHxHEIGHT">.

    python docs/diagrams/render.py              # all diagrams
    python docs/diagrams/render.py 05-lineage   # one diagram
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "img"


def find_browser() -> str:
    candidates = []
    if os.name == "nt":
        for base in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                                  os.environ.get("LOCALAPPDATA"))):
            candidates += [Path(base, "Google", "Chrome", "Application", "chrome.exe"),
                           Path(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                       Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                       Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "microsoft-edge", "microsoft-edge-stable", "chrome", "msedge"):
        exe = shutil.which(name)
        if exe:
            return exe
    sys.exit("Chrome, Chromium or Edge is required to render the diagrams.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="?", help="render just this diagram (file name without .html)")
    args = ap.parse_args()

    browser = find_browser()
    OUT.mkdir(parents=True, exist_ok=True)
    pages = [p for p in sorted(HERE.glob("*.html")) if not args.only or p.stem == args.only]
    if not pages:
        sys.exit(f"no diagram named {args.only!r}")

    with tempfile.TemporaryDirectory(prefix="minilake-diagrams-") as user_data:
        for page in pages:
            m = re.search(r'name="viewport-size" content="(\d+)x(\d+)"', page.read_text(encoding="utf-8"))
            if not m:
                sys.exit(f"{page.name} has no viewport-size meta tag")
            png = OUT / f"{page.stem}.png"
            png.unlink(missing_ok=True)
            result = subprocess.run(
                [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                 "--force-device-scale-factor=1", f"--user-data-dir={user_data}",
                 f"--window-size={m[1]},{m[2]}", f"--screenshot={png}", page.as_uri()],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not png.is_file():
                sys.exit(f"render failed for {page.name} (exit {result.returncode})")
            print(f"  {page.name} -> docs/img/{png.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
