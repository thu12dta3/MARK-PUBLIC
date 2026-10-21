from collections import deque
from contextlib import contextmanager
import os
import re
import json
import hmac
import sqlite3
import stat
import time
from hashlib import sha256
from pathlib import Path
from datetime import datetime, timezone

from .scopes import MemoryScope, ScopedMemory, validate_scope_identifier, resolve_query_scopes
from .dedup import DeduplicationEngine, MemoryConflict, compute_fact_fingerprint, normalize_fact_text
from .portable import create_portable_payload, parse_portable_payload, ImportReport, PORTABLE_SCHEMA_VERSION


class ShortMemory:
    def __init__(self, limit: int = 6):
        self.items = deque(maxlen=max(1, limit))

    def add(self, user: str, assistant: str, source: str = "mark") -> None:
        self.items.append({"user": user, "assistant": assistant, "created_at": int(time.time()), "source": source})

    def clear(self) -> None:
        self.items.clear()

    def history(self, limit: int = 30) -> list[dict]:
        return list(self.items)[-max(1, limit):]

    def render(self) -> str:
        return "\n".join(f"{user_prefix(x.get('source', 'mark'))}: {x['user']}\nMARK: {x['assistant']}" for x in self.items) or "Nenhuma."


def source_label(source: str) -> str:
    if source.startswith("desktop-"):
        return "PC"
    if source.startswith("wear-"):
        return "relógio"
    if source.startswith("mobile-"):
        return "celular"
    if source.startswith("legacy-"):
        return "outro cliente"
    return ""


def user_prefix(source: str) -> str:
    label = source_label(source)
    return f"Usuário ({label})" if label else "Usuário"


