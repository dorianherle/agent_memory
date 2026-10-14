from __future__ import annotations

import os

from google import genai


class GeminiProvider:
    """Gemini embeddings for ``memory_find(query=...)``.

    Reads ``GEMINI_API_KEY`` from the environment (set by
    ``agent-memory cursor install`` into MCP env +
    ``.agent_memory/credentials.env``).
    """

    def __init__(
        self,
        model: str = "gemini-embedding-001",
        dimensions: int = 1536,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip() or None
        if not key:
            raise ValueError(
                "GEMINI_API_KEY is not set "
                "(expected in .agent_memory/credentials.env or project .env)"
            )
        self.client = genai.Client(api_key=key)

    @property
    def fingerprint(self) -> str:
        return f"gemini:{self.model}:{self.dimensions}"

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        response = self.client.models.embed_content(
            model=self.model,
            contents=texts,
            config={
                "task_type": task,
                "output_dimensionality": self.dimensions,
            },
        )
        return [embedding.values for embedding in response.embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), 100):
            vectors.extend(
                self._embed(texts[start : start + 100], "RETRIEVAL_DOCUMENT")
            )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]
