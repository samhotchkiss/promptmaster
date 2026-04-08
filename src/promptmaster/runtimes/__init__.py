from pathlib import Path

from promptmaster.models import RuntimeKind
from promptmaster.plugin_host import extension_host_for_root
from promptmaster.runtimes.base import RuntimeAdapter


def get_runtime(runtime: RuntimeKind, *, root_dir: Path | None = None) -> RuntimeAdapter:
    root = str((root_dir or Path.cwd()).resolve())
    return extension_host_for_root(root).get_runtime(runtime.value)
