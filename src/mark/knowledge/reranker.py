"""Camada de Reranking para Priorização de Contexto e Filtragem de Redundância (MMR).

Reordena e seleciona os trechos mais informativos e diversificados para a janela de contexto,
combinando cobertura de termos, correspondência de frases exatas, confiabilidade da fonte
e penalização de redundância semântica (Maximal Marginal Relevance).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any

from ..cognition.embeddings import cosine, tokens
from .hybrid import RetrievalHit


@dataclass
class RerankResult:
    hit: RetrievalHit
    rerank_score: float
    relevance_breakdown: dict[str, float] = field(default_factory=dict)
    selected: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.hit.chunk.chunk_id,
            "doc_id": self.hit.document.doc_id,
            "title": self.hit.document.title,
            "page": self.hit.chunk.page,
            "section": self.hit.chunk.section,
            "text": self.hit.chunk.text,
            "rerank_score": round(self.rerank_score, 4),
            "breakdown": {k: round(v, 4) for k, v in self.relevance_breakdown.items()},
        }


def _phrase_overlap(query: str, text: str) -> float:
    """Detecta se bi-gramas ou tri-gramas da query ocorrem contiguamente no texto."""
    q_words = re.findall(r"\w+", query.lower())
    t_words = re.findall(r"\w+", text.lower())
    if len(q_words) < 2 or not t_words:
        return 0.0

    t_text = " " + " ".join(t_words) + " "
    matches = 0
    total_bigrams = len(q_words) - 1

    for i in range(total_bigrams):
        bigram = f" {q_words[i]} {q_words[i+1]} "
        if bigram in t_text:
            matches += 1

    return matches / max(1, total_bigrams)


class Reranker:
    """Reranker contextual de múltiplos sinais com seleção por diversidade MMR."""

    def __init__(
        self,
        redundancy_threshold: float = 0.82,
        max_context_chars: int = 4000,
    ) -> None:
        self.redundancy_threshold = redundancy_threshold
        self.max_context_chars = max_context_chars

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalHit],
        top_k: int = 5,
    ) -> list[RerankResult]:
        """Reordena e filtra candidatos por relevância máxima e diversidade informativa."""
        if not candidates:
            return []

        q_tokens = set(tokens(query))
        scored: list[tuple[float, dict[str, float], RetrievalHit]] = []

        for hit in candidates:
            text = hit.chunk.text
            section = hit.chunk.section
            c_tokens = set(tokens(text))

            # 1. Cobertura de termos únicos da pergunta
            coverage = len(q_tokens & c_tokens) / max(1, len(q_tokens))

            # 2. Correspondência de frases contíguas (bi-gramas)
            phrase_score = _phrase_overlap(query, text)

            # 3. Correspondência com cabeçalho de seção
            s_tokens = set(tokens(section))
            section_bonus = 0.15 if (q_tokens & s_tokens) else 0.0

            # 4. Confiabilidade / Tier da Fonte
            tier_bonus = 0.0
            doc_meta = hit.document.metadata or {}
            source_tier = str(doc_meta.get("source_tier", "STANDARD")).upper()
            if source_tier in {"OFFICIAL", "VERIFIED", "PRIMARY"}:
                tier_bonus = 0.1
            elif source_tier == "UNVERIFIED":
                tier_bonus = -0.1

            # 5. Score base do RRF / Embedding
            base_score = hit.combined_score * 10.0  # normaliza escala do RRF
            semantic_score = hit.semantic_score

            # Cálculo ponderado
            final_score = (
                (0.35 * coverage)
                + (0.25 * semantic_score)
                + (0.20 * phrase_score)
                + (0.10 * base_score)
                + section_bonus
                + tier_bonus
            )

            breakdown = {
                "coverage": coverage,
                "semantic": semantic_score,
                "phrase": phrase_score,
                "base_rrf": base_score,
                "section_bonus": section_bonus,
                "tier_bonus": tier_bonus,
            }
            scored.append((final_score, breakdown, hit))

        # Ordenação inicial por score calculado
        scored.sort(key=lambda x: x[0], reverse=True)

        # Seleção por Maximal Marginal Relevance (MMR) para mitigar redundância
        selected: list[RerankResult] = []
        used_chars = 0

        for score, breakdown, hit in scored:
            if len(selected) >= top_k:
                break

            chunk_len = len(hit.chunk.text)
            if selected and (used_chars + chunk_len > self.max_context_chars):
                continue

            # Checa redundância contra já selecionados
            is_redundant = False
            for prev in selected:
                # Compara vetores se existirem
                if hit.chunk.vector and prev.hit.chunk.vector:
                    sim = cosine(hit.chunk.vector, prev.hit.chunk.vector)
                    if sim > self.redundancy_threshold:
                        is_redundant = True
                        break
                else:
                    # Fallback para overlap lexical de tokens
                    prev_tokens = set(tokens(prev.hit.chunk.text))
                    curr_tokens = set(tokens(hit.chunk.text))
                    jaccard = len(prev_tokens & curr_tokens) / max(1, len(prev_tokens | curr_tokens))
                    if jaccard > 0.75:
                        is_redundant = True
                        break

            if not is_redundant or not selected:
                selected.append(
                    RerankResult(
                        hit=hit,
                        rerank_score=score,
                        relevance_breakdown=breakdown,
                        selected=True,
                    )
                )
                used_chars += chunk_len

        return selected
