import sys
import tempfile
import chromadb

from chromadb.config import Settings

print("Python:", sys.version.split()[0])
print("Chroma:", chromadb.__version__)

# 자동 삭제하지 않는 임시 테스트 DB
db_path = tempfile.mkdtemp(prefix="aloha_chroma_")

client = chromadb.PersistentClient(
    path=db_path,
    settings=Settings(anonymized_telemetry=False),
)

print("CLIENT_OK")

collection = client.create_collection(
    name="native_probe",
    embedding_function=None,
)

print("COLLECTION_OK")

vector_a = [1.0] + [0.0] * 1023
vector_b = [0.0, 1.0] + [0.0] * 1022

print("UPSERT_START")

collection.upsert(
    ids=["probe-a", "probe-b"],
    embeddings=[vector_a, vector_b],
    documents=["Diagnostic A", "Diagnostic B"],
)

print("UPSERT_OK")

collection.upsert(
    ids=["probe-a", "probe-b"],
    embeddings=[vector_a, vector_b],
    documents=["Diagnostic A", "Diagnostic B"],
)

assert collection.count() == 2
print("REPEAT_UPSERT_OK")

result = collection.query(
    query_embeddings=[vector_a],
    n_results=1,
)

assert result["ids"] == [["probe-a"]]

print("QUERY_OK:", result["ids"])
print("PASS: save, update, search")
print("TEST_DB:", db_path)