
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import date
import hashlib
import json
from urllib.parse import quote
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from .domains import Domain, domain, REGISTRY
from .embeddings import EmbeddingProvider, HashEmbeddingProvider, tokens, cosine
from .schemas import SourceRecord, ChunkRecord, Confidence, Freshness, SCHEMA_VERSION
from .storage import private_file

DEFAULT_DB = Path.home() / '.local/state/mark/cognition/knowledge.sqlite3'
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TEXT = 2_000_000
STOP_WORDS = set("a o os as de da do das dos e em no na nos nas um uma uns umas que qual quais como para por com se isso isto voce eu me ao esta estou estamos sao ser esse essa estes essas the of and or is are to in on for with what which how this that it an i you oi ola alo bom boa dia tarde noite tudo bem obrigado obrigada valeu tchau ate mais mark".split())

@dataclass(frozen=True)
class Citation:
    source_id: str
    title: str
    section: str
    page: int | None
    date: str | None
    confidence: str
    freshness: str
    source_type: str
    url: str
    version: int
    claim_status: str = 'UNKNOWN'
    source_tier: str = 'UNKNOWN'
    def to_dict(self): return asdict(self)

@dataclass(frozen=True)
class SearchHit:
    chunk: ChunkRecord
    citation: Citation
    score: float
    def to_dict(self): return {'chunk': self.chunk.to_dict(), 'citation': self.citation.to_dict(), 'score': self.score}

class KnowledgeBackend(ABC):
    @abstractmethod
    def search(self, query: str, domains=(), limit=5, **filters) -> list[SearchHit]: ...
    @abstractmethod
    def stats(self) -> dict: ...

def freshness(source: SourceRecord, today: date | None = None) -> Freshness:
    m = source.metadata
    if m.get('historical') is True or source.domain in (Domain.HISTORY, Domain.MILITARY_HISTORY): return Freshness.HISTORICAL
    stamp = m.get('last_verified') or m.get('version_date') or source.publication_date
    if not stamp: return Freshness.UNKNOWN
    try: age = ((today or date.today()) - date.fromisoformat(stamp)).days
    except (ValueError, TypeError): return Freshness.UNKNOWN
    if age < 0: return Freshness.UNKNOWN
    if m.get('valid_until') and date.fromisoformat(m['valid_until']) < (today or date.today()): return Freshness.STALE
    return Freshness.STALE if age > (90 if source.domain == Domain.LAW else 180) else Freshness.CURRENT

def read_document(path):
    if path.is_symlink(): raise ValueError('Symlink não permitido')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES: raise ValueError('Documento inválido ou maior que 8 MiB')
        data = stream.read(MAX_FILE_BYTES+1)
    if len(data)>MAX_FILE_BYTES: raise ValueError('Documento excede o limite')
    return data

def extract_file(path: Path, data=None) -> list[tuple[int | None, str]]:
    data = read_document(path) if data is None else data
    if path.suffix.lower() in {'.txt', '.md'}:
        text = data.decode('utf-8')
        if len(text) > MAX_TEXT or '\x00' in text: raise ValueError('Texto inválido ou excede o limite')
        return [(None, text)]
    if path.suffix.lower() != '.pdf': raise ValueError('Formato não suportado: use TXT, MD ou PDF')
    if not shutil.which('pdftotext'): raise RuntimeError('PDF requer pdftotext (Poppler)')
    if data[:5] != b'%PDF-': raise ValueError('PDF inválido')
    with tempfile.TemporaryDirectory(prefix='mark-ingest-') as tmp:
        out = Path(tmp) / 'text.txt'
        snapshot = Path(tmp) / 'input.pdf'; snapshot.write_bytes(data)
        command = ['pdftotext', '-layout', '-enc', 'UTF-8', str(snapshot), str(out)]
        if shutil.which('prlimit'):
            command = ['prlimit', '--as=536870912', '--cpu=12', '--fsize=8388608', '--'] + command
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if result.returncode or not out.is_file() or out.stat().st_size > MAX_FILE_BYTES: raise ValueError('PDF não pôde ser extraído dentro dos limites')
        text = out.read_text(encoding='utf-8')
        if len(text) > MAX_TEXT: raise ValueError('Texto do PDF excede o limite')
        pages = [(i+1, page) for i, page in enumerate(text.split('\f')) if page.strip()]
        if not pages: raise ValueError('OCR_REQUIRED: PDF sem camada de texto')
        return pages

