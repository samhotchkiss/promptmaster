from promptmaster.models import ProviderKind
from promptmaster.providers.base import ProviderAdapter
from promptmaster.providers.claude import ClaudeAdapter
from promptmaster.providers.codex import CodexAdapter


def get_provider(provider: ProviderKind) -> ProviderAdapter:
    if provider is ProviderKind.CLAUDE:
        return ClaudeAdapter()
    if provider is ProviderKind.CODEX:
        return CodexAdapter()
    raise ValueError(f"Unsupported provider: {provider}")
