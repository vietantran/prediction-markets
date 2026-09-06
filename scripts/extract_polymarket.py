"""Polymarket discovery. Example: python scripts/extract_polymarket.py --query '2026 Senate'."""
import sys
from pmresearch.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["discover", "--platform", "polymarket", *sys.argv[1:]]))
