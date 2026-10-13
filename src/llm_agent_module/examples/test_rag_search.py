"""Synthetic-data smoke test: Ollama embeddings -> Chroma storage -> search.

No real site records, PDFs, credentials, or model weights are included.
This is a connectivity/filter test, not a retrieval-quality benchmark.
"""

import json
import math
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import chromadb
from chromadb.config import Settings

ROOT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:11434"
EMBEDDING_MODEL = "bge-m3"
EMBEDDING_DIM = 1024

# Keep this demo separate from previously created databases.
DB_PATH = ROOT / "data" / "chroma_demo"
COLLECTION_NAME = "aloha_synthetic_rag_v1"
COLLECTION_METADATA = {
    "source_type": "synthetic",
    "embedding_model": EMBEDDING_MODEL,
    "dataset_version": 1,
}

QUESTION = "What is the electrical progress at Demo Store A in Week 7?"
EXPECTED_ID = "demo-store-a-w7-electrical"
SEARCH_FILTER = {
    "$and": [
        {"store": "demo-store-a"},
        {"week": 7},
        {"source_type": "synthetic"},
    ]
}

# All store names and percentages below are invented for this demo.
DOCUMENTS = [
    {
        "id": "demo-store-a-w6-electrical",
        "text": "SYNTHETIC DATA: Demo Store A, Week 6: Electrical progress is 30%.",
        "store": "demo-store-a",
        "week": 6,
    },
    {
        "id": EXPECTED_ID,
        "text": "SYNTHETIC DATA: Demo Store A, Week 7: Electrical progress is 45%.",
        "store": "demo-store-a",
        "week": 7,
    },
    {
        "id": "demo-store-b-w7-electrical",
        "text": "SYNTHETIC DATA: Demo Store B, Week 7: Electrical progress is 60%.",
        "store": "demo-store-b",
        "week": 7,
    },
]


def ollama_request(
    endpoint: str, payload: dict[str, Any], timeout: int = 180
) -> dict[str, Any]:
    """Call the local Ollama API without printing request/response bodies on errors."""
    request = Request(
        BASE_URL + endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(
            f"Ollama HTTP {exc.code} at {endpoint}. "
            "Check the local service and the requested model in 'ollama list'."
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(
            "Cannot reach Ollama or the request timed out. "
            "Start Ollama and check the local API."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Ollama returned an invalid JSON response.") from exc

    if not isinstance(result, dict) or "error" in result:
        raise RuntimeError("Ollama returned an error or unexpected response format.")
    return result


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Use the same embedding model for documents and questions."""
    result = ollama_request(
        "/api/embed",
        {
            "model": EMBEDDING_MODEL,
            "input": texts,
            "truncate": False,
            "keep_alive": "0s",
        },
    )
    vectors = result.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise RuntimeError("Unexpected number of embeddings returned by Ollama.")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIM:
            raise RuntimeError(f"Expected {EMBEDDING_DIM}-dimensional embeddings.")
        if not all(type(x) in (int, float) and math.isfinite(x) for x in vector):
            raise RuntimeError("Embedding contains invalid numeric values.")
        if not any(vector):
            raise RuntimeError("Ollama returned a zero vector.")
    return vectors


def validate_demo_collection(collection: Any) -> None:
    """Reject collections whose declared demo/model metadata does not match."""
    metadata = collection.metadata or {}
    if any(metadata.get(key) != value for key, value in COLLECTION_METADATA.items()):
        raise RuntimeError("Demo collection metadata mismatch; do not reuse a real-data DB.")


def main() -> None:
    print("DEMO ONLY: synthetic data, not an actual construction report.", flush=True)
    print("Question:", QUESTION, flush=True)
    texts = [record["text"] for record in DOCUMENTS]

    print("1. Generating bge-m3 embeddings...", flush=True)
    vectors = embed_texts(texts + [QUESTION])
    print("EMBEDDING_DIM:", len(vectors[0]), flush=True)

    client = chromadb.PersistentClient(
        path=str(DB_PATH),
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
        metadata=COLLECTION_METADATA,
        configuration={"hnsw": {"space": "cosine"}},
    )
    validate_demo_collection(collection)
    collection.upsert(
        ids=[record["id"] for record in DOCUMENTS],
        documents=texts,
        embeddings=vectors[:-1],
        metadatas=[
            {"store": record["store"], "week": record["week"], "source_type": "synthetic"}
            for record in DOCUMENTS
        ],
    )
    count = collection.count()
    if count != len(DOCUMENTS):
        raise RuntimeError("Expected exactly 3 records in the dedicated demo collection.")
    print("2. Chroma storage complete. DOCUMENT_COUNT:", count, flush=True)

    result = collection.query(
        query_embeddings=[vectors[-1]],
        where=SEARCH_FILTER,
        n_results=1,
        include=["documents", "metadatas", "distances"],
    )
    if result["ids"] != [[EXPECTED_ID]]:
        raise RuntimeError("The returned record did not match the expected demo ID.")

    print("3. Retrieved ID:", result["ids"][0], flush=True)
    print("Retrieved document:", result["documents"][0], flush=True)
    print("Cosine distance:", result["distances"][0], flush=True)
    print("PASS: bge-m3 -> Chroma -> search", flush=True)
    print("Scope: connectivity and filtering only; not retrieval accuracy.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)