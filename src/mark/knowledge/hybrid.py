"""Motor de Recuperação Híbrida do MARK (BM25 FTS5 + Dense Embeddings + RRF).

Combina busca léxica de alta precisão baseada em BM25 (FTS5) com busca semântica
baseada em vetores locais (cosine similarity) utilizando Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from ..cognition.embeddings import EmbeddingProvider, cosine, tokens
from .indexer import DocumentRecord, KnowledgeIndexer, PassageChunk


@dataclass
class RetrievalHit:
    chunk: PassageChunk
    document: DocumentRecord
    combined_score: float
    lexical_score: float
    semantic_score: float
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk": self.chunk.to_dict(),
            "document": self.document.to_dict(),
            "combined_score": round(self.combined_score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "semantic_score": round(self.semantic_score, 4),
            "rank": self.rank,
        }


def _sanitize_fts_query(query: str) -> str:
    """Higieniza a query para sintaxe segura do FTS5 (removendo operadores e caracteres especiais)."""
    raw_tokens = tokens(query)
    clean = [t for t in raw_tokens if len(t) >= 2]
    if not clean:
        return ""
    # Junta com OR para permitir cobertura ampla e ranqueamento BM25
    return " OR ".join(f'"{token}"' for token in clean[:32])


class HybridRetriever:
    """Recuperador híbrido unificando busca léxica e semântica com fusão por ranking recíproco."""

    def __init__(
        self,
        indexer: KnowledgeIndexer,
        rrf_k: int = 60,
        lexical_weight: float = 0.5,
        semantic_weight: float = 0.5,
    ) -> None:
        self.indexer = indexer
        self.rrf_k = max(1, rrf_k)
        self.lexical_weight = lexical_weight
        self.semantic_weight = semantic_weight

    def retrieve(
        self,
        query: str,
        limit: int = 10,
        collection: str | None = None,
        candidate_pool_size: int = 50,
    ) -> list[RetrievalHit]:
        """Executa busca híbrida combinada e retorna os melhores trechos ranqueados."""
        if not query or not query.strip():
            return []

        fts_expression = _sanitize_fts_query(query)
        embedding_provider: EmbeddingProvider = self.indexer.embedding
        query_vector = embedding_provider.embed(query)

        # 1. Busca Léxica BM25 via FTS5
        lexical_ranks: dict[str, int] = {}
        lexical_raw_scores: dict[str, float] = {}

        if fts_expression:
            base_sql = """
                SELECT fts.chunk_id, bm25(chunks_fts) as score
                FROM chunks_fts fts
                JOIN sources s ON fts.source_id = s.id
                WHERE s.active = 1
            """
            params: list[Any] = []
            if collection:
                base_sql += " AND s.collection = ?"
                params.append(collection)
            base_sql += " AND chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?"
            params.extend([fts_expression, candidate_pool_size])

            try:
                rows = self.indexer.conn.execute(base_sql, params).fetchall()
                for rank, (chunk_id, score) in enumerate(rows, start=1):
                    lexical_ranks[chunk_id] = rank
                    # bm25 no SQLite retorna valores negativos (mais negativo = mais relevante)
                    lexical_raw_scores[chunk_id] = abs(float(score))
            except Exception:
                # Em caso de query que falhe no FTS, prossegue com busca semântica
                pass

        # 2. Busca Semântica Densa (Vectors)
        vector_sql = """
            SELECT c.id, c.source_id, c.chunk_index, c.text, c.section, c.page, c.vector, c.metadata,
                   s.title, s.author, s.file_path, s.format, s.checksum, s.collection, s.metadata,
                   s.created_at, s.updated_at
            FROM chunks c
            JOIN sources s ON c.source_id = s.id
            WHERE s.active = 1
        """
        v_params: list[Any] = []
        if collection:
            vector_sql += " AND s.collection = ?"
            v_params.append(collection)

        # Se tivermos candidatos do FTS, podemos focar neles ou varrer banco se for leve
        rows = self.indexer.conn.execute(vector_sql, v_params).fetchall()

        semantic_candidates: list[tuple[str, float, PassageChunk, DocumentRecord]] = []
        for r in rows:
            cid = r[0]
            c_vec = json.loads(r[6])
            sim = cosine(query_vector, c_vec)
            chunk = PassageChunk(
                chunk_id=r[0],
                doc_id=r[1],
                chunk_index=r[2],
                text=r[3],
                section=r[4],
                page=r[5],
                vector=c_vec,
                metadata=json.loads(r[7]),
            )
            doc = DocumentRecord(
                doc_id=r[1],
                title=r[8],
                author=r[9],
                file_path=r[10],
                format=r[11],
                checksum=r[12],
                collection=r[13],
                metadata=json.loads(r[14]),
                created_at=r[15],
                updated_at=r[16],
                active=True,
            )
            semantic_candidates.append((cid, sim, chunk, doc))

        # Ordena semanticamente
        semantic_candidates.sort(key=lambda x: x[1], reverse=True)
        semantic_ranks: dict[str, int] = {}
        semantic_raw_scores: dict[str, float] = {}
        chunks_map: dict[str, tuple[PassageChunk, DocumentRecord]] = {}

        for rank, (cid, sim, chunk, doc) in enumerate(semantic_candidates[:candidate_pool_size], start=1):
            semantic_ranks[cid] = rank
            semantic_raw_scores[cid] = max(0.0, float(sim))
            chunks_map[cid] = (chunk, doc)

        # Garante que todos os achados do FTS também estejam em chunks_map
        all_cids = set(lexical_ranks.keys()) | set(semantic_ranks.keys())
        missing_cids = [cid for cid in all_cids if cid not in chunks_map]
        if missing_cids:
            placeholders = ",".join("?" for _ in missing_cids)
            fetch_sql = f"""
                SELECT c.id, c.source_id, c.chunk_index, c.text, c.section, c.page, c.vector, c.metadata,
                       s.title, s.author, s.file_path, s.format, s.checksum, s.collection, s.metadata,
                       s.created_at, s.updated_at
                FROM chunks c
                JOIN sources s ON c.source_id = s.id
                WHERE c.id IN ({placeholders})
            """
            for r in self.indexer.conn.execute(fetch_sql, missing_cids).fetchall():
                c_vec = json.loads(r[6])
                chunk = PassageChunk(
                    chunk_id=r[0],
                    doc_id=r[1],
                    chunk_index=r[2],
                    text=r[3],
                    section=r[4],
                    page=r[5],
                    vector=c_vec,
                    metadata=json.loads(r[7]),
                )
                doc = DocumentRecord(
                    doc_id=r[1],
                    title=r[8],
                    author=r[9],
                    file_path=r[10],
                    format=r[11],
                    checksum=r[12],
                    collection=r[13],
                    metadata=json.loads(r[14]),
                    created_at=r[15],
                    updated_at=r[16],
                    active=True,
                )
                chunks_map[r[0]] = (chunk, doc)
                semantic_raw_scores[r[0]] = max(0.0, float(cosine(query_vector, c_vec)))

        # 3. Reciprocal Rank Fusion (RRF)
        hits: list[RetrievalHit] = []
        for cid in all_cids:
            if cid not in chunks_map:
                continue
            chunk, doc = chunks_map[cid]

            lex_rank = lexical_ranks.get(cid)
            sem_rank = semantic_ranks.get(cid)

            # Cálculo de score RRF
            rrf_lex = (1.0 / (self.rrf_k + lex_rank)) if lex_rank else 0.0
            rrf_sem = (1.0 / (self.rrf_k + sem_rank)) if sem_rank else 0.0

            combined = (self.lexical_weight * rrf_lex) + (self.semantic_weight * rrf_sem)
            lex_score = lexical_raw_scores.get(cid, 0.0)
            sem_score = semantic_raw_scores.get(cid, 0.0)

            hits.append(
                RetrievalHit(
                    chunk=chunk,
                    document=doc,
                    combined_score=combined,
                    lexical_score=lex_score,
                    semantic_score=sem_score,
                )
            )

        # Ordena pelo combined score decrescente
        hits.sort(key=lambda h: h.combined_score, reverse=True)
        for i, hit in enumerate(hits[:limit], start=1):
            hit.rank = i

        return hits[:limit]
