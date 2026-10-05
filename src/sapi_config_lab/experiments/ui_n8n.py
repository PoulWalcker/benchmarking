"""Import new inactive UI copies through n8n's supported Docker CLI."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class DockerUi:
    """No execution, credential access, or update operation is exposed here."""

    def __init__(self, container: str = "n8n-n8n-1"):
        self.container = container
        identity = json.loads(self._command("inspect", "--format", "{{json .Id}}", container))
        if not isinstance(identity, str) or not identity:
            raise ValueError("Could not identify the n8n container")
        self.identity = identity

    @staticmethod
    def _command(*arguments: str) -> str:
        result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=90)
        if result.returncode:
            # Docker/n8n output can contain private configuration; keep it out of
            # the ordinary UI error message.
            raise ValueError("Docker/n8n command failed. Check that the local n8n container is running.")
        return result.stdout

    @contextmanager
    def _temporary_directory(self):
        directory = self._command("exec", self.identity, "mktemp", "-d", "/tmp/sapi-ui-XXXXXX").strip()
        if not directory.startswith("/tmp/sapi-ui-") or "/" in directory[len("/tmp/sapi-ui-") :]:
            raise ValueError("Unexpected n8n temporary directory")
        try:
            yield directory
        finally:
            self._command("exec", self.identity, "rm", "-rf", directory)

    def workflows(self) -> dict[str, dict]:
        with self._temporary_directory() as directory:
            path = directory + "/workflows.json"
            self._command("exec", self.identity, "n8n", "export:workflow", "--all", "--output=" + path)
            rows = json.loads(self._command("exec", self.identity, "cat", path))
        if not isinstance(rows, list) or any(not isinstance(row, dict) or "id" not in row for row in rows):
            raise ValueError("Unexpected n8n workflow export")
        return {row["id"]: row for row in rows}

    def import_new(self, path: Path, *, existing: dict[str, dict]) -> dict:
        workflow = json.loads(path.read_text())
        if workflow.get("active") is not False or workflow["id"] in existing:
            raise ValueError("Refusing to import an active graph or overwrite an existing workflow")
        with self._temporary_directory() as directory:
            target = directory + "/workflow.json"
            self._command("cp", str(path.resolve()), self.identity + ":" + target)
            self._command("exec", self.identity, "n8n", "import:workflow", "--input=" + target)
        after = self.workflows()
        if set(after) != set(existing) | {workflow["id"]} or any(after[key] != row for key, row in existing.items()):
            raise ValueError("Workflow inventory changed unexpectedly; inspect the preserved UI artifacts")
        imported = after[workflow["id"]]
        if any(imported.get(key) != workflow[key] for key in ("name", "active", "nodes", "connections", "settings")):
            raise ValueError("Imported graph differs from the prepared graph")
        return imported
