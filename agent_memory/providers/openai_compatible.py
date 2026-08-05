import json
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

BATCH_SIZE = 100
MAX_WORKERS = 8


class OpenAICompatibleProvider:
    def __init__(self) -> None:
        self.base_url = os.environ["AGENT_MEMORY_EMBEDDING_BASE_URL"].rstrip("/")
        self.model = os.environ["AGENT_MEMORY_EMBEDDING_MODEL"]
        self.api_key = os.getenv("AGENT_MEMORY_EMBEDDING_API_KEY", "EMPTY")
        self.query_instruction = os.getenv(
            "AGENT_MEMORY_QUERY_INSTRUCTION",
            "Given a question about past agent trajectories, retrieve relevant "
            "memory entries that help answer it.",
        )

    @property
    def fingerprint(self) -> str:
        return f"openai-compatible:{self.base_url}:{self.model}"

    def _embed(self, texts: list[str]) -> list[list[float]]:
        request = Request(
            f"{self.base_url}/embeddings",
            data=json.dumps({"model": self.model, "input": texts}).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request) as response:
            data = json.load(response)["data"]
        return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        batches = [
            texts[start:start + BATCH_SIZE]
            for start in range(0, len(texts), BATCH_SIZE)
        ]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            return [vector for batch in pool.map(self._embed, batches) for vector in batch]

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"{self.query_instruction}\n{text}"])[0]
