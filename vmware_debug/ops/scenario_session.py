"""Metadata-only, in-process host accounting across several skill backends.

Not a distributed spend cap, audit store, authorization boundary or model runner.
"""

from __future__ import annotations

import re
import threading
import uuid


class SessionStopped(ValueError):
    """A call cannot start under this host session's declared scope/budget."""


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value)


class ScenarioSession:
    """A trusted host creates one session and uses it across its skill calls.

    Attempts are counted before dispatch; failed or cancelled calls are never
    refunded. Restarting the process loses this budget. No file/network writes.
    """

    def __init__(self, capabilities: set[tuple[str, str]], max_calls: int):
        if type(max_calls) is not int or max_calls <= 0 or not capabilities:
            raise ValueError("A positive call budget and explicit capabilities are required")
        if any(not _identifier(b) or not _identifier(t) for b, t in capabilities):
            raise ValueError("Backend/tool identifiers required")
        self.trace_id = uuid.uuid4().hex
        self._capabilities = frozenset(capabilities)
        self._max_calls = max_calls
        self._steps = []
        self._lock = threading.Lock()

    def begin(self, backend: str, tool: str) -> str:
        with self._lock:
            if (backend, tool) not in self._capabilities:
                raise SessionStopped("Call outside the selected scenario capabilities")
            if len(self._steps) >= self._max_calls:
                raise SessionStopped("Task call budget exhausted; report incomplete coverage")
            step = f"{self.trace_id}:{len(self._steps) + 1}"
            self._steps.append(
                {
                    "step_id": step,
                    "backend": backend,
                    "tool": tool,
                    "state": "started",
                    "input_tokens": None,
                    "output_tokens": None,
                }
            )
            return step

    def finish(self, step_id: str, state: str, input_tokens=None, output_tokens=None):
        if state not in {"ok", "error", "denied", "unknown"}:
            raise ValueError("Explicit bounded outcome required")
        for value in (input_tokens, output_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Token usage is nonnegative integer or unmeasured")
        with self._lock:
            row = next((r for r in self._steps if r["step_id"] == step_id), None)
            if row is None or row["state"] != "started":
                raise SessionStopped("Unknown or already completed step")
            row.update(state=state, input_tokens=input_tokens, output_tokens=output_tokens)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "trace_id": self.trace_id,
                "scope": "in-process host coordinator",
                "attempted_calls": len(self._steps),
                "max_calls": self._max_calls,
                "steps": [dict(row) for row in self._steps],
                "audit": "Separate from authoritative backend operation audit",
            }
