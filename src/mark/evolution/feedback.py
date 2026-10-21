"""Módulo de Captura e Gestão de Feedback Explícito do MARK.

Permite ao usuário avaliar respostas (positivas/negativas), fornecer correções
e categorizar falhas cognitivas (alucinações, citações desancoradas, lógica incorreta),
armazenando os registros em banco SQLite local isolado para auditoria e aprendizado.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

from ..cognition.storage import private_file

logger = logging.getLogger("mark.evolution.feedback")

DEFAULT_FEEDBACK_DB = Path.home() / ".local/state/mark/evolution/feedback.sqlite3"


class FeedbackRating(int, Enum):
    POSITIVE = 1
    NEUTRAL = 0
    NEGATIVE = -1


class FailureCategory(str, Enum):
    HALLUCINATION = "HALLUCINATION"
    UNANCHORED_CITATION = "UNANCHORED_CITATION"
    WRONG_LOGIC = "WRONG_LOGIC"
    INCOMPLETE = "INCOMPLETE"
    STYLE_TONE = "STYLE_TONE"
    PROMPT_MISUNDERSTANDING = "PROMPT_MISUNDERSTANDING"
    OTHER = "OTHER"


@dataclass
class FeedbackEntry:
    id: str
    prompt: str
    response: str
    rating: int
    category: str | None = None
    correction_text: str | None = None
    model: str = "default"
    session_id: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FeedbackCollector:
    """Coletor isolado de feedback explícito e catalogador de falhas cognitivas."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else DEFAULT_FEEDBACK_DB
        self.path = private_file(self.path)
        self.conn = sqlite3.connect(str(self.path), timeout=15)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_db()

    def _init_db(self) -> None:
        with self.conn:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    response TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    category TEXT,
                    correction_text TEXT,
                    model TEXT NOT NULL,
                    session_id TEXT,
                    tags TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )"""
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fb_rating ON feedback(rating)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fb_category ON feedback(category)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fb_created ON feedback(created_at)")

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def __enter__(self) -> FeedbackCollector:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def record_feedback(
        self,
        prompt: str,
        response: str,
        rating: int | FeedbackRating,
        category: str | FailureCategory | None = None,
        correction_text: str | None = None,
        model: str = "default",
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> FeedbackEntry:
        """Registra uma avaliação explícita do usuário."""
        fid = uuid.uuid4().hex
        r_val = rating.value if isinstance(rating, FeedbackRating) else int(rating)
        c_val = category.value if isinstance(category, FailureCategory) else (str(category) if category else None)
        entry = FeedbackEntry(
            id=fid,
            prompt=prompt,
            response=response,
            rating=r_val,
            category=c_val,
            correction_text=correction_text,
            model=model,
            session_id=session_id,
            tags=list(tags or []),
            created_at=int(time.time()),
        )

        with self.conn:
            self.conn.execute(
                """INSERT INTO feedback (id, prompt, response, rating, category,
                                         correction_text, model, session_id, tags, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.prompt,
                    entry.response,
                    entry.rating,
                    entry.category,
                    entry.correction_text,
                    entry.model,
                    entry.session_id,
                    json.dumps(entry.tags, ensure_ascii=False),
                    entry.created_at,
                ),
            )
        return entry

    def list_feedback(
        self,
        min_rating: int | None = None,
        max_rating: int | None = None,
        category: str | FailureCategory | None = None,
        limit: int = 100,
    ) -> list[FeedbackEntry]:
        """Consulta os registros filtrando por nota ou categoria de falha."""
        query = "SELECT id, prompt, response, rating, category, correction_text, model, session_id, tags, created_at FROM feedback WHERE 1=1"
        params: list[Any] = []

        if min_rating is not None:
            query += " AND rating >= ?"
            params.append(min_rating)
        if max_rating is not None:
            query += " AND rating <= ?"
            params.append(max_rating)
        if category is not None:
            c_val = category.value if isinstance(category, FailureCategory) else str(category)
            query += " AND category = ?"
            params.append(c_val)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cur = self.conn.execute(query, params)
        results: list[FeedbackEntry] = []
        for row in cur.fetchall():
            results.append(
                FeedbackEntry(
                    id=row[0],
                    prompt=row[1],
                    response=row[2],
                    rating=row[3],
                    category=row[4],
                    correction_text=row[5],
                    model=row[6],
                    session_id=row[7],
                    tags=json.loads(row[8]),
                    created_at=row[9],
                )
            )
        return results

    def get_failure_patterns(self) -> dict[str, Any]:
        """Identifica padrões agregados de falhas recorrentes no sistema."""
        cur = self.conn.execute(
            """SELECT category, COUNT(*) as cnt
               FROM feedback
               WHERE rating < 0 AND category IS NOT NULL
               GROUP BY category
               ORDER BY cnt DESC"""
        )
        counts = {row[0]: row[1] for row in cur.fetchall()}

        cur_total = self.conn.execute(
            "SELECT COUNT(*), AVG(rating) FROM feedback"
        ).fetchone()

        total_entries = cur_total[0] if cur_total else 0
        avg_rating = cur_total[1] if (cur_total and cur_total[1] is not None) else 0.0

        return {
            "total_entries": total_entries,
            "average_rating": round(avg_rating, 3),
            "failure_counts": counts,
            "most_frequent_failure": max(counts, key=counts.get) if counts else None,
        }
