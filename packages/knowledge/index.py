"""检索先过滤权限、版本和型号，再把候选交给任何排名器。"""
import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping


class KnowledgeError(ValueError):
    pass


DOCUMENT_TRANSITIONS: dict[str, set[str]] = {
    "UPLOADED": {"PARSED", "FAILED", "REVOKED"},
    "PARSED": {"REVIEWED", "FAILED", "REVOKED"},
    "REVIEWED": {"INDEXING", "REVOKED"},
    "INDEXING": {"PUBLISHED", "FAILED", "REVOKED"},
    "PUBLISHED": {"REVOKED"}, "FAILED": {"UPLOADED", "INDEXING", "REVOKED"},
    "REVOKED": set(),
}


def transition_document(current: str, target: str, *, reviewed: bool = False, index_ready: bool = False) -> str:
    if target not in DOCUMENT_TRANSITIONS.get(current, set()):
        raise KnowledgeError("INVALID_DOCUMENT_TRANSITION")
    if target == "PUBLISHED" and not (reviewed and index_ready):
        raise KnowledgeError("DOCUMENT_REVIEW_AND_INDEX_REQUIRED")
    if current == "FAILED" and target == "INDEXING" and not reviewed:
        raise KnowledgeError("DOCUMENT_REVIEW_REQUIRED")
    return target


def _terms(query: str) -> set[str]:
    return set(re.findall(r"[\w.-]+", query.casefold()))


class KnowledgeIndex:
    def __init__(self, documents: Iterable[Mapping[str, Any]], generation: str = "1", acl_version: str = "1", *,
                 scorer: Callable[[str, list[dict[str, Any]]], Iterable[float]] | None = None) -> None:
        self.documents = {str(doc["id"]): deepcopy(dict(doc)) for doc in documents}
        self.generation, self.acl_version = generation, acl_version
        self.scorer = scorer

    def cache_key(self, context: Mapping[str, Any], query: str, collection_ids: Iterable[str], document_version_ids: Iterable[str] = ()) -> str:
        import json
        payload = [self.generation, self.acl_version, context.get("project_id"), context.get("user_id"), context.get("member"),
                   context.get("acl_version"), query, sorted(collection_ids), sorted(document_version_ids)]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def search(self, context: Mapping[str, Any], query: str, collection_ids: Iterable[str],
               document_version_ids: Iterable[str] = (), top_k: int = 5) -> dict[str, Any]:
        if not 1 <= top_k <= 20 or not query.strip():
            raise KnowledgeError("INVALID_SEARCH")
        if context.get("member") is not True:
            return {"hits": [], "partial": False, "warnings": ["PROJECT_ACCESS_DENIED"]}
        collections, versions = set(collection_ids), set(document_version_ids)
        candidates: list[dict[str, Any]] = []
        for doc in self.documents.values():
            if not self._visible(doc, context, collections, versions):
                continue
            models = doc.get("model_numbers", ())
            requested_model = context.get("model_number")
            if requested_model and requested_model not in models:
                continue
            for chunk in doc.get("chunks", ()):
                if not chunk.get("locator") or not chunk.get("text"):
                    continue
                candidates.append({**deepcopy(chunk), "document_id": doc["id"], "document": doc})
        scores = list(self.scorer(query, candidates)) if self.scorer else [self._score(query, item) for item in candidates]
        if len(scores) != len(candidates):
            raise KnowledgeError("RANKER_OUTPUT_INVALID")
        ranked = sorted(zip(candidates, scores), key=lambda pair: (-pair[1], str(pair[0]["document_id"]), pair[0]["locator"]))
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        retrieved = datetime.now(timezone.utc).isoformat()
        for chunk, score in ranked:
            if score <= 0:
                continue
            digest = hashlib.sha256(chunk["text"].encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            doc = chunk["document"]
            hits.append({"source_id": doc.get("source_id", doc["id"]), "source_uri": doc["source_uri"], "locator": chunk["locator"],
                         "content_sha256": digest, "retrieved_at": retrieved, "license": doc["license"],
                         "acl_scope": f"project:{context['project_id']}", "text": chunk["text"]})
            if len(hits) == top_k:
                break
        return {"hits": hits, "partial": False, "warnings": [] if hits else ["NO_EVIDENCE_FOUND"]}

    @staticmethod
    def _visible(doc: Mapping[str, Any], context: Mapping[str, Any], collections: set[str], versions: set[str]) -> bool:
        return (doc.get("status") == "PUBLISHED" and doc.get("project_id") == context.get("project_id")
                and doc.get("collection_id") in collections and (not versions or doc.get("version_id") in versions)
                and (doc.get("project_readable") is True or context.get("user_id") in doc.get("allowed_user_ids", ())))

    @staticmethod
    def _score(query: str, chunk: Mapping[str, Any]) -> float:
        text = chunk["text"].casefold()
        exact_models = sum(10 for model in chunk["document"].get("model_numbers", ()) if model.casefold() in _terms(query))
        return float(exact_models + sum(1 for term in _terms(query) if term in text))

    def revoke(self, document_id: str) -> None:
        if document_id not in self.documents:
            raise KnowledgeError("DOCUMENT_NOT_FOUND")
        self.documents[document_id]["status"] = "REVOKED"
        self.acl_version = hashlib.sha256((self.acl_version + ":" + document_id).encode()).hexdigest()


class PgvectorSearch:
    """pgvector 扩展点；连接必须由持久化层提供，不创建全局连接。"""
    def __init__(self, connection: Any, *, generation: str, model_version: str, dimensions: int) -> None:
        if dimensions <= 0:
            raise KnowledgeError("INVALID_VECTOR_DIMENSION")
        self.connection, self.generation, self.model_version, self.dimensions = connection, generation, model_version, dimensions

    def search(self, vector: Iterable[float], *, project_id: str, authorized_document_ids: Iterable[str], top_k: int = 5) -> list[Any]:
        import math
        values = tuple(vector)
        ids = list(authorized_document_ids)
        if len(values) != self.dimensions or not all(math.isfinite(value) for value in values):
            raise KnowledgeError("VECTOR_DIMENSION_MISMATCH")
        if not 1 <= top_k <= 20:
            raise KnowledgeError("INVALID_TOP_K")
        if not ids:
            return []
        # ACL 清单在 SQL 召回之前应用；不同模型/维度的 generation 永不混用。
        query = """SELECT document_id, locator, text, embedding <=> %s::vector AS distance
                   FROM knowledge_vectors WHERE project_id = %s AND document_id = ANY(%s)
                   AND generation = %s AND model_version = %s AND dimensions = %s AND revoked = false
                   ORDER BY distance, document_id, locator LIMIT %s"""
        cursor = self.connection.execute(query, ("[" + ",".join(map(str, values)) + "]", project_id, ids,
                                                self.generation, self.model_version, self.dimensions, top_k))
        return list(cursor.fetchall())
