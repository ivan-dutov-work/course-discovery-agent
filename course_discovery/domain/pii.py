from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pii:
    subject: str
    redacted: bool = True
