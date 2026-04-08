from promptmaster.plugin_api.v1 import PromptMasterPlugin
from promptmaster.runtimes.docker import DockerRuntimeAdapter

plugin = PromptMasterPlugin(
    name="docker_runtime",
    capabilities=("runtime",),
    runtimes={"docker": DockerRuntimeAdapter},
)
