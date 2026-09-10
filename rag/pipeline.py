from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import torch
from sentence_transformers import SentenceTransformer
from sentence_transformers.util import semantic_search

from config import CHUNK_OVERLAP, CHUNK_SIZE, EMBEDDING_MODEL, TOP_K
from docs.loaders import ExtractedBlock

_embedder: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _embedder = SentenceTransformer(EMBEDDING_MODEL, device=device)
    return _embedder


@dataclass
class Chunk:
    text: str
    source: str
    location: str


@dataclass
class IndexedFile:
    path: Path
    name: str
    chunks: list[Chunk] = field(default_factory=list)


@dataclass
class Hit:
    chunk: Chunk
    score: float


def chunk_blocks(blocks: list[ExtractedBlock], source: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for block in blocks:
        text = " ".join(block.text.split())
        if not text:
            continue
        if len(text) <= CHUNK_SIZE:
            chunks.append(Chunk(text=text, source=source, location=block.location))
            continue
        start = 0
        part = 1
        overlap = min(CHUNK_OVERLAP, CHUNK_SIZE - 1)
        while start < len(text):
            end = min(start + CHUNK_SIZE, len(text))
            piece = text[start:end].strip()
            if piece:
                loc = f"{block.location}, часть {part}"
                chunks.append(Chunk(text=piece, source=source, location=loc))
                part += 1
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks


class DocumentIndex:
    def __init__(self) -> None:
        self.files: list[IndexedFile] = []
        self._chunks: list[Chunk] = []
        self._embeddings = None

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.files]

    def add_file(self, path: Path, chunks: list[Chunk]) -> None:
        self.files = [f for f in self.files if f.path != path]
        self.files.append(IndexedFile(path=path, name=path.name, chunks=chunks))
        self._rebuild()

    def _rebuild(self) -> None:
        self._chunks = [c for f in self.files for c in f.chunks]
        if not self._chunks:
            self._embeddings = None
            return
        model = get_embedder()
        texts = [c.text for c in self._chunks]
        self._embeddings = model.encode(
            texts,
            convert_to_tensor=True,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

    def search(self, query: str, top_k: int = TOP_K) -> list[Hit]:
        if not self._chunks or self._embeddings is None:
            return []
        model = get_embedder()
        q = model.encode(
            query,
            convert_to_tensor=True,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        corpus = self._embeddings
        if corpus.ndim == 1:
            corpus = corpus.unsqueeze(0)
        hits = semantic_search(q, corpus, top_k=min(top_k, len(self._chunks)))[0]
        result: list[Hit] = []
        for item in hits:
            idx = int(item["corpus_id"])
            result.append(Hit(chunk=self._chunks[idx], score=float(item["score"])))
        return result

    def preview_chunks(self, limit: int = 18) -> list[Chunk]:
        if not self._chunks:
            return []
        if len(self._chunks) <= limit:
            return list(self._chunks)
        step = max(1, len(self._chunks) // limit)
        picked = self._chunks[::step][:limit]
        return picked
