"""Indexador de Conhecimento e Biblioteca de Documentos do MARK.

Suporta importação, organização, remoção e indexação estruturada de:
- Documentos PDF (com extração de texto e páginas)
- Documentos DOCX (via parsing seguro de XML nativo)
- Arquivos de texto (.txt, .md, .csv, .json, .log)

Mantém índices locais:
1. Metadados e catálogo em SQLite (tabela sources)
2. Chunks de texto com embeddings (tabela chunks)
3. Índice textual BM25 em tempo real (FTS5 virtual table chunks_fts)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
from typing import Any, Iterable

from ..cognition.embeddings import EmbeddingProvider, HashEmbeddingProvider
from ..cognition.library_extract import extract_known, supported
from ..cognition.storage import private_file

MAX_FILE_BYTES = 16 * 1024 * 1024  # 16 MiB
MAX_TEXT_CHARS = 2_500_000
DEFAULT_KNOWLEDGE_DB = Path.home() / ".local/state/mark/cognition/knowledge_rag.sqlite3"


@dataclass
class DocumentRecord:
    doc_id: str
    title: str
    author: str
    file_path: str
    format: str
    checksum: str
    collection: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PassageChunk:
    chunk_id: str
    doc_id: str
    chunk_index: int
    text: str
    section: str = ""
    page: int | None = None
    vector: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_read_document(path: Path) -> bytes:
    p = Path(path).resolve()
    if p.is_symlink():
        raise ValueError("Links simbólicos não são permitidos por segurança")
    if not p.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {p}")
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"Arquivo de {size} bytes excede o limite máximo de {MAX_FILE_BYTES} bytes")
    return p.read_bytes()


def _chunk_text_pages(
    pages: list[tuple[int | None, str]],
    doc_id: str,
    target_chunk_size: int = 1000,
    overlap: int = 150,
) -> list[PassageChunk]:
    """Divide páginas de documento em chunks navegáveis com overlap e rastreamento de seção."""
    chunks: list[PassageChunk] = []
    chunk_counter = 0

    heading_regex = re.compile(r"(?mi)^(?:#{1,6}\s+[^\n]+|chapter\s+\d+|cap[íi]tulo\s+\d+|se[cç][aã]o\s+\d+)")

    for page_num, page_content in pages:
        clean_text = page_content.strip()
        if not clean_text:
            continue

        lines = clean_text.splitlines()
        current_section = ""
        current_buffer = []
        current_len = 0

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            if heading_regex.match(line_str):
                current_section = re.sub(r"^#+\s*", "", line_str)[:120]

            line_len = len(line_str)
            if current_len + line_len + 1 > target_chunk_size and current_buffer:
                body = " ".join(current_buffer)
                cid = hashlib.sha256(f"{doc_id}:{chunk_counter}:{body[:64]}".encode()).hexdigest()[:16]
                chunks.append(
                    PassageChunk(
                        chunk_id=cid,
                        doc_id=doc_id,
                        chunk_index=chunk_counter,
                        text=body,
                        section=current_section,
                        page=page_num,
                    )
                )
                chunk_counter += 1

                # Overlap
                overlap_text = body[-overlap:] if len(body) > overlap else ""
                current_buffer = [overlap_text, line_str] if overlap_text else [line_str]
                current_len = sum(len(x) + 1 for x in current_buffer)
            else:
                current_buffer.append(line_str)
                current_len += line_len + 1

        if current_buffer:
            body = " ".join(current_buffer)
            if body.strip():
                cid = hashlib.sha256(f"{doc_id}:{chunk_counter}:{body[:64]}".encode()).hexdigest()[:16]
                chunks.append(
                    PassageChunk(
                        chunk_id=cid,
                        doc_id=doc_id,
                        chunk_index=chunk_counter,
                        text=body,
                        section=current_section,
                        page=page_num,
                    )
                )
                chunk_counter += 1

    return chunks


class KnowledgeIndexer:
    """Indexador transacional de documentos locais com SQLite e FTS5."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        read_only: bool = False,
    ) -> None:
        self.path = Path(db_path) if db_path is not None else DEFAULT_KNOWLEDGE_DB
        self.read_only = read_only
        self.embedding = embedding_provider or HashEmbeddingProvider()

        if not self.read_only:
            self.path = private_file(self.path)
        else:
            if not self.path.exists():
                raise FileNotFoundError(f"Banco não encontrado: {self.path}")

        self.conn = sqlite3.connect(
            f"file:{self.path.resolve()}?mode=ro" if self.read_only else str(self.path),
            uri=bool(self.read_only),
            timeout=15,
        )
        self.conn.execute("PRAGMA foreign_keys = ON")
        if not self.read_only:
            self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    format TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )"""
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_checksum ON sources(checksum)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_collection ON sources(collection)")

            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    section TEXT NOT NULL,
                    page INTEGER,
                    vector TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )"""
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id)")

            self.conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    text,
                    section,
                    chunk_id UNINDEXED,
                    source_id UNINDEXED,
                    tokenize="unicode61 remove_diacritics 2"
                )"""
            )

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def __enter__(self) -> KnowledgeIndexer:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def ingest_file(
        self,
        file_path: str | Path,
        title: str | None = None,
        author: str | None = None,
        collection: str = "default",
        metadata: dict[str, Any] | None = None,
        force_reindex: bool = False,
    ) -> DocumentRecord:
        """Processa e indexa arquivo (PDF, DOCX, TXT, MD, etc.) gerando chunks e embeddings."""
        if self.read_only:
            raise PermissionError("Instância aberta apenas para leitura")

        path = Path(file_path).resolve()
        raw_bytes = _safe_read_document(path)
        checksum = hashlib.sha256(raw_bytes).hexdigest()

        # Verifica se já existe pelo checksum
        existing = self.conn.execute(
            "SELECT id, title, active FROM sources WHERE checksum = ?", (checksum,)
        ).fetchone()

        if existing and not force_reindex:
            doc_id, existing_title, is_active = existing
            if not is_active:
                with self.conn:
                    self.conn.execute(
                        "UPDATE sources SET active = 1, updated_at = ? WHERE id = ?",
                        (int(time.time()), doc_id),
                    )
            rec = self.get_document(doc_id)
            if rec is not None:
                return rec

        ext = path.suffix.lower()
        doc_title = title or path.stem.replace("_", " ").title()
        doc_author = author or "UNKNOWN"
        doc_meta = dict(metadata or {})

        # Extração de páginas estruturadas
        pages: list[tuple[int | None, str]] = []
        if ext in {".txt", ".md", ".csv", ".json", ".log"}:
            text_content = raw_bytes.decode("utf-8", errors="replace")
            if len(text_content) > MAX_TEXT_CHARS:
                raise ValueError(f"Texto extraído excede limite de {MAX_TEXT_CHARS} caracteres")
            pages = [(1, text_content)]
        else:
            try:
                extracted = extract_known(path)
                pages = extracted.get("pages", [])
                doc_author = author or extracted.get("author", doc_author)
                doc_title = title or extracted.get("title", doc_title)
            except Exception as exc:
                # Fallback para pdftotext se for PDF
                if ext == ".pdf" and shutil.which("pdftotext"):
                    import subprocess
                    import tempfile

                    with tempfile.TemporaryDirectory(prefix="mark-pdf-") as tmp:
                        out_txt = Path(tmp) / "out.txt"
                        res = subprocess.run(
                            ["pdftotext", "-layout", "-enc", "UTF-8", str(path), str(out_txt)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=20,
                        )
                        if res.returncode == 0 and out_txt.is_file():
                            content = out_txt.read_text(encoding="utf-8", errors="replace")
                            parts = content.split("\f")
                            pages = [(i + 1, p) for i, p in enumerate(parts) if p.strip()]
                if not pages:
                    raise ValueError(f"Não foi possível extrair texto do documento ({ext}): {exc}") from exc

        if not pages:
            raise ValueError("O documento não contém texto legível ou camada de texto")

        doc_id = hashlib.sha256(f"{doc_title}:{checksum}".encode()).hexdigest()[:12]
        chunks = _chunk_text_pages(pages, doc_id=doc_id)

        now = int(time.time())
        doc_record = DocumentRecord(
            doc_id=doc_id,
            title=doc_title,
            author=doc_author,
            file_path=str(path),
            format=ext.lstrip("."),
            checksum=checksum,
            collection=collection,
            metadata=doc_meta,
            created_at=now,
            updated_at=now,
            active=True,
        )

        with self.conn:
            # Se for reindex, remove chunks antigos
            self.remove_document(doc_id)

            self.conn.execute(
                """INSERT INTO sources (id, title, author, file_path, format, checksum,
                                        collection, metadata, created_at, updated_at, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    doc_record.doc_id,
                    doc_record.title,
                    doc_record.author,
                    doc_record.file_path,
                    doc_record.format,
                    doc_record.checksum,
                    doc_record.collection,
                    json.dumps(doc_record.metadata, ensure_ascii=False),
                    doc_record.created_at,
                    doc_record.updated_at,
                ),
            )

            for chunk in chunks:
                vec = self.embedding.embed(chunk.text)
                chunk.vector = vec
                self.conn.execute(
                    """INSERT INTO chunks (id, source_id, chunk_index, text, section, page, vector, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.section,
                        chunk.page,
                        json.dumps(chunk.vector),
                        json.dumps(chunk.metadata, ensure_ascii=False),
                    ),
                )
                self.conn.execute(
                    """INSERT INTO chunks_fts (text, section, chunk_id, source_id)
                       VALUES (?, ?, ?, ?)""",
                    (chunk.text, chunk.section, chunk.chunk_id, chunk.doc_id),
                )

        return doc_record

    def index_text(
        self,
        title: str,
        content: str,
        author: str = "SYSTEM",
        collection: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Indexa diretamente um bloco de texto sem necessidade de arquivo físico no disco."""
        if self.read_only:
            raise PermissionError("Instância aberta apenas para leitura")

        raw_bytes = content.encode("utf-8")
        checksum = hashlib.sha256(raw_bytes).hexdigest()
        doc_id = hashlib.sha256(f"{title}:{checksum}".encode()).hexdigest()[:12]
        pages = [(1, content)]
        chunks = _chunk_text_pages(pages, doc_id=doc_id)

        now = int(time.time())
        doc_record = DocumentRecord(
            doc_id=doc_id,
            title=title,
            author=author,
            file_path=f"memory://{doc_id}",
            format="text",
            checksum=checksum,
            collection=collection,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
            active=True,
        )

        with self.conn:
            self.remove_document(doc_id)
            self.conn.execute(
                """INSERT INTO sources (id, title, author, file_path, format, checksum,
                                        collection, metadata, created_at, updated_at, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    doc_record.doc_id,
                    doc_record.title,
                    doc_record.author,
                    doc_record.file_path,
                    doc_record.format,
                    doc_record.checksum,
                    doc_record.collection,
                    json.dumps(doc_record.metadata, ensure_ascii=False),
                    doc_record.created_at,
                    doc_record.updated_at,
                ),
            )

            for chunk in chunks:
                vec = self.embedding.embed(chunk.text)
                chunk.vector = vec
                self.conn.execute(
                    """INSERT INTO chunks (id, source_id, chunk_index, text, section, page, vector, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.section,
                        chunk.page,
                        json.dumps(chunk.vector),
                        json.dumps(chunk.metadata, ensure_ascii=False),
                    ),
                )
                self.conn.execute(
                    """INSERT INTO chunks_fts (text, section, chunk_id, source_id)
                       VALUES (?, ?, ?, ?)""",
                    (chunk.text, chunk.section, chunk.chunk_id, chunk.doc_id),
                )

        return doc_id

    def remove_document(self, doc_id: str) -> bool:
        """Remove completamente um documento e todos os seus chunks e índices FTS."""
        if self.read_only:
            raise PermissionError("Instância aberta apenas para leitura")

        with self.conn:
            # Remove do FTS
            self.conn.execute("DELETE FROM chunks_fts WHERE source_id = ?", (doc_id,))
            # Remove dos chunks
            self.conn.execute("DELETE FROM chunks WHERE source_id = ?", (doc_id,))
            # Remove da tabela sources
            cur = self.conn.execute("DELETE FROM sources WHERE id = ?", (doc_id,))
            return cur.rowcount > 0

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        row = self.conn.execute(
            """SELECT id, title, author, file_path, format, checksum,
                      collection, metadata, created_at, updated_at, active
               FROM sources WHERE id = ?""",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        return DocumentRecord(
            doc_id=row[0],
            title=row[1],
            author=row[2],
            file_path=row[3],
            format=row[4],
            checksum=row[5],
            collection=row[6],
            metadata=json.loads(row[7]),
            created_at=row[8],
            updated_at=row[9],
            active=bool(row[10]),
        )

    def get_chunk(self, chunk_id: str) -> PassageChunk | None:
        row = self.conn.execute(
            """SELECT id, source_id, chunk_index, text, section, page, vector, metadata
               FROM chunks WHERE id = ?""",
            (chunk_id,),
        ).fetchone()
        if not row:
            return None
        return PassageChunk(
            chunk_id=row[0],
            doc_id=row[1],
            chunk_index=row[2],
            text=row[3],
            section=row[4],
            page=row[5],
            vector=json.loads(row[6]),
            metadata=json.loads(row[7]),
        )

    def list_documents(
        self, collection: str | None = None, active_only: bool = True
    ) -> list[DocumentRecord]:
        query = "SELECT id, title, author, file_path, format, checksum, collection, metadata, created_at, updated_at, active FROM sources WHERE 1=1"
        params: list[Any] = []
        if active_only:
            query += " AND active = 1"
        if collection is not None:
            query += " AND collection = ?"
            params.append(collection)
        query += " ORDER BY updated_at DESC"

        rows = self.conn.execute(query, params).fetchall()
        return [
            DocumentRecord(
                doc_id=r[0],
                title=r[1],
                author=r[2],
                file_path=r[3],
                format=r[4],
                checksum=r[5],
                collection=r[6],
                metadata=json.loads(r[7]),
                created_at=r[8],
                updated_at=r[9],
                active=bool(r[10]),
            )
            for r in rows
        ]

    def stats(self) -> dict[str, Any]:
        sources_count = self.conn.execute("SELECT count(*) FROM sources WHERE active = 1").fetchone()[0]
        chunks_count = self.conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        db_size = self.path.stat().st_size if self.path.exists() else 0
        return {
            "sources": sources_count,
            "chunks": chunks_count,
            "db_bytes": db_size,
            "db_path": str(self.path),
            "embedding_model": getattr(self.embedding, "model", "custom"),
            "embedding_dimensions": getattr(self.embedding, "dimensions", 128),
        }
