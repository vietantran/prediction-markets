"""Kalshi discovery. Example: python scripts/extract_kalshi.py --topic fed_monetary_policy."""
import sys
from pmresearch.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["discover", "--platform", "kalshi", *sys.argv[1:]]))
