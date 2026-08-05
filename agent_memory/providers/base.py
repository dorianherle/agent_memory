import os
from importlib import import_module
from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def fingerprint(self) -> str:
        """A value that changes when the model or vector size changes."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Create one vector for each stored message."""

    def embed_query(self, text: str) -> list[float]:
        """Create one vector for a search query."""


def load_provider() -> EmbeddingProvider:
    target = os.getenv("AGENT_MEMORY_PROVIDER")
    if not target:
        raise ValueError(
            "semantic search needs AGENT_MEMORY_PROVIDER=module:ProviderClass"
        )
    module_name, class_name = target.split(":", 1)
    provider_class = getattr(import_module(module_name), class_name)
    return provider_class()
