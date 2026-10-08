"""Harbor agent: one call to the local model wrapper; its exact answer is the submission."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from harbor.agents.base import BaseAgent

from sapi_config_lab.evidence import write_json
from sapi_config_lab.harbor_integration.model_wrapper import request_wrapper
from sapi_config_lab.wrapper_audit import reported_model, reported_tokens, stderr_sha256, tool_markers

MAX_ANSWER_CHARACTERS = 100_000


class WrapperYamlAgent(BaseAgent):
    def __init__(self, *args, upstream: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.upstream = upstream

    @staticmethod
    def name():
        return "existing-wrapper-yaml"

    def version(self):
        return "1.0"

    async def setup(self, environment):
        pass

    def request(self, prompt):
        raw = request_wrapper(self.upstream, prompt, 195, 2_000_000)
        if len(raw) > 2_000_000:
            raise RuntimeError("Wrapper response exceeded size limit")
        return json.loads(raw)

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "prompt.txt").write_text(instruction)
        record = {
            "status": "generation_error",
            "provider": "existing-codex-exec-wrapper",
            "prompt_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
            "generation_calls": 1,
            "repairs": 0,
            "runtime_llm_mode": "stub",
        }
        started = time.monotonic()
        try:
            record["failure_reason"] = "wrapper_transport_or_response"
            wrapper = await asyncio.to_thread(self.request, instruction)
            if not isinstance(wrapper, dict):
                raise RuntimeError("Wrapper response was not an object")
            record.update(audit_stderr(wrapper.get("stderr", "")))
            if wrapper.get("ok") is not True or wrapper.get("exit_code") != 0:
                record["failure_reason"] = "wrapper_unsuccessful"
                raise RuntimeError("Existing wrapper did not complete successfully")
            if record["observed_tool_markers"]:
                record["failure_reason"] = "observed_tool_use"
                raise RuntimeError("Tool use observed: attempt is not prompt-only")
            answer = wrapper.get("output")
            if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_ANSWER_CHARACTERS:
                record["failure_reason"] = "empty_or_oversized_generation"
                raise RuntimeError("Empty or oversized generation")
            # No YAML rewriting, fence stripping, compilation feedback or repair.
            submission = self.logs_dir / "submission.yaml"
            submission.write_text(answer)
            record["submission_sha256"] = hashlib.sha256(submission.read_bytes()).hexdigest()
            record["failure_reason"] = "submission_upload"
            await environment.upload_file(source_path=submission, target_path="/app/submission/config.yaml")
            record["status"] = "submitted"
            record["failure_reason"] = None
        except Exception as error:  # Harbor boundary; external text may carry model output
            # Only the type is kept.
            record["error_type"] = type(error).__name__
            raise RuntimeError("YAML generation failed; see agent/generation.json") from None
        finally:
            record["duration_seconds"] = round(time.monotonic() - started, 3)
            write_json(self.logs_dir / "generation.json", record)
            context.metadata = record


def audit_stderr(stderr: str) -> dict:
    """Limited CLI metadata; wrapper stderr itself is never persisted."""
    return {
        "model": reported_model(stderr),
        "cli_reported_tokens": reported_tokens(stderr),
        "observed_tool_markers": tool_markers(stderr),
        "stderr_sha256": stderr_sha256(stderr),
        "tool_isolation": "prompt restriction plus stderr audit; not enforced by wrapper",
    }
