"""Raw finite-duration WebSocket capture with heartbeat and reconnect handling."""
import sys
from pmresearch.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["stream", *sys.argv[1:]]))
