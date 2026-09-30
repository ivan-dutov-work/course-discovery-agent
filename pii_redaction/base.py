from __future__ import annotations

from typing import Protocol


class RedactionError(RuntimeError):
    pass


class Redactor(Protocol):
    def redact(self, text: str) -> str:
        """Return `text` with PII replaced by `<ENTITY_TYPE>` placeholders.

        Raises RedactionError when detection cannot run.
        """
        ...