def chunk_pages(pages, source: SourceRecord, embedding: EmbeddingProvider):
    result = []
    section = ''
    for page, text in pages:

        pieces = re.split(r'(?mi)(^#{1,6}\s+[^\n]+$|^\s*(?:cap[ií]tulo|chapter)\s+\d+[^\n]{0,150}$)', text)
        for piece in pieces:
            if not piece.strip(): continue
            if re.match(r'(?i)^(?:#{1,6}\s+|\s*(?:cap[ií]tulo|chapter)\s+\d+)', piece): section = piece.lstrip('# ').strip()[:1000]; continue
            piece = piece.strip()
            for start in range(0, len(piece), 1280):
                body = piece[start:start+1400]
                if not body.strip(): continue
                chunk_id = hashlib.sha256(f'{source.source_id}:{len(result)}'.encode()).hexdigest()
                result.append(ChunkRecord(chunk_id, source.source_id, source.domain, body, section, page,
                                          metadata={'claims': source.metadata.get('claims', {}), 'source_metadata': source.metadata, 'position': start, 'chunk_number': len(result)}, embedding_model=embedding.model, embedding_version=embedding.version))
                if start + 1400 >= len(piece): break
                if len(result) > 5000: raise ValueError('Documento gera chunks demais')
    if not result: raise ValueError('Documento vazio')
    return result

