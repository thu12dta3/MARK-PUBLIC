"""Módulo de Conhecimento, RAG Híbrido, Reranking e Citações Verificáveis do MARK."""

from .indexer import DocumentRecord, KnowledgeIndexer, PassageChunk
from .hybrid import HybridRetriever, RetrievalHit
from .reranker import Reranker, RerankResult
from .citation import (
    CitationMatch,
    CitationVerificationReport,
    CitationVerifier,
    extract_citations,
    format_clickable_citations,
    citation_system_instruction,
    verify_citations,
)
from .pipeline import RAGContext, RAGPipeline

__all__ = [
    "DocumentRecord",
    "PassageChunk",
    "KnowledgeIndexer",
    "HybridRetriever",
    "RetrievalHit",
    "Reranker",
    "RerankResult",
    "CitationMatch",
    "CitationVerificationReport",
    "CitationVerifier",
    "extract_citations",
    "format_clickable_citations",
    "citation_system_instruction",
    "verify_citations",
    "RAGContext",
    "RAGPipeline",
]
