"""Pipeline RAG Completo do MARK (Híbrido + Reranking + Prompt Formatado + Verificação de Citações)."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .citation import (
    CitationVerificationReport,
    CitationVerifier,
    citation_system_instruction,
    format_clickable_citations,
    verify_citations,
)
from .hybrid import HybridRetriever, RetrievalHit
from .indexer import DocumentRecord, KnowledgeIndexer, PassageChunk
from .reranker import RerankResult, Reranker


@dataclass
class RAGContext:
    query: str
    passages: list[PassageChunk]
    results: list[RerankResult]
    formatted_context_text: str
    system_instruction: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "passages": [p.to_dict() for p in self.passages],
            "results": [r.to_dict() for r in self.results],
            "context_length": len(self.formatted_context_text),
        }


class RAGPipeline:
    """Orquestrador do ciclo completo de Recuperação Aumentada por Geração (RAG)."""

    def __init__(
        self,
        indexer: KnowledgeIndexer | None = None,
        db_path: str | Path | None = None,
        rrf_k: int = 60,
        max_context_chars: int = 4000,
    ) -> None:
        self.indexer = indexer or KnowledgeIndexer(db_path=db_path)
        self.retriever = HybridRetriever(self.indexer, rrf_k=rrf_k)
        self.reranker = Reranker(max_context_chars=max_context_chars)
        self.verifier = CitationVerifier()

    def retrieve_and_format(
        self,
        query: str,
        top_k: int = 4,
        collection: str | None = None,
    ) -> RAGContext:
        """Executa busca híbrida, reranking e monta o bloco de contexto formatado com DocIDs."""
        candidates = self.retriever.retrieve(query, limit=16, collection=collection)
        reranked = self.reranker.rerank(query, candidates, top_k=top_k)

        passages = [r.hit.chunk for r in reranked]
        lines = [
            "Fontes locais verificadas da Biblioteca (use como referência obrigatória para citações [DocID:Trecho]):"
        ]

        for i, item in enumerate(reranked, start=1):
            chunk = item.hit.chunk
            doc = item.hit.document
            page_info = f", p. {chunk.page}" if chunk.page is not None else ""
            sec_info = f", Seção: '{chunk.section}'" if chunk.section else ""
            lines.append(
                f"\n--- Fonte {i} [DocID: {doc.doc_id}] ---"
                f"\nTítulo: {doc.title}{page_info}{sec_info}"
                f"\nTrecho: {chunk.text.strip()}"
            )

        context_text = "\n".join(lines) if passages else ""
        instruction = citation_system_instruction()

        return RAGContext(
            query=query,
            passages=passages,
            results=reranked,
            formatted_context_text=context_text,
            system_instruction=instruction,
        )

    def verify_response(
        self,
        response_text: str,
        context: RAGContext | list[PassageChunk],
    ) -> CitationVerificationReport:
        """Verifica se as citações [DocID:Trecho] da resposta gerada são matematicamente ancoradas."""
        passages = context.passages if isinstance(context, RAGContext) else context
        return self.verifier.verify(response_text, passages)

    def format_html(
        self,
        response_text: str,
        context: RAGContext | None = None,
    ) -> str:
        """Formata o texto da resposta para a interface com links de citação interativos."""
        titles = {}
        if context:
            for r in context.results:
                titles[r.hit.document.doc_id] = r.hit.document.title
        return format_clickable_citations(response_text, titles)
