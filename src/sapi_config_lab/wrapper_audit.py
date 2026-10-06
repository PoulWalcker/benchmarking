"""What the local model wrapper's CLI stderr reveals. Observational only: the wrapper does not disable tools."""

from __future__ import annotations

import hashlib
import re

TOOL_MARKERS = {
    "shell": r"(?m)^exec(?:\s|$)",
    "tool": r"(?m)^tool\s+",
    "file_edit": r"(?m)^(?:file update|apply_patch)(?:\s|$)",
    "web": r"(?mi)^(?:web search|searching the web|searched the web)(?:\s|$)",
}


def reported_model(stderr: str) -> str | None:
    found = re.search(r"(?m)^model:\s*([A-Za-z0-9_.-]+)\s*$", stderr)
    return found.group(1) if found else None


def reported_tokens(stderr: str) -> int | None:
    found = re.search(r"(?m)^tokens used\s*\n([\d,]+)\s*$", stderr)
    return int(found.group(1).replace(",", "")) if found else None


def tool_markers(stderr: str) -> list[str]:
    return [name for name, pattern in TOOL_MARKERS.items() if re.search(pattern, stderr)]


def stderr_sha256(stderr: str) -> str:
    return hashlib.sha256(stderr.encode()).hexdigest()