class SharedMemory:


    DEFAULT_PATH = Path.home() / ".local/state/mark/conversation.sqlite3"

    def __init__(self, limit: int = 6, path: Path | None = None):
        self.limit = max(1, limit)
        self.path = path or self.DEFAULT_PATH
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.path.parent.stat()
        if parent.st_uid != os.getuid():
            raise PermissionError("A pasta do histórico do MARK precisa pertencer ao usuário.")
        self.path.parent.chmod(0o700)
        if not self.path.exists():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                os.close(os.open(self.path, flags, 0o600))
            except FileExistsError:
                pass
        details = self.path.lstat()
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o077:
            raise PermissionError("O histórico do MARK precisa ser privado do usuário (0600).")
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, source TEXT NOT NULL, user_text TEXT NOT NULL, assistant_text TEXT NOT NULL)")

            db.execute("""CREATE TABLE IF NOT EXISTS scoped_memories (
                id INTEGER PRIMARY KEY,
                scope TEXT NOT NULL CHECK(scope IN ('global','specialization','project','task')),
                scope_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                content TEXT NOT NULL,
                provenance TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(scope, scope_id, fingerprint)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS scoped_memories_scope ON scoped_memories(scope, scope_id, updated_at DESC)")

            db.execute("""CREATE TABLE IF NOT EXISTS memory_conflicts (
                id TEXT PRIMARY KEY,
                existing_memory_id INTEGER NOT NULL REFERENCES scoped_memories(id) ON DELETE CASCADE,
                scope TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                existing_content TEXT NOT NULL,
                new_content TEXT NOT NULL,
                reason TEXT NOT NULL,
                similarity_score REAL NOT NULL,
                detected_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING'
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS memory_conflicts_status ON memory_conflicts(status, detected_at DESC)")
        self.dedup_engine = DeduplicationEngine()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, user: str, assistant: str, source: str = "mark") -> None:
        with self._db() as db:
            db.execute("INSERT INTO turns (created_at, source, user_text, assistant_text) VALUES (?, ?, ?, ?)",
                       (int(time.time()), source, user, assistant))


        try:
            from ..honcho_memory import enqueue_turn
            enqueue_turn(user, assistant)
        except Exception:
            pass

    def clear(self) -> None:
        with self._db() as db:
            db.execute("DELETE FROM turns")

    def history(self, limit: int = 30) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT created_at, source, user_text, assistant_text FROM turns ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        rows.reverse()
        return [
            {"created_at": created, "source": source, "user": user, "assistant": assistant}
            for created, source, user, assistant in rows
        ]

    def render(self) -> str:
        with self._db() as db:
            rows = db.execute("SELECT source, user_text, assistant_text FROM turns ORDER BY id DESC LIMIT ?",
                              (self.limit,)).fetchall()
        rows.reverse()
        return "\n".join(f"{user_prefix(source)}: {user[:600]}\nMARK: {assistant[:800]}"
                         for source, user, assistant in rows) or "Nenhuma."

    def remember_fact(self, content: str, scope: str = "global", context=None,
                      provenance: str = "explicit_user_request", check_conflicts: bool = True) -> dict:

        from ..core.context import CoreContext

        context = context if isinstance(context, CoreContext) else CoreContext.create()
        if scope not in {"global", "specialization", "project", "task"}:
            raise ValueError("Escopo de memória inválido")
        if not isinstance(content, str) or not content.strip() or len(content) > 4000:
            raise ValueError("Memória vazia ou longa demais")
        if not isinstance(provenance, str) or not provenance.strip() or len(provenance) > 128:
            raise ValueError("Proveniência inválida")
        scope_id = {
            "global": "",
            "specialization": context.specialization,
            "project": context.project_id,
            "task": context.task_id,
        }[scope]
        if scope_id is None:
            raise ValueError("O contexto atual não tem esse identificador")
        normalized = " ".join(content.split())
        fingerprint = sha256(normalized.casefold().encode("utf-8")).hexdigest()
        now = int(time.time())

        conflicts = []
        if check_conflicts:
            existing = self.list_facts(scope=scope, scope_id=scope_id)
            conflicts = self.dedup_engine.detect_conflicts(normalized, existing, scope, scope_id)

        with self._db() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO scoped_memories "
                "(scope, scope_id, fingerprint, content, provenance, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scope, scope_id, fingerprint, normalized, provenance.strip(), now, now),
            )
            saved = cursor.rowcount == 1
            if conflicts and saved:
                for c in conflicts:
                    db.execute(
                        "INSERT OR REPLACE INTO memory_conflicts "
                        "(id, existing_memory_id, scope, scope_id, existing_content, new_content, reason, similarity_score, detected_at, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (c.conflict_id, c.existing_memory_id, c.scope, c.scope_id, c.existing_content, c.new_content, c.reason, c.similarity_score, c.detected_at, c.status),
                    )

        result = {"saved": saved, "scope": scope, "scope_id": scope_id}
        if conflicts:
            result["conflicts"] = [c.to_dict() for c in conflicts]
        return result

    def list_conflicts(self, status: str = "PENDING") -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id, existing_memory_id, scope, scope_id, existing_content, new_content, "
                "reason, similarity_score, detected_at, status FROM memory_conflicts "
                "WHERE (? IS NULL OR status = ?) ORDER BY detected_at DESC",
                (status, status),
            ).fetchall()
        return [
            {
                "conflict_id": r[0],
                "existing_memory_id": r[1],
                "scope": r[2],
                "scope_id": r[3],
                "existing_content": r[4],
                "new_content": r[5],
                "reason": r[6],
                "similarity_score": r[7],
                "detected_at": r[8],
                "status": r[9],
            }
            for r in rows
        ]

    def resolve_conflict(self, conflict_id: str, choice: str = "use_new") -> bool:
        valid_choices = {"use_new", "keep_existing", "keep_both"}
        if choice not in valid_choices:
            raise ValueError(f"Escolha de resolução inválida: '{choice}'. Use: {', '.join(valid_choices)}")
        with self._db() as db:
            c = db.execute(
                "SELECT id, existing_memory_id, new_content, scope, scope_id, status FROM memory_conflicts WHERE id = ?",
                (conflict_id,),
            ).fetchone()
            if not c:
                return False
            _, existing_id, new_content, scope, scope_id, status = c
            if choice == "use_new":
                norm = " ".join(new_content.split())
                fp = sha256(norm.casefold().encode("utf-8")).hexdigest()
                existing_row = db.execute(
                    "SELECT id FROM scoped_memories WHERE scope = ? AND scope_id = ? AND fingerprint = ?",
                    (scope, scope_id, fp),
                ).fetchone()
                if existing_row:
                    db.execute("DELETE FROM scoped_memories WHERE id = ?", (existing_id,))
                else:
                    db.execute(
                        "UPDATE scoped_memories SET content = ?, fingerprint = ?, updated_at = ? WHERE id = ?",
                        (norm, fp, int(time.time()), existing_id),
                    )
                db.execute("UPDATE memory_conflicts SET status = 'RESOLVED_USE_NEW' WHERE id = ?", (conflict_id,))
            elif choice == "keep_existing":
                norm = " ".join(new_content.split())
                fp = sha256(norm.casefold().encode("utf-8")).hexdigest()
                db.execute(
                    "DELETE FROM scoped_memories WHERE scope = ? AND scope_id = ? AND fingerprint = ? AND id != ?",
                    (scope, scope_id, fp, existing_id),
                )
                db.execute("UPDATE memory_conflicts SET status = 'RESOLVED_KEEP_EXISTING' WHERE id = ?", (conflict_id,))
            elif choice == "keep_both":
                norm = " ".join(new_content.split())
                fp = sha256(norm.casefold().encode("utf-8")).hexdigest()
                now = int(time.time())
                db.execute(
                    "INSERT OR IGNORE INTO scoped_memories "
                    "(scope, scope_id, fingerprint, content, provenance, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'resolved_conflict_both', ?, ?)",
                    (scope, scope_id, fp, norm, now, now),
                )
                db.execute("UPDATE memory_conflicts SET status = 'RESOLVED_KEEP_BOTH' WHERE id = ?", (conflict_id,))
            return True

    def recall_facts(self, query: str, context=None, limit: int = 6,
                     character_budget: int = 3000) -> list[dict]:

        from ..core.context import CoreContext

        if not isinstance(query, str) or not query.strip():
            return []
        context = context if isinstance(context, CoreContext) else CoreContext.create()
        allowed = [("global", "")]
        allowed.append(("specialization", context.specialization))
        if context.project_id:
            allowed.append(("project", context.project_id))
        if context.task_id:
            allowed.append(("task", context.task_id))
        terms = set(re.findall(r"[\wÀ-ÿ]{3,}", query.casefold()))
        if not terms:
            return []
        with self._db() as db:
            rows = []
            for scope, scope_id in allowed:
                rows.extend(db.execute(
                    "SELECT scope, scope_id, content, provenance, created_at "
                    "FROM scoped_memories WHERE scope=? AND scope_id=? "
                    "ORDER BY updated_at DESC LIMIT 200", (scope, scope_id),
                ).fetchall())
        ranked = []
        for scope, scope_id, content, provenance, created_at in rows:
            matches = len(terms & set(re.findall(r"[\wÀ-ÿ]{3,}", content.casefold())))
            if matches:
                ranked.append((matches, created_at, scope, scope_id, content, provenance))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        selected = []
        used = 0
        for matches, created_at, scope, scope_id, content, provenance in ranked[:max(0, min(limit, 20))]:
            if used + len(content) > max(0, character_budget):
                continue
            selected.append({"scope": scope, "scope_id": scope_id, "content": content,
                             "provenance": provenance, "created_at": created_at,
                             "matched_terms": matches})
            used += len(content)
        return selected

    def list_facts(self, query: str = "", scope: str | None = None,
                   scope_id: str | None = None, limit: int = 500) -> list[dict]:

        if not isinstance(query, str) or len(query) > 500:
            raise ValueError("Busca inválida")
        if scope is not None and scope not in {"global", "specialization", "project", "task"}:
            raise ValueError("Escopo inválido")
        if scope_id is not None and (not isinstance(scope_id, str) or len(scope_id) > 128):
            raise ValueError("Identificador de escopo inválido")
        terms = set(re.findall(r"[\wÀ-ÿ]{2,}", query.casefold()))
        with self._db() as db:
            rows = db.execute(
                "SELECT id, scope, scope_id, content, provenance, created_at, updated_at "
                "FROM scoped_memories WHERE (? IS NULL OR scope=?) "
                "AND (? IS NULL OR scope_id=?) ORDER BY updated_at DESC, id DESC",
                (scope, scope, scope_id, scope_id),
            ).fetchall()
        if terms:
            rows = [row for row in rows if terms <= set(re.findall(r"[\wÀ-ÿ]{2,}", row[3].casefold()))]
        rows = rows[:max(1, min(int(limit), 2000))]
        return [dict(id=row[0], scope=row[1], scope_id=row[2], content=row[3],
                     provenance=row[4], created_at=row[5], updated_at=row[6]) for row in rows]

    def update_fact(self, memory_id: int, content: str) -> bool:
        if type(memory_id) is not int or memory_id <= 0:
            raise ValueError("Identificador de memória inválido")
        if not isinstance(content, str) or not content.strip() or len(content) > 4000:
            raise ValueError("Memória vazia ou longa demais")
        normalized = " ".join(content.split())
        fingerprint = sha256(normalized.casefold().encode("utf-8")).hexdigest()
        try:
            with self._db() as db:
                changed = db.execute(
                    "UPDATE scoped_memories SET content=?, fingerprint=?, updated_at=? WHERE id=?",
                    (normalized, fingerprint, int(time.time()), memory_id),
                ).rowcount
        except sqlite3.IntegrityError:
            raise ValueError("Já existe uma memória igual nesse escopo") from None
        return bool(changed)

    def delete_fact(self, memory_id: int) -> bool:
        if type(memory_id) is not int or memory_id <= 0:
            raise ValueError("Identificador de memória inválido")
        with self._db() as db:
            return bool(db.execute("DELETE FROM scoped_memories WHERE id=?", (memory_id,)).rowcount)

    def facts_backup(self) -> bytes:

        with self._db() as db:
            count = db.execute("SELECT COUNT(*) FROM scoped_memories").fetchone()[0]
        if count > 2000:
            raise ValueError("O backup suporta até 2.000 memórias explícitas; nenhum dado foi exportado.")
        entries = self.list_facts(limit=2000)
        payload = {"schema_version": 1, "kind": "mark_explicit_memories",
                   "created_at": datetime.now(timezone.utc).isoformat(), "entries": entries}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        payload["sha256"] = sha256(canonical).hexdigest()
        return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

    def export_facts(self, destination: Path, passphrase: str | None = None, encrypted: bool = False) -> int:
        path = Path(destination).expanduser()
        if encrypted or passphrase:
            entries = self.list_facts(limit=5000)
            data = create_portable_payload(entries, encrypted=True, passphrase=passphrase)
        else:
            data = self.facts_backup()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            raise FileExistsError("O arquivo de destino já existe; escolha outro nome") from None
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if encrypted or passphrase:
            return len(entries)
        return len(json.loads(data)["entries"])

    def restore_facts(self, source: Path | bytes, passphrase: str | None = None) -> dict:
        if isinstance(source, bytes):
            raw = source
        else:
            path = Path(source)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16_000_000:
                raise ValueError("Backup inválido ou grande demais")
            raw = path.read_bytes()
        if len(raw) > 16_000_000:
            raise ValueError("Backup inválido ou grande demais")

        payload = parse_portable_payload(raw, passphrase=passphrase)
        entries = payload.get("entries", [])
        if not isinstance(entries, list) or len(entries) > 5000:
            raise ValueError("Formato de backup não reconhecido")

        existing_facts = self.list_facts(limit=5000)
        prepared = []
        conflicts_count = 0

        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Entrada de memória inválida")
            scope, scope_id = entry.get("scope"), entry.get("scope_id")
            content, provenance = entry.get("content"), entry.get("provenance")
            if scope not in {"global", "specialization", "project", "task"}:
                raise ValueError("Escopo de memória inválido")
            if (not isinstance(scope_id, str) or len(scope_id) > 128
                    or (scope == "global" and scope_id != "")
                    or (scope != "global" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", scope_id))):
                raise ValueError("Identificador de escopo inválido")
            if (not isinstance(content, str) or not content.strip() or len(content) > 4000
                    or not isinstance(provenance, str) or not provenance.strip() or len(provenance) > 128):
                raise ValueError("Conteúdo de memória inválido")
            normalized = " ".join(content.split())
            fp = sha256(normalized.casefold().encode("utf-8")).hexdigest()

            # Detecção de conflito na importação sem exclusão silenciosa
            conflicts = self.dedup_engine.detect_conflicts(normalized, existing_facts, scope, scope_id)
            if conflicts:
                conflicts_count += len(conflicts)
                with self._db() as db:
                    for c in conflicts:
                        db.execute(
                            "INSERT OR REPLACE INTO memory_conflicts "
                            "(id, existing_memory_id, scope, scope_id, existing_content, new_content, reason, similarity_score, detected_at, status) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (c.conflict_id, c.existing_memory_id, c.scope, c.scope_id, c.existing_content, c.new_content, c.reason, c.similarity_score, c.detected_at, c.status),
                        )

            prepared.append((scope, scope_id, fp, normalized, provenance.strip(), int(time.time()), int(time.time())))

        inserted = 0
        with self._db() as db:
            for row in prepared:
                inserted += db.execute(
                    "INSERT OR IGNORE INTO scoped_memories "
                    "(scope, scope_id, fingerprint, content, provenance, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", row,
                ).rowcount

        res = {"inserted": inserted, "duplicates": len(prepared) - inserted}
        if conflicts_count > 0:
            res["conflicts_flagged"] = conflicts_count
        return res
