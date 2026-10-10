"""Harbor agent: one call to the local model wrapper; its exact answer is the submission."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import time
from urllib.error import URLError

from harbor.agents.base import BaseAgent

from sapi_config_lab.evidence import durable_json, write_json
from sapi_config_lab.execute.agency import strict_json
from sapi_config_lab.harbor_integration.model_wrapper import request_wrapper
from sapi_config_lab.wrapper_audit import reported_model, reported_tokens, stderr_sha256, tool_markers

MAX_ANSWER_CHARACTERS = 100_000


class WrapperYamlAgent(BaseAgent):
    def __init__(
        self,
        *args,
        upstream: str,
        expected_model: str,
        prompt_path: str | None = None,
        prompt_sha256: str | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.upstream = upstream
        self.expected_model = expected_model
        self.prompt_path = Path(prompt_path) if prompt_path is not None else None
        self.prompt_sha256 = prompt_sha256

    @staticmethod
    def name():
        return "existing-wrapper-yaml"

    def version(self):
        return "1.0"

    async def setup(self, environment):
        pass

    def request(self, prompt):
        return request_wrapper(self.upstream, prompt, 195, 2_000_000)

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "status": "generation_error",
            "provider": "existing-codex-exec-wrapper",
            "prompt_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
            "generation_calls": 1,
            "repairs": 0,
            "runtime_llm_mode": "stub",
            "expected_model": self.expected_model,
            "model_outcome": "not_dispatched",
            "outcome_reason": None,
        }
        started = time.monotonic()
        try:
            if self.prompt_path is not None:
                raw = self.prompt_path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != self.prompt_sha256:
                    raise ValueError("Frozen authoring prompt differs")
                instruction = raw.decode("utf-8")
            (self.logs_dir / "prompt.txt").write_bytes(instruction.encode())
            record["prompt_sha256"] = hashlib.sha256(instruction.encode()).hexdigest()
            record["failure_reason"] = "wrapper_transport_or_response"
            record.update(model_outcome="unknown", outcome_reason="dispatch_unsettled")
            try:
                durable_json(self.logs_dir / "generation.json", record)
            except OSError:
                record.update(model_outcome="not_dispatched", outcome_reason=None)
                raise
            record["outcome_reason"] = "transport_error"
            raw = await asyncio.to_thread(self.request, instruction)
            record["outcome_reason"] = "incomplete_receipt"
            if len(raw) > 2_000_000:
                raise RuntimeError("Wrapper response exceeded size limit")
            wrapper = strict_json(raw)
            if not isinstance(wrapper, dict):
                raise RuntimeError("Wrapper response was not an object")
            record["outcome_reason"] = "wrapper_unsettled"
            if wrapper.get("ok") is not True or type(wrapper.get("exit_code")) is not int or wrapper["exit_code"] != 0:
                record["failure_reason"] = "wrapper_unsuccessful"
                record.update(audit_stderr(wrapper.get("stderr", "")))
                raise RuntimeError("Existing wrapper did not complete successfully")
            record.update(model_outcome="settled", outcome_reason=None)
            record.update(audit_stderr(wrapper.get("stderr", "")))
            if record["model"] is None:
                record["failure_reason"] = "model_identity_unverified"
                raise RuntimeError("Authoring model identity could not be verified")
            if record["model"] != self.expected_model:
                record["failure_reason"] = "model_identity_mismatch"
                raise RuntimeError("Authoring model identity differs")
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
        except asyncio.CancelledError:
            if record["model_outcome"] == "unknown":
                record["outcome_reason"] = "cancelled"
            raise
        except Exception as error:  # Harbor boundary; external text may carry model output
            if record["model_outcome"] == "unknown" and (
                isinstance(error, TimeoutError)
                or (isinstance(error, URLError) and isinstance(error.reason, TimeoutError))
            ):
                record["outcome_reason"] = "timeout"
            # Only the type is kept.
            record["error_type"] = type(error).__name__
            raise RuntimeError("YAML generation failed; see agent/generation.json") from None
        finally:
            record["duration_seconds"] = round(time.monotonic() - started, 3)
            durable_json(self.logs_dir / "generation.json", record)
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


class ReplayYamlAgent(BaseAgent):
    """Upload one exact recorded answer without authoring, commands or repair."""

    def __init__(self, *args, submission_path: str, submission_sha256: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.submission_path = Path(submission_path)
        self.submission_sha256 = submission_sha256

    @staticmethod
    def name():
        return "recorded-yaml"

    def version(self):
        return "1.0"

    async def setup(self, environment):
        pass

    async def run(self, instruction, environment, context):
        raw = self.submission_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.submission_sha256:
            raise ValueError("Selected submission differs")
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        source = self.logs_dir / "submission.yaml"
        source.write_bytes(raw)
        await environment.upload_file(source_path=source, target_path="/app/submission/config.yaml")
        record = {
            "status": "submitted",
            "generation_calls": 0,
            "repairs": 0,
            "submission_sha256": self.submission_sha256,
        }
        write_json(self.logs_dir / "replay.json", record)
        context.metadata = record
