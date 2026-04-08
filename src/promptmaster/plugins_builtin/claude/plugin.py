from promptmaster.plugin_api.v1 import PromptMasterPlugin
from promptmaster.providers.claude import ClaudeAdapter

plugin = PromptMasterPlugin(
    name="claude",
    capabilities=("provider",),
    providers={"claude": ClaudeAdapter},
)
