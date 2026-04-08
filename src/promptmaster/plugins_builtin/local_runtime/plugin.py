from promptmaster.plugin_api.v1 import PromptMasterPlugin
from promptmaster.runtimes.local import LocalRuntimeAdapter

plugin = PromptMasterPlugin(
    name="local_runtime",
    capabilities=("runtime",),
    runtimes={"local": LocalRuntimeAdapter},
)
