"""Painel de tarefas locais do Engineer, extraído de desktop.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

ACTIVE_STATES = ("WAITING_APPROVAL", "QUEUED", "RUNNING", "PAUSED", "BLOCKED", "VERIFYING")


def mission_counts(db_path: Path | None = None) -> dict[str, int]:
    """Contagem de missões por estado; leitura somente, segura fora da GUI thread."""
    from .missions import MissionStore

    path = MissionStore.DEFAULT_PATH if db_path is None else db_path
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        uri = "file:" + quote(str(path)) + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            return dict(db.execute(
                "SELECT state, COUNT(*) FROM missions GROUP BY state"
            ).fetchall())
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return {}


class EngineerTasksWidget(QWidget):
    def __init__(self, open_chat, open_missions, open_github, icon: QIcon | None = None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.summary = QLabel("Tarefas: carregando…")
        self.summary.setObjectName("engineerMissionSummary")
        layout.addWidget(self.summary)

        actions = QHBoxLayout()
        chat = QPushButton("Abrir chat de código")
        chat.setObjectName("primaryButton")
        chat.clicked.connect(open_chat)

        missions = QPushButton("Tarefas e permissões…")
        if icon is not None:
            missions.setIcon(icon)
        missions.clicked.connect(open_missions)

        github = QPushButton("Repositórios GitHub…")
        github.clicked.connect(open_github)

        actions.addWidget(chat)
        actions.addWidget(missions)
        actions.addWidget(github)
        actions.addStretch(1)
        layout.addLayout(actions)

    def set_counts(self, counts: dict[str, int]) -> None:
        total = sum(counts.values())
        if not total:
            self.summary.setText("Tarefas locais · nenhuma registrada")
            return
        active = sum(counts.get(state, 0) for state in ACTIVE_STATES)
        completed = counts.get("COMPLETED", 0)
        self.summary.setText(
            f"Tarefas locais · {total} registradas · {active} aguardando ou em andamento · {completed} concluídas"
        )

    def set_unavailable(self) -> None:
        self.summary.setText("Tarefas locais · estado indisponível")
