from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass
class Progress:
    title: str
    total: int = 100
    width: int = 36
    value: int = 0

    def show(self, value: int | None = None, label: str = "") -> None:
        if value is not None:
            self.value = max(0, min(self.total, int(value)))
        ratio = self.value / self.total if self.total else 1.0
        filled = round(self.width * ratio)
        bar = "#" * filled + "-" * (self.width - filled)
        suffix = f"  {label}" if label else ""
        print(f"\r{self.title} [{bar}] {self.value:3d}%{suffix}", end="", flush=True)
        if self.value >= self.total:
            print()

    def step(self, value: int, label: str = "") -> None:
        self.show(value, label)

    def fail(self, label: str) -> None:
        print()
        print(f"STOPPED: {label}", file=sys.stderr)