class SQLiteKnowledgeStore(KnowledgeBackend):
    def __init__(self, path: Path = DEFAULT_DB, embedding: EmbeddingProvider | None = None, read_only=False):
        self.path = Path(path) if read_only else private_file(path)
        if read_only:
            details = self.path.lstat()
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o077: raise PermissionError('Banco privado inválido')
        self.embedding = embedding or HashEmbeddingProvider()
        self.db = sqlite3.connect('file:'+quote(str(self.path))+'?mode=ro', timeout=10, uri=True) if read_only else sqlite3.connect(self.path, timeout=10)
        try:
            self._initialize(read_only)
        except BaseException:
            self.db.close()
            raise

    def _initialize(self, read_only):
        self.db.execute('PRAGMA foreign_keys=ON')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, SCHEMA_VERSION): self.db.close(); raise ValueError('Versão do banco não suportada; migração necessária')
        if read_only:
            if version != SCHEMA_VERSION: self.db.close(); raise ValueError('Versão do banco inválida')
            return
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, group_id TEXT NOT NULL, record TEXT NOT NULL, active INTEGER NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS sources_group ON sources(group_id)')
            self.db.execute('CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), record TEXT NOT NULL, vector TEXT NOT NULL)')
            self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(text, title, chunk_id UNINDEXED, tokenize="unicode61 remove_diacritics 2")')
            self.db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def ingest(self, path: Path, domain_name, *, title: str, license: str, metadata=None, **fields):
        started = time.perf_counter()
        p = Path(path).absolute()
        raw = read_document(p)
        pages = extract_file(p, raw)
        checksum = hashlib.sha256(raw).hexdigest()
        return self.ingest_pages(p, pages, checksum, domain_name, title=title, license=license, metadata=metadata, **fields)

    def ingest_pages(self, p, pages, checksum, domain_name, *, title, license, metadata=None, **fields):
        started = time.perf_counter()
        metadata = {} if metadata is None else metadata
        d = domain(domain_name)

        group = hashlib.sha256(json.dumps([d.value, metadata.get('document_key', title), fields.get('url','')], sort_keys=True).encode()).hexdigest()
        identity = hashlib.sha256(json.dumps([group, checksum, license, metadata, fields], sort_keys=True).encode()).hexdigest()
        previous = self.db.execute('SELECT record FROM sources WHERE id=?', (identity,)).fetchone()
        if previous:
            return {'status': 'DUPLICATE', 'source_id': identity, 'version': json.loads(previous[0])['version'], 'latency_ms': (time.perf_counter()-started)*1000}
        versions = [json.loads(row[0])['version'] for row in self.db.execute('SELECT record FROM sources WHERE group_id=?', (group,))]
        source = SourceRecord(identity, d, title, license, checksum, local_path=str(p), metadata=metadata, version=max(versions, default=0)+1, **fields)
        chunks = chunk_pages(pages, source, self.embedding)
        vectors = [self.embedding.embed(c.text) for c in chunks]
        for v in vectors:
            if len(v) != self.embedding.dimensions: raise ValueError('Provider retornou dimensão inválida')
        with self.db:
            self.db.execute('UPDATE sources SET active=0 WHERE group_id=?', (group,))
            self.db.execute('INSERT INTO sources VALUES (?,?,?,1)', (identity,group,json.dumps(source.to_dict())))
            for c,v in zip(chunks, vectors):
                self.db.execute('INSERT INTO chunks VALUES (?,?,?,?)', (c.chunk_id,identity,json.dumps(c.to_dict()),json.dumps(v)))
                self.db.execute('INSERT INTO chunk_fts VALUES (?,?,?)', (c.text,title,c.chunk_id))
        return {'status': 'INDEXED', 'source_id': identity, 'version': source.version, 'chunks': len(chunks), 'latency_ms': (time.perf_counter()-started)*1000}

    def search(self, query: str, domains=(), limit=5, source_types=(), confidence=None, include_history=False, collections=()) -> list[SearchHit]:
        if not isinstance(query, str) or len(query) > 10000: raise ValueError('Consulta inválida')
        if type(limit) is not int or not 1 <= limit <= 20: raise ValueError('Limite inválido')
        terms = list(dict.fromkeys(t for t in tokens(query) if t not in STOP_WORDS and len(t) >= 2))[:32]
        if not terms: return []
        domains = {domain(d).value for d in domains}
        expression = ' OR '.join('"' + t + '"' for t in terms)
        query_vector = self.embedding.embed(query)
        hits = []
        query_sql = 'SELECT c.record,c.vector,s.record FROM chunks c JOIN sources s ON c.source_id=s.id WHERE (? OR s.active=1)'
        params = [int(include_history)]

        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='library_documents'").fetchone():
            query_sql += " AND ((COALESCE(json_extract(s.record,'$.metadata.library'),0)=0 AND NOT EXISTS (SELECT 1 FROM library_documents d WHERE d.source_id=s.id)) OR EXISTS (SELECT 1 FROM library_documents d JOIN library_collections l ON d.collection=l.name WHERE d.source_id=s.id AND d.enabled=1 AND l.enabled=1))"
            if collections:
                query_sql += ' AND EXISTS (SELECT 1 FROM library_documents d WHERE d.source_id=s.id AND d.collection IN (' + ','.join('?' for _ in collections) + '))'  # nosec: B608
                params += list(collections)
        where=query_sql.split(' WHERE ',1)[1]
        lexical_sql='SELECT chunk_id,bm25(chunk_fts) FROM chunk_fts JOIN chunks c ON chunk_fts.chunk_id=c.id JOIN sources s ON c.source_id=s.id WHERE '+where+' AND chunk_fts MATCH ? ORDER BY bm25(chunk_fts) LIMIT 500'  # nosec: B608
        lexical={c:float(score) for c,score in self.db.execute(lexical_sql,params+[expression])}
        lexical_rank={key:1/(rank+1) for rank,key in enumerate(lexical)}
        if self.embedding.model.startswith('mark-hash'):
            if not lexical: return []
            query_sql += ' AND c.id IN (' + ','.join('?' for _ in lexical) + ')'
            params += list(lexical)
        rows = self.db.execute(query_sql, params)
        for chunk_json, vector_json, source_json in rows:
            c = ChunkRecord(**json.loads(chunk_json)); s = SourceRecord(**json.loads(source_json))
            if domains and s.domain.value not in domains and not s.metadata.get('library'): continue
            if source_types and s.source_type.value not in source_types: continue
            if confidence and s.reliability.value != confidence: continue
            if c.embedding_model != self.embedding.model or c.embedding_version != self.embedding.version:
                raise ValueError('Embedding mudou: execute reindex')
            semantic = cosine(query_vector, json.loads(vector_json))
            overlaps = len(set(terms) & set(tokens(c.text + ' ' + s.title)))
            if self.embedding.model.startswith('mark-hash') and overlaps / len(terms) < .5: continue

            if c.chunk_id not in lexical and (self.embedding.model.startswith('mark-hash') or semantic < .35): continue
            score = overlaps / len(terms) + max(0, semantic)*.25 + 0.15*lexical_rank.get(c.chunk_id,0)
            if s.source_type.value == 'UNVERIFIED': score *= .5
            cit = Citation(s.source_id,s.title,c.section,c.page,s.publication_date,s.reliability.value,
                           freshness(s).value,s.source_type.value,s.url,s.version,
                           str(s.metadata.get('claim_status','UNKNOWN')),str(s.metadata.get('source_tier','UNKNOWN')))
            hits.append(SearchHit(c,cit,score))
        return sorted(hits, key=lambda h: (-h.score,h.chunk.chunk_id))[:limit]

    def document_passages(self,titles,chapter=None,limit=5):
        if not 1<=limit<=20 or len(titles)>2:raise ValueError('Limite inválido')
        selected=[]
        for title in titles:
            rows=self.db.execute("SELECT c.record,s.record FROM chunks c JOIN sources s ON c.source_id=s.id WHERE s.active=1 AND lower(json_extract(s.record,'$.title'))=lower(?) ORDER BY c.rowid",(title,))
            local=[]
            for chunk_json,source_json in rows:
                c=ChunkRecord(**json.loads(chunk_json));s=SourceRecord(**json.loads(source_json))
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='library_documents'").fetchone():
                    enabled=self.db.execute('SELECT 1 FROM library_documents d JOIN library_collections l ON d.collection=l.name WHERE d.source_id=? AND d.enabled=1 AND l.enabled=1',(s.source_id,)).fetchone()
                    if s.metadata.get('library') and not enabled:continue
                if chapter is not None and not re.search(r'(?i)(?:cap[ií]tulo|chapter)\s+'+str(int(chapter))+r'(?!\d)',c.section):continue
                citation=Citation(s.source_id,s.title,c.section,c.page,s.publication_date,s.reliability.value,freshness(s).value,s.source_type.value,s.url,s.version)
                local.append(SearchHit(c,citation,1.0))
                if len(local)>=limit:break
            selected.extend(local)
        return selected[:20]

    def reindex(self, embedding: EmbeddingProvider | None = None):
        embedding = embedding or self.embedding
        rows = self.db.execute('SELECT id,record FROM chunks').fetchall()
        with self.db:
            for id_,record in rows:
                c=ChunkRecord(**json.loads(record)); c.embedding_model=embedding.model; c.embedding_version=embedding.version
                v=embedding.embed(c.text)
                if len(v) != embedding.dimensions: raise ValueError('Dimensão inválida')
                self.db.execute('UPDATE chunks SET record=?,vector=? WHERE id=?', (json.dumps(c.to_dict()),json.dumps(v),id_))
        self.embedding = embedding
        return {'reindexed': len(rows), 'model': embedding.model, 'version': embedding.version, 'dimensions': embedding.dimensions}

    def stats(self):
        return {'schema_version': SCHEMA_VERSION, 'sources': self.db.execute('SELECT count(*) FROM sources').fetchone()[0],
                'active_sources': self.db.execute('SELECT count(*) FROM sources WHERE active=1').fetchone()[0],
                'chunks': self.db.execute('SELECT count(*) FROM chunks').fetchone()[0], 'index_bytes': self.path.stat().st_size,
                'embedding_model':self.embedding.model, 'embedding_version':self.embedding.version, 'dimensions':self.embedding.dimensions}

KnowledgeStore = SQLiteKnowledgeStore

def conflict_report(hits):


    values = {}
    for hit in hits:
        for key,value in hit.chunk.metadata.get('claims',{}).items():
            values.setdefault(key, {}).setdefault(json.dumps(value,sort_keys=True), []).append(hit.citation.source_id)
    return {key: records for key,records in values.items() if len(records)>1}
