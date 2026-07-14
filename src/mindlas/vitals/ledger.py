"""Append-only JSONL event store, one file per session. Pure storage."""
from __future__ import annotations

from pathlib import Path

from .events import Event, event_from_json, event_to_json


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: Event) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(event_to_json(event) + "\n")

    def events(self) -> list[Event]:
        if not self.path.exists():
            return []
        out: list[Event] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(event_from_json(line))
                except (ValueError, KeyError):
                    # tolerate a partial/corrupt last line (crash mid-write)
                    continue
        return out

    def count_kind(self, kind: str) -> int:
        return sum(1 for e in self.events() if e.kind == kind)
