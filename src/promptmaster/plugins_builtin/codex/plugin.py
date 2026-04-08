from promptmaster.plugin_api.v1 import PromptMasterPlugin
from promptmaster.providers.codex import CodexAdapter

plugin = PromptMasterPlugin(
    name="codex",
    capabilities=("provider",),
    providers={"codex": CodexAdapter},
)
