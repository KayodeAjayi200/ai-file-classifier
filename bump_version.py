#!/usr/bin/env python3
"""
Bump APP_VERSION in search.py using Microsoft versioning: Major.Minor.Build.Revision
  Build   = YYMM  (e.g. 2605 for May 2026)
  Revision = day  (e.g. 10)

Run this before committing/pushing a new release:
    python bump_version.py           # auto-dates today
    python bump_version.py 1 2       # bump Major.Minor manually
"""
import re, sys
from datetime import date
from pathlib import Path

TARGET = Path(__file__).parent / "search.py"

def bump():
    content = TARGET.read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)\.(\d+)"', content)
    if not m:
        print("ERROR: APP_VERSION line not found in search.py")
        sys.exit(1)

    major, minor = int(m.group(1)), int(m.group(2))

    # Allow manual Major.Minor override via CLI args
    if len(sys.argv) >= 3:
        major, minor = int(sys.argv[1]), int(sys.argv[2])
    elif len(sys.argv) == 2:
        minor = int(sys.argv[1])

    today = date.today()
    build    = int(f"{today.year % 100:02d}{today.month:02d}")   # YYMM
    revision = today.day

    old_ver = m.group(0)
    new_ver = f'APP_VERSION  = "{major}.{minor}.{build}.{revision}"   # Major.Minor.Build(YYMM).Revision(day)'

    if old_ver.split('"')[1] == f"{major}.{minor}.{build}.{revision}":
        print(f"Version already at {major}.{minor}.{build}.{revision} — no change needed.")
        return

    content = content[:m.start()] + new_ver + content[m.end():]
    TARGET.write_text(content, encoding="utf-8")
    print(f"✓ Bumped to {major}.{minor}.{build}.{revision}")

if __name__ == "__main__":
    bump()
