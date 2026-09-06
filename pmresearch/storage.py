"""Timestamped run directories, raw JSONL, normalized CSV/JSONL and explicit manifests."""
from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path

from .http import utc_now


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=str, allow_nan=False) + "\n")


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_csv(path, rows):
    rows = list(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {}
            for key, value in row.items():
                if isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False)
                # Neutralize untrusted text when opened in Excel.
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                    value = "'" + value
                out[key] = value
            writer.writerow(out)


class RunStore:
    def __init__(self, root="data", command="collect", settings=None):
        self.started_at = utc_now()
        run_id = self.started_at.replace(":", "").replace(".", "_") + "_" + uuid.uuid4().hex[:6]
        self.path = Path(root).resolve() / run_id
        self.path.mkdir(parents=True)
        self.command = command
        self.settings = settings or {}
        self.request_count = 0
        self.errors = []
        self.warnings = []
        self._raw = (self.path / "raw_responses.jsonl").open("w", encoding="utf-8")
        self.finish(status="running")

    def record(self, response):
        self._raw.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
        self._raw.flush()
        self.request_count += 1

    def error(self, operation, error):
        self.errors.append({"operation": operation, "error": str(error), "observed_at": utc_now()})

    def finish(self, status=None, **extra):
        status = status or ("partial" if self.errors or self.warnings else "complete_within_requested_scope")
        manifest = {"schema_version": 1, "command": self.command, "started_at": self.started_at,
                    "finished_at": None if status == "running" else utc_now(), "status": status,
                    "settings": self.settings, "request_count": self.request_count,
                    "errors": self.errors, "warnings": self.warnings, **extra}
        write_json(self.path / "manifest.json", manifest)
        return manifest

    def close(self):
        self._raw.close()
