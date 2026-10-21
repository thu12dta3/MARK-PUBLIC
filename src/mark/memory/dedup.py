"""Motor de Deduplicação e Detecção de Conflitos de Memória do MARK.

Detecta:
1. Hashes idênticos (duplicatas exatas).
2. Semântica similar e divergências informativas (conflitos de fatos).
3. Versionamento seguro com preservação: NUNCA apaga dados silenciosamente;
   sinaliza conflitos para resolução manual explícita do usuário.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import re
import time
from typing import Any

from ..cognition.embeddings import HashEmbeddingProvider, cosine, tokens


@dataclass
class MemoryConflict:
    conflict_id: str
    existing_memory_id: int
    scope: str
    scope_id: str
    existing_content: str
    new_content: str
    reason: str
    similarity_score: float
    detected_at: int = field(default_factory=lambda: int(time.time()))
    status: str = "PENDING"  # PENDING, RESOLVED_KEEP_EXISTING, RESOLVED_USE_NEW, RESOLVED_KEEP_BOTH

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_fact_text(text: str) -> str:
    """Normaliza texto removendo espaços extras e padronizando formato."""
    return " ".join(text.strip().split())


def compute_fact_fingerprint(text: str) -> str:
    """Calcula hash determinístico SHA-256 do texto normalizado em minúsculas."""
    clean = normalize_fact_text(text).casefold()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


class DeduplicationEngine:
    """Motor de análise de similaridade e conflito entre memórias persistentes."""

    def __init__(
        self,
        semantic_threshold: float = 0.65,
        exact_threshold: float = 0.95,
    ) -> None:
        self.semantic_threshold = semantic_threshold
        self.exact_threshold = exact_threshold
        self.embedder = HashEmbeddingProvider()

    def compare(self, text_a: str, text_b: str) -> float:
        """Calcula similaridade híbrida (Jaccard lexical + Cosine embedding)."""
        clean_a = normalize_fact_text(text_a).casefold()
        clean_b = normalize_fact_text(text_b).casefold()

        if clean_a == clean_b:
            return 1.0

        tok_a = set(tokens(clean_a))
        tok_b = set(tokens(clean_b))

        if not tok_a or not tok_b:
            return 0.0

        jaccard = len(tok_a & tok_b) / len(tok_a | tok_b)
        vec_a = self.embedder.embed(clean_a)
        vec_b = self.embedder.embed(clean_b)
        cos = max(0.0, float(cosine(vec_a, vec_b)))

        # Ponderação 50% Jaccard, 50% Cosine
        return round(0.5 * jaccard + 0.5 * cos, 4)

    def detect_conflicts(
        self,
        new_content: str,
        existing_facts: list[dict[str, Any]],
        scope: str,
        scope_id: str,
    ) -> list[MemoryConflict]:
        """Varre fatos existentes no mesmo escopo para detectar contradições ou divergências."""
        conflicts: list[MemoryConflict] = []
        clean_new = normalize_fact_text(new_content)
        new_fp = compute_fact_fingerprint(clean_new)

        for fact in existing_facts:
            # Só compara no mesmo escopo e scope_id
            if fact.get("scope") != scope or fact.get("scope_id") != scope_id:
                continue

            existing_content = fact.get("content", "")
            existing_fp = fact.get("fingerprint") or compute_fact_fingerprint(existing_content)

            # Duplicata exata: não é conflito destrutivo, apenas duplicata
            if new_fp == existing_fp:
                continue

            sim = self.compare(existing_content, clean_new)

            # Se for similaridade relevante (>= 0.60), analisa se há divergência
            if sim >= self.semantic_threshold:
                # Checa se diferem em números, caminhos, valores ou estados
                nums_old = set(re.findall(r"\b\d+\b", existing_content))
                nums_new = set(re.findall(r"\b\d+\b", clean_new))

                reason = "Alta similaridade semântica com conteúdo divergente."
                if nums_old != nums_new:
                    reason = f"Divergência numérica/identificador detectada: '{nums_old}' vs '{nums_new}'."

                cid = hashlib.sha256(f"{fact.get('id')}:{new_fp}".encode()).hexdigest()[:12]
                conflicts.append(
                    MemoryConflict(
                        conflict_id=cid,
                        existing_memory_id=fact.get("id", 0),
                        scope=scope,
                        scope_id=scope_id,
                        existing_content=existing_content,
                        new_content=clean_new,
                        reason=reason,
                        similarity_score=sim,
                        status="PENDING",
                    )
                )

        return conflicts
