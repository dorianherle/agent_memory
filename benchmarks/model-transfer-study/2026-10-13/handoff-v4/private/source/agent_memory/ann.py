import fcntl
import hashlib
import os
from pathlib import Path
import threading
from typing import Callable

import hnswlib
import numpy


class HnswIndex:
    def __init__(self, git_dir: Path, provider: str) -> None:
        name = hashlib.sha256(provider.encode()).hexdigest()[:16]
        self.path = git_dir / f"vectors-{name}.hnsw"
        self.index = None
        self.dimensions = 0
        self.labels: dict[int, str] = {}
        self.modified_ns = 0
        self.lock = threading.RLock()

    def update(
        self,
        labels: dict[int, str],
        dimensions: int,
        load_vectors: Callable[[list[int]], dict[int, numpy.ndarray]],
    ) -> None:
        if not labels:
            return
        lock_path = self.path.with_suffix(".lock")
        with self.lock, open(lock_path, "w") as file_lock:
            fcntl.flock(file_lock, fcntl.LOCK_EX)
            modified_ns = self.path.stat().st_mtime_ns if self.path.exists() else 0
            reload_index = (
                self.index is None
                or self.dimensions != dimensions
                or modified_ns != self.modified_ns
            )

            if reload_index:
                self.dimensions = dimensions
                self.index = hnswlib.Index(space="cosine", dim=dimensions)
                if self.path.exists():
                    try:
                        self.index.load_index(
                            str(self.path),
                            max_elements=max(100, len(labels) * 2),
                        )
                    except RuntimeError:
                        self.index = hnswlib.Index(space="cosine", dim=dimensions)
                        self.index.init_index(
                            max_elements=max(100, len(labels) * 2),
                            ef_construction=200,
                            M=16,
                        )
                else:
                    self.index.init_index(
                        max_elements=max(100, len(labels) * 2),
                        ef_construction=200,
                        M=16,
                    )

            indexed = set(self.index.get_ids_list())
            missing_labels = [label for label in labels if label not in indexed]
            if missing_labels:
                vectors = load_vectors(missing_labels)
                needed = len(indexed) + len(missing_labels)
                if needed > self.index.get_max_elements():
                    self.index.resize_index(max(needed * 2, 100))
                self.index.add_items(
                    numpy.stack([vectors[label] for label in missing_labels]),
                    numpy.asarray(missing_labels),
                )
                temporary = self.path.with_suffix(".tmp")
                self.index.save_index(str(temporary))
                os.replace(temporary, self.path)
            self.modified_ns = (
                self.path.stat().st_mtime_ns if self.path.exists() else 0
            )
            self.index.set_ef(min(max(100, len(labels)), 1000))
            self.labels = labels

    def search(self, query: numpy.ndarray, k: int) -> list[str]:
        with self.lock:
            if self.index is None or not self.labels:
                return []
            count = self.index.get_current_count()
            labels, _ = self.index.knn_query(query, k=min(k, count))
            return [
                self.labels[int(label)]
                for label in labels[0]
                if int(label) in self.labels
            ]
