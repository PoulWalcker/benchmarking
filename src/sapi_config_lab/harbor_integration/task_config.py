"""Validate the protected YAML transfer and separate-verifier Harbor configuration."""

from importlib.metadata import version

from harbor.models.task.config import TaskConfig

ARTIFACTS = [
    {"source": "/logs/artifacts", "destination": "discarded-convention", "exclude": ["*"]},
    {"source": "/submission/config.yaml", "destination": "submission/config.yaml"},
]
COLLECT = [{"command": "python3 /opt/sapi-submission.py collect", "user": "root", "timeout_sec": 10.0}]


def validate_config(text: str) -> TaskConfig:
    """Validate native Harbor settings and the supported Docker YAML transfer profile."""
    if version("harbor") != "0.21.0":
        raise ValueError("Native tasks require Harbor 0.21.0")
    config = TaskConfig.model_validate_toml(text)
    if config.steps or config.environment.os != "linux":
        raise ValueError("This native profile requires single-step Linux tasks")
    if config.verifier.environment is None or config.verifier.environment_mode == "shared":
        raise ValueError("An explicit separate verifier environment is required")
    for environment in (config.environment, config.verifier.environment):
        if environment.storage_mb is not None or environment.gpus or environment.gpu_types or environment.tpu:
            raise ValueError("Docker supports CPU and memory limits, not disk, GPU or TPU requirements")
        if environment.os != "linux":
            raise ValueError("Both environments must use Linux")
        if environment.docker_image or environment.env or environment.mcp_servers or environment.skills_dir:
            raise ValueError("Prebuilt images, environment injection and external agent inputs are not supported")
    if config.agent.user != "1000":
        raise ValueError("Author must run as unprivileged user 1000")
    if config.verifier.user not in (None, "root", "0", 0):
        raise ValueError("Verifier must be able to read the protected submission")
    if config.solution.env or config.verifier.env:
        raise ValueError("Credential injection is outside this native profile")
    actual = [
        item.model_dump(exclude_defaults=True) if not isinstance(item, str) else item for item in config.artifacts
    ]
    if actual != ARTIFACTS:
        raise ValueError("Declare only the protected YAML and excluded conventional artifact directory")
    hooks = [item.model_dump(exclude_defaults=True) for item in config.verifier.collect]
    if hooks != COLLECT:
        raise ValueError("Declare the root-owned YAML collection hook")
    return config
