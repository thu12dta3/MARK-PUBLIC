"""Módulo de Extração, Formatação e Verificação Estrita de Citações Factuais do MARK.

Força citações ancoradas no formato `[DocID:Trecho]` ou `[DocID:p.N]` e valida
matematicamente se as afirmações derivam dos documentos originais ou se configuram alucinação.
Gera links clicáveis `mark-citation://` para inspeção visual do trecho original na interface.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import html
import re
from urllib.parse import quote
from typing import Any, Iterable

from .indexer import PassageChunk

# Suporta [DocID:Trecho], [DocID:p.12], [DocID:Trecho, p.4]
_CITATION_REGEX = re.compile(
    r"\[([a-zA-Z0-9_\-\.]{2,64}):\s*([^\]\r\n]{1,160})\]"
)


@dataclass
class CitationMatch:
    doc_id: str
    excerpt: str
    page: int | None = None
    raw_tag: str = ""
    start_pos: int = 0
    end_pos: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CitationVerificationReport:
    total_citations: int
    verified_count: int
    partial_count: int
    unanchored_count: int
    hallucinated_count: int
    anchoring_ratio: float
    is_strictly_anchored: bool
    matches: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_citations(text: str) -> list[CitationMatch]:
    """Extrai todas as citações no padrão [DocID:Trecho] do texto."""
    matches: list[CitationMatch] = []
    if not text:
        return matches

    for match in _CITATION_REGEX.finditer(text):
        doc_id = match.group(1).strip()
        raw_excerpt = match.group(2).strip()

        # Checa se há indicação de página: ex 'p. 12' ou 'página 12'
        page_val = None
        page_match = re.search(r"\b(?:p\.|p[aá]g\.?|p[aá]gina)\s*(\d{1,5})\b", raw_excerpt, re.IGNORECASE)
        if page_match:
            try:
                page_val = int(page_match.group(1))
            except ValueError:
                pass

        matches.append(
            CitationMatch(
                doc_id=doc_id,
                excerpt=raw_excerpt,
                page=page_val,
                raw_tag=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )
    return matches


class CitationVerifier:
    """Verificador determinístico de ancoragem de citações contra os documentos originais."""

    def __init__(self, partial_threshold: float = 0.5) -> None:
        self.partial_threshold = partial_threshold

    def verify(
        self,
        text: str,
        passages: Iterable[PassageChunk] | dict[str, PassageChunk | list[PassageChunk]],
    ) -> CitationVerificationReport:
        """Compara cada citação com os chunks reais da base para atestar veracidade."""
        citations = extract_citations(text)
        if not citations:
            return CitationVerificationReport(
                total_citations=0,
                verified_count=0,
                partial_count=0,
                unanchored_count=0,
                hallucinated_count=0,
                anchoring_ratio=1.0,
                is_strictly_anchored=True,
                matches=[],
            )

        # Mapeamento de chunks por doc_id
        doc_chunks: dict[str, list[PassageChunk]] = {}
        if isinstance(passages, dict):
            for k, v in passages.items():
                if isinstance(v, list):
                    doc_chunks[k] = v
                else:
                    doc_chunks.setdefault(v.doc_id, []).append(v)
        else:
            for p in passages:
                doc_chunks.setdefault(p.doc_id, []).append(p)

        verified = 0
        partial = 0
        unanchored = 0
        hallucinated = 0
        details: list[dict[str, Any]] = []

        for cit in citations:
            doc_id = cit.doc_id
            if doc_id not in doc_chunks:
                # O documento citado sequer existe nas fontes fornecidas! Alucinação!
                hallucinated += 1
                details.append({
                    "doc_id": doc_id,
                    "citation": cit.to_dict(),
                    "status": "HALLUCINATED_DOC",
                    "reason": f"O DocID '{doc_id}' não existe no contexto recuperado.",
                    "confidence": 0.0,
                })
                continue

            chunks_for_doc = doc_chunks[doc_id]
            # Se a citação citar página específica, tenta filtrar
            if cit.page is not None:
                page_chunks = [c for c in chunks_for_doc if c.page == cit.page]
                if page_chunks:
                    chunks_for_doc = page_chunks

            # Limpa excerpt de marcações de página para checagem textual
            clean_excerpt = re.sub(
                r"\b(?:p\.|p[aá]g\.?|p[aá]gina)\s*\d{1,5}\b", "", cit.excerpt, flags=re.I
            ).strip(" ,;:-")

            # Se for puramente citação de página válida sem texto extra
            if not clean_excerpt and cit.page is not None:
                verified += 1
                details.append({
                    "doc_id": doc_id,
                    "citation": cit.to_dict(),
                    "status": "VERIFIED",
                    "reason": f"Citação ancorada na página {cit.page} do documento.",
                    "confidence": 1.0,
                })
                continue

            # Testa correspondência textual contra os chunks do documento
            words_in_excerpt = set(re.findall(r"\w+", clean_excerpt.lower()))
            best_overlap = 0.0
            exact_match_found = False
            matching_chunk_id = None

            for chunk in chunks_for_doc:
                chunk_lower = chunk.text.lower()
                if clean_excerpt.lower() in chunk_lower:
                    exact_match_found = True
                    matching_chunk_id = chunk.chunk_id
                    break

                chunk_words = set(re.findall(r"\w+", chunk_lower))
                if words_in_excerpt:
                    overlap = len(words_in_excerpt & chunk_words) / len(words_in_excerpt)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        matching_chunk_id = chunk.chunk_id

            if exact_match_found or best_overlap >= 0.85:
                verified += 1
                details.append({
                    "doc_id": doc_id,
                    "citation": cit.to_dict(),
                    "status": "VERIFIED",
                    "chunk_id": matching_chunk_id,
                    "confidence": 1.0 if exact_match_found else round(best_overlap, 3),
                })
            elif best_overlap >= self.partial_threshold:
                partial += 1
                details.append({
                    "doc_id": doc_id,
                    "citation": cit.to_dict(),
                    "status": "PARTIAL_MATCH",
                    "chunk_id": matching_chunk_id,
                    "confidence": round(best_overlap, 3),
                    "reason": "Trecho parcialmente ancorado nas fontes.",
                })
            else:
                unanchored += 1
                details.append({
                    "doc_id": doc_id,
                    "citation": cit.to_dict(),
                    "status": "UNANCHORED_EXCERPT",
                    "chunk_id": matching_chunk_id,
                    "confidence": round(best_overlap, 3),
                    "reason": "Trecho citado não encontrado no texto do documento.",
                })

        total = len(citations)
        ratio = (verified + (0.5 * partial)) / max(1, total)
        is_strictly_anchored = (hallucinated == 0 and unanchored == 0)

        return CitationVerificationReport(
            total_citations=total,
            verified_count=verified,
            partial_count=partial,
            unanchored_count=unanchored,
            hallucinated_count=hallucinated,
            anchoring_ratio=round(ratio, 4),
            is_strictly_anchored=is_strictly_anchored,
            matches=details,
        )


def verify_citations(
    text: str,
    passages: Iterable[PassageChunk] | dict[str, PassageChunk | list[PassageChunk]],
) -> CitationVerificationReport:
    """Helper direto para verificação de citações."""
    return CitationVerifier().verify(text, passages)


def format_clickable_citations(
    text: str,
    doc_titles: dict[str, str] | None = None,
) -> str:
    """Substitui tags de citação [DocID:Trecho] por links ricos da UI mark-citation://."""
    if not text:
        return text

    doc_titles = doc_titles or {}

    def _repl(match: re.Match) -> str:
        doc_id = match.group(1).strip()
        excerpt = match.group(2).strip()
        title = doc_titles.get(doc_id, doc_id)
        # Codifica para URL segura
        safe_url = f"mark-citation://{quote(doc_id)}?excerpt={quote(excerpt)}&title={quote(title)}"
        label = html.escape(match.group(0))
        return f'<a href="{safe_url}" style="color: #58A6FF; text-decoration: none; font-weight: 500; background: rgba(88, 166, 255, 0.12); padding: 1px 6px; border-radius: 4px; border: 1px solid rgba(88, 166, 255, 0.3);">{label}</a>'

    return _CITATION_REGEX.sub(_repl, text)


def citation_system_instruction() -> str:
    """Instrução mandatória de ancoragem e citação para o System Prompt."""
    return (
        " Regra de Citações Verificáveis: Cada afirmação factual derivada do contexto recuperado "
        "DEVE ser associada estritamente a uma citação no formato [DocID:Trecho] (ou [DocID:p.N] "
        "se houver página). Não invente DocIDs ou trechos não presentes nas fontes. Se a informação "
        "não constar nas fontes fornecidas, declare explicitamente a ausência."
    )
