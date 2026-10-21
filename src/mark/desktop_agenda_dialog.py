"""Diálogo de agenda do MARK Desktop."""

from __future__ import annotations

import json

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout

from .desktop_dialogs import WorkerDialog
from .desktop_task import Task
from .desktop_helpers import _request


class AgendaDialog(WorkerDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.workers: set = set()
        self.setWindowTitle("Agenda do MARK")
        self.setMinimumSize(480, 420)
        layout = QVBoxLayout(self)
        intro = QLabel("Compromissos e lembretes compartilhados com o celular e a voz do MARK.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.items = QListWidget()
        layout.addWidget(self.items, 1)
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Ex.: me lembre de buscar encomenda amanhã às 16h")
        self.entry.returnPressed.connect(self.add_item)
        row.addWidget(self.entry, 1)
        self.add_button = QPushButton("Adicionar")
        self.add_button.clicked.connect(self.add_item)
        row.addWidget(self.add_button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):

        self.items.clear()

        def load():
            _, body = _request("/v1/agenda", timeout=5)
            return json.loads(body).get("items", [])

        def done(items):
            if not items:
                self.items.addItem("Sua agenda está vazia.")
                return
            from datetime import datetime
            for item in items:
                if item["due_at"]:
                    when = datetime.fromisoformat(item["due_at"]).astimezone().strftime("%d/%m %H:%M")
                    line = f"{when}  ·  {item['title']}"
                else:
                    line = f"Sem horário  ·  {item['title']}"
                if item["completed"]:
                    line = "✓  " + line
                self.items.addItem(line)

        def failed(message):
            self.items.addItem("Não consegui carregar a agenda: " + message)

        self._start_task(load, done, failed)

    def add_item(self):

        command = self.entry.text().strip()
        if not command:
            return
        self.add_button.setDisabled(True)
        self.entry.setDisabled(True)

        def add():
            _, body = _request("/v1/chat/completions", {
                "model": "mark", "stream": False,
                "messages": [{"role": "user", "content": command}],
            })
            return json.loads(body)["choices"][0]["message"]["content"]

        def done(reply):
            self.add_button.setDisabled(False)
            self.entry.setDisabled(False)
            self.entry.clear()
            self.items.insertItem(0, reply)
            self.refresh()

        def failed(message):
            self.add_button.setDisabled(False)
            self.entry.setDisabled(False)
            QMessageBox.warning(self, "Agenda", "Não consegui salvar esse item.\n\n" + message)

        self._start_task(add, done, failed)

    def _start_task(self, operation, success, failure):
        worker = Task(operation, self)
        self.workers.add(worker)
        worker.completed.connect(lambda result, w=worker: self._finish(w, success, result))
        worker.failed.connect(lambda message, w=worker: self._finish(w, failure, message))
        worker.start()

    def _finish(self, worker, callback, result):
        self.workers.discard(worker)
        callback(result)
        worker.deleteLater()

    def closeEvent(self, event):
        if self.workers:
            event.ignore()
            return
        event.accept()
