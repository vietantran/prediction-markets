"""Download selected markets' books, price history and public trades."""
import sys
from pmresearch.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["collect", *sys.argv[1:]]))
