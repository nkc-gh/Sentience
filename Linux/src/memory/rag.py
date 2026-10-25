"""Chat-scoped document embeddings and local retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from computer.documents import extract_pages


def _root(provider: Any) -> str:
    base = (provider.base_url or "").rstrip("/")
    for suffix in ("/embeddings", "/chat/completions", "/v1"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    name = provider.name.casefold()
    if base:
        return base
    defaults = {
        "openai": "https://api.openai.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "together": "https://api.together.xyz/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "mistral": "https://api.mistral.ai/v1",
        "deepseek": "https://api.deepseek.com/v1",
    }
    return next((url for key, url in defaults.items() if key in name), "")


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Embedding request failed: {exc}") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise RuntimeError(f"Embedding API HTTP {exc.code}: {detail}") from exc


def embed_texts(provider: Any, model_id: str, texts: list[str]) -> list[list[float]]:
    """Embed text batches through Ollama or an OpenAI-compatible embeddings API."""
    if not texts:
        return []
    name = provider.name.casefold()
    headers = {"User-Agent": "Sentience/1.0"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    if provider.kind == "local" and "ollama" in name:
        root = _root(provider) or (provider.base_url or "http://localhost:11434").rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        data = _post_json(f"{root}/api/embed", {"model": model_id, "input": texts}, headers)
        vectors = data.get("embeddings")
    else:
        root = _root(provider)
        if not root:
            raise RuntimeError(f"Provider '{provider.name}' has no supported embeddings endpoint.")
        data = _post_json(f"{root}/embeddings", {"model": model_id, "input": texts}, headers)
        entries = data.get("data", [])
        entries = sorted(entries, key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in entries]
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise RuntimeError("Embedding provider returned a different number of vectors than requested.")
    result = []
    for vector in vectors:
        if not isinstance(vector, list) or not vector or any(not isinstance(value, (int, float)) for value in vector):
            raise RuntimeError("Embedding provider returned an invalid vector.")
        result.append([float(value) for value in vector])
    return result


def _chunks(page_text: str, size: int = 1100, overlap: int = 160) -> list[str]:
    text = "\n".join(line.rstrip() for line in page_text.splitlines()).strip()
    if not text:
        return []
    result = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            result.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return result


def index_file(db: Any, providers: Any, chat_id: int, file_path: str) -> tuple[int, str]:
    path = Path(file_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("/add expects a file path.")
    if path.stat().st_size > 25_000_000:
        raise ValueError("The file is larger than the 25 MB chat-index limit.")
    pages = extract_pages(str(path))
    if not pages:
        raise ValueError("Sentience could not extract text from this PDF. Check that the PDF is readable and OCR dependencies are installed for scanned pages.")
    staged = []
    for page_number, text in pages:
        for chunk in _chunks(text):
            staged.append((page_number, chunk))
            if len(staged) > 4000:
                raise ValueError("This document creates more than 4,000 passages; split it into smaller files before adding.")
    if not staged:
        raise ValueError("The file contains no searchable text.")
    candidates = providers.get_models_for_task("embedding")
    if not candidates:
        raise ValueError("No embedding model is assigned for Embedding under its current scope. Configure it with 'routing'.")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    failures = []
    model_candidates = list(candidates)
    model_candidates.extend(
        model for model in providers.get_fallback_models_for_task("embedding", candidates[0].id)
        if model.id not in {candidate.id for candidate in candidates}
    )
    for model in model_candidates:
        provider = next((item for item in providers.get_providers() if item.name.casefold() == model.provider.casefold()), None)
        if not provider:
            continue
        try:
            vectors: list[list[float]] = []
            batch_size = 32
            for offset in range(0, len(staged), batch_size):
                vectors.extend(embed_texts(provider, model.model_id, [text for _, text in staged[offset:offset + batch_size]]))
            rows = []
            for index, ((page_number, text), vector) in enumerate(zip(staged, vectors)):
                rows.append({
                    "source_name": path.name,
                    "page_number": page_number,
                    "chunk_index": index,
                    "content": text,
                    "embedding": vector,
                    "embedding_model_id": model.id,
                    "content_hash": content_hash,
                })
            db.replace_chat_document_chunks(chat_id, str(path), rows)
            return len(rows), model.model_id
        except Exception as exc:
            failures.append(f"{model.provider}/{model.model_id}: {exc}")
    raise RuntimeError("No configured embedding model could index this file:\n" + "\n".join(failures))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else -1.0


def retrieve_context(db: Any, providers: Any, chat_id: int, query: str, limit: int = 5) -> str:
    """Retrieve top passages, embedding the query with each indexed model."""
    chunks = db.get_chat_document_chunks(chat_id)
    if not chunks:
        return ""
    allowed = {model.id: model for model in providers.get_models_for_task("embedding")}
    grouped: dict[str, list[dict]] = {}
    for chunk in chunks:
        model_id = chunk["embedding_model_id"]
        if model_id in allowed:
            grouped.setdefault(model_id, []).append(chunk)
    scored = []
    for internal_model_id, items in grouped.items():
        model = allowed[internal_model_id]
        provider = next((item for item in providers.get_providers() if item.name.casefold() == model.provider.casefold()), None)
        if not provider:
            continue
        try:
            query_vector = embed_texts(provider, model.model_id, [query])[0]
        except Exception:
            continue
        for chunk in items:
            score = _cosine(query_vector, chunk["embedding"])
            if score >= 0:
                scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    lines = []
    for score, chunk in scored[:limit]:
        page = f", page {chunk['page_number']}" if chunk.get("page_number") else ""
        lines.append(f"[Source: {chunk['source_name']}{page}; relevance {score:.3f}]\n{chunk['content']}")
    return "\n\n---\n\n".join(lines)[:12000]
