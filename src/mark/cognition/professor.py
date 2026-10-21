
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time

from .storage import private_file

DEFAULT_STUDY_DB = Path.home() / ".local/state/mark/cognition/study.sqlite3"
LEVELS = {"iniciante", "intermediário", "avançado"}


@dataclass(frozen=True, slots=True)
class StudyMode:
    key: str
    label: str
    instruction: str


MODES = (
    StudyMode("explain", "Explicar", "Explique o conceito com clareza, uma analogia útil e um exemplo; ajuste a profundidade ao nível informado."),
    StudyMode("teach", "Ensinar por etapas", "Ensine em etapas pequenas. Apresente somente a primeira etapa e confira minha compreensão antes de avançar."),
    StudyMode("quiz", "Quiz", "Faça uma pergunta por vez, sem mostrar a resposta antes da minha tentativa. Depois explique o resultado e a fonte quando houver."),
    StudyMode("practice", "Praticar", "Proponha um exercício progressivo sem entregar a solução primeiro. Dê pistas graduais se eu pedir."),
    StudyMode("evaluate", "Avaliar resposta", "Peça minha resposta se ela ainda não estiver na conversa; avalie-a com critérios explícitos, explique erros e incertezas."),
    StudyMode("flashcards", "Flashcards", "Crie até cinco cartões de pergunta e resposta por rodada, baseados no material disponível; mostre primeiro as perguntas."),
    StudyMode("review", "Revisar", "Ajude a revisar o que já estudamos nesta conversa. Separe o que foi praticado do que ainda não foi confirmado."),
    StudyMode("plan", "Plano de estudo", "Monte uma sequência realista de objetivos, pré-requisitos, exercícios e revisão; não presuma quanto tempo tenho."),
)
_MODES = {mode.key: mode for mode in MODES}


@dataclass(frozen=True, slots=True)
class StudyPlan:
    id: int
    subject: str
    objective: str
    source: str
    level: str
    mode: str
    status: str
    created_at: float
    updated_at: float


def _field(value: str, name: str, limit: int, *, required=False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} inválido")
    clean = " ".join(value.split()).strip()
    if required and not clean:
        raise ValueError(f"Informe {name}")
    if len(clean) > limit:
        raise ValueError(f"{name} excede {limit} caracteres")
    return clean


def build_study_prompt(subject: str, mode: str, level: str = "iniciante", *,
                       objective: str = "", source: str = "") -> str:

    subject = _field(subject, "o assunto", 160, required=True)
    objective = _field(objective, "o objetivo", 300)
    source = _field(source, "o título da fonte", 200).replace('"', "'")
    if mode not in _MODES:
        raise ValueError("Modo de estudo inválido")
    if level not in LEVELS:
        raise ValueError("Nível de estudo inválido")
    lines = [f"Vamos estudar: {subject}.", f"Nível desejado: {level}.", _MODES[mode].instruction]
    if objective:
        lines.append(f"Meu objetivo declarado: {objective}.")
    if source:
        lines.append(f'Use somente minha biblioteca local e priorize o documento "{source}". Cite apenas metadados recuperados; se o trecho não sustentar a resposta, diga isso claramente.')
    else:
        lines.append("Se eu não indicar uma fonte da Biblioteca, diferencie o conhecimento geral do MARK do que foi confirmado na minha Biblioteca.")
    lines.append("Mantenha o diálogo interativo e não invente meu progresso ou minhas respostas anteriores.")
    return " ".join(lines)


class StudyPlanStore:


    SCHEMA_VERSION = 1

    def __init__(self, path: Path = DEFAULT_STUDY_DB):
        self.path = private_file(path)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, self.SCHEMA_VERSION):
            self.db.close()
            raise ValueError("Versão do progresso de estudo não suportada")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS study_plans (
                id INTEGER PRIMARY KEY,
                subject TEXT NOT NULL,
                objective TEXT NOT NULL,
                source TEXT NOT NULL,
                level TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','COMPLETED')),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""")
            self.db.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _record(row) -> StudyPlan:
        return StudyPlan(row["id"], row["subject"], row["objective"], row["source"],
                         row["level"], row["mode"], row["status"], row["created_at"], row["updated_at"])

    def start(self, subject: str, objective: str = "", source: str = "",
              level: str = "iniciante", mode: str = "explain") -> StudyPlan:
        subject = _field(subject, "o assunto", 160, required=True)
        objective = _field(objective, "o objetivo", 300)
        source = _field(source, "o título da fonte", 200)
        if level not in LEVELS:
            raise ValueError("Nível de estudo inválido")
        if mode not in _MODES:
            raise ValueError("Modo de estudo inválido")
        now = time.time()
        with self.db:
            cur = self.db.execute("INSERT INTO study_plans(subject,objective,source,level,mode,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                  (subject, objective, source, level, mode, "ACTIVE", now, now))
        return self.get(cur.lastrowid)

    def list(self, limit: int = 50) -> list[StudyPlan]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Limite de planos inválido")
        rows = self.db.execute("SELECT * FROM study_plans ORDER BY updated_at DESC,id DESC LIMIT ?", (limit,))
        return [self._record(row) for row in rows]

    def get(self, plan_id: int) -> StudyPlan | None:
        row = self.db.execute("SELECT * FROM study_plans WHERE id=?", (plan_id,)).fetchone()
        return self._record(row) if row else None

    def complete(self, plan_id: int) -> bool:
        with self.db:
            cur = self.db.execute("UPDATE study_plans SET status='COMPLETED',updated_at=? WHERE id=? AND status='ACTIVE'",
                                  (time.time(), plan_id))
        return cur.rowcount == 1

    def reset(self) -> int:

        with self.db:
            cur = self.db.execute("DELETE FROM study_plans")
        return cur.rowcount
