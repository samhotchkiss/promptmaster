from promptmaster.models import RuntimeKind
from promptmaster.runtimes.base import RuntimeAdapter
from promptmaster.runtimes.docker import DockerRuntimeAdapter
from promptmaster.runtimes.local import LocalRuntimeAdapter


def get_runtime(runtime: RuntimeKind) -> RuntimeAdapter:
    if runtime is RuntimeKind.LOCAL:
        return LocalRuntimeAdapter()
    if runtime is RuntimeKind.DOCKER:
        return DockerRuntimeAdapter()
    raise ValueError(f"Unsupported runtime: {runtime}")
