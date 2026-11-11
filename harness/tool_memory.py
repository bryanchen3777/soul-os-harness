"""SA-4 harness-side tool memory (decision-complete).

Decision-complete scope (SA-4): this module stores tool details (paths /
param templates / raw results / failure experience) under the ``harness/``
namespace. The soul side MUST NOT read it: ``read_for_soul`` is a hard block
(always raises), and this module imports NO soul-side package code
(pinned by the SA-4 T4 test module).

Namespace + credential separation: every key must start with ``harness/``;
soul-namespace keys are refused at record time (fail-closed both directions,
so the two sides can never share a key).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

__all__ = [
    "HARNESS_NAMESPACE",
    "TOOL_MEMORY_VERSION",
    "ToolMemoryDenied",
    "ToolMemoryStore",
]

#: Tool-memory version (bumped only by a decision-complete ticket).
TOOL_MEMORY_VERSION = "sa4/v1"

#: The ONLY namespace this store serves. Soul keys are refused, not remapped.
HARNESS_NAMESPACE = "harness"


class ToolMemoryDenied(Exception):
    """Fail-closed refusal: wrong namespace, missing record, or soul-side read."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _require_harness_key(key) -> str:
    if not isinstance(key, str) or not key.startswith(HARNESS_NAMESPACE + "/"):
        raise ToolMemoryDenied(400, "invalid_request", "harness-namespace key required")
    return key


@dataclass
class ToolMemoryStore:
    """Harness-only store for tool details (no soul reader exists, by design)."""

    _records: dict = field(default_factory=dict)

    def record(
        self,
        key: str,
        *,
        path: str = "",
        params_template: str = "",
        outcome: str = "",
        failure_experience: str = "",
    ) -> str:
        """Record one tool detail bundle (all values plain str, else 400)."""
        _require_harness_key(key)
        bundle = {
            "path": path,
            "params_template": params_template,
            "outcome": outcome,
            "failure_experience": failure_experience,
        }
        for name, value in bundle.items():
            if not isinstance(value, str):
                raise ToolMemoryDenied(400, "invalid_request", "%s must be str" % (name,))
        self._records[key] = bundle
        return key

    def read_for_harness(self, key: str) -> dict:
        """Read one bundle (harness side only; returns a copy)."""
        _require_harness_key(key)
        try:
            bundle = self._records[key]
        except KeyError:
            raise ToolMemoryDenied(404, "not_found", "no such tool record") from None
        return dict(bundle)

    def read_for_soul(self, key: str) -> dict:
        """Soul-side read: HARD BLOCK (always raises, never returns content)."""
        raise ToolMemoryDenied(
            403, "forbidden", "soul side may not read harness tool memory"
        )

    def keys(self) -> tuple:
        """Stored keys in insertion order (all ``harness/``-prefixed)."""
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)
