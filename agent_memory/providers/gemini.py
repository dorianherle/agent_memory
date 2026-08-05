from google import genai


class GeminiProvider:
    def __init__(self, model: str = "gemini-embedding-001", dimensions: int = 1536) -> None:
        self.model = model
        self.dimensions = dimensions
        self.client = genai.Client()

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
                self._embed(texts[start:start + 100], "RETRIEVAL_DOCUMENT")
            )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]
