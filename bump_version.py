#!/usr/bin/env python3
"""
Bump APP_VERSION in search.py.  Format: Major.YYMMDD.Minor
  YYMMDD = date of the build  (e.g. 260510 for 2026-05-10)
  Minor  = per-day increment  (resets to 0 on a new date)

Usage:
    python bump_version.py           # increment Minor for today
    python bump_version.py 2         # set Major=2 (resets Minor to 0)
"""
import re, sys
from datetime import date
from pathlib import Path

TARGET = Path(__file__).parent / "search.py"

def bump():
    content = TARGET.read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', content)
    if not m:
        print("ERROR: APP_VERSION not found in search.py"); sys.exit(1)

    cur_major, cur_date, cur_minor = int(m.group(1)), int(m.group(2)), int(m.group(3))

    today = date.today()
    yymmdd = int(f"{today.year % 100:02d}{today.month:02d}{today.day:02d}")

    major = int(sys.argv[1]) if len(sys.argv) >= 2 else cur_major

    # New date → reset Minor; same date → increment Minor
    if major != cur_major or yymmdd != cur_date:
        minor = 0
    else:
        minor = cur_minor + 1

    old_str = m.group(0)
    new_str = f'APP_VERSION  = "{major}.{yymmdd}.{minor}"   # Major.YYMMDD.Minor'

    if old_str == new_str:
        print(f"Version already at {major}.{yymmdd}.{minor} — no change needed.")
        return

    content = content[:m.start()] + new_str + content[m.end():]
    TARGET.write_text(content, encoding="utf-8")
    print(f"✓ Bumped to {major}.{yymmdd}.{minor}")

if __name__ == "__main__":
    bump()
