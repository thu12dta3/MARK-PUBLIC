"""Diálogo de integração com o GitHub, extraído de ``desktop.py``.

A classe ``Task`` vem de ``desktop_task``, que não importa ``desktop``.
"""

from __future__ import annotations

import difflib

from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from . import github
from .desktop_dialogs import WorkerDialog
from .desktop_task import Task


class GitHubDialog(WorkerDialog):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GitHub do MARK")
        self.setMinimumSize(760, 600)
        self.resize(900, 700)
        self.workers: set[Task] = set()
        self.loaded_identity: tuple[str, str, str] | None = None
        self.loaded_sha: str | None = None
        self.loaded_content: str | None = None
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Veja repositórios e arquivos acessíveis pela sua conta GitHub. "
            "Ao salvar, o MARK cria um commit no branch selecionado."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.account_label = QLabel("Verificando a conta GitHub…")
        layout.addWidget(self.account_label)

        layout.addWidget(QLabel("Repositório (dono/nome)"))
        repo_row = QHBoxLayout()
        self.repo = QComboBox()
        self.repo.setEditable(True)
        self.repo.addItem("thu12dta3/MARK")
        repo_row.addWidget(self.repo, 1)
        self.refresh_repos_button = QPushButton("Atualizar lista")
        self.refresh_repos_button.clicked.connect(self.refresh_repositories)
        repo_row.addWidget(self.refresh_repos_button)
        layout.addLayout(repo_row)

        layout.addWidget(QLabel("Branch"))
        self.branch = QLineEdit("main")
        layout.addWidget(self.branch)
        layout.addWidget(QLabel("Caminho do arquivo"))
        path_row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("Ex.: README.md ou src/app.py")
        path_row.addWidget(self.path, 1)
        self.load_button = QPushButton("Abrir arquivo")
        self.load_button.clicked.connect(self.load_file)
        path_row.addWidget(self.load_button)
        layout.addLayout(path_row)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Abra um arquivo de texto para revisar ou editar.")
        layout.addWidget(self.editor, 1)

        layout.addWidget(QLabel("Mensagem do commit"))
        self.commit_message = QLineEdit("Atualizar arquivo pelo MARK")
        layout.addWidget(self.commit_message)
        footer = QHBoxLayout()
        self.status = QLabel("Os arquivos permanecem no GitHub; nenhum token é copiado para o MARK.")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.save_button = QPushButton("Revisar e salvar no GitHub")
        self.save_button.clicked.connect(self.save_file)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        self.refresh_repositories()

    def _start(self, operation, success):
        worker = Task(operation, self)
        self.workers.add(worker)
        worker.completed.connect(lambda value, w=worker: self._finish(w, success, value))
        worker.failed.connect(lambda message, w=worker: self._fail(w, message))
        worker.start()

    def _finish(self, worker, callback, value):
        self.workers.discard(worker)
        worker.deleteLater()
        callback(value)

    def _fail(self, worker, message):
        self.workers.discard(worker)
        worker.deleteLater()
        for widget in (self.refresh_repos_button, self.load_button, self.save_button,
                       self.repo, self.branch, self.path, self.editor, self.commit_message):
            widget.setDisabled(False)
        self.status.setText("Não consegui acessar o GitHub.")
        QMessageBox.warning(self, "GitHub", message)

    def refresh_repositories(self):
        self.refresh_repos_button.setDisabled(True)
        self.account_label.setText("Conectando ao GitHub…")

        def load():
            return github.account(), github.repositories()

        def done(result):
            account, repositories = result
            self.account_label.setText("Conectado como " + account)
            current = self.repo.currentText().strip()
            self.repo.clear()
            for item in repositories:
                self.repo.addItem(item["name"], item["branch"])
            if current:
                index = self.repo.findText(current)
                if index >= 0:
                    self.repo.setCurrentIndex(index)
                else:
                    self.repo.setEditText(current)
            elif self.repo.count():
                self.repo.setCurrentIndex(0)
            self.refresh_repos_button.setDisabled(False)
            self.status.setText(f"{len(repositories)} repositórios disponíveis.")

        self._start(load, done)

    def _target(self):
        return (self.repo.currentText().strip(), self.branch.text().strip(), self.path.text().strip())

    def load_file(self):
        repo, branch, path = self._target()
        self.load_button.setDisabled(True)

        def done(result):
            self.loaded_sha, content = result
            self.loaded_identity = (repo, branch, path)
            self.loaded_content = content
            self.editor.setPlainText(content)
            self.load_button.setDisabled(False)
            self.status.setText(f"Arquivo aberto · revisão {self.loaded_sha[:8]}")

        self._start(lambda: github.read_file(repo, branch, path), done)

    def save_file(self):
        repo, branch, path = self._target()
        if not self.loaded_sha or self.loaded_identity != (repo, branch, path):
            QMessageBox.information(self, "GitHub", "Abra o arquivo antes de salvar para proteger alterações recentes.")
            return
        content = self.editor.toPlainText()
        if content == self.loaded_content:
            QMessageBox.information(self, "GitHub", "O arquivo não tem alterações para salvar.")
            return
        if not content.strip():
            answer = QMessageBox.question(
                self, "Arquivo vazio", "O conteúdo ficará vazio. Quer continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        message = self.commit_message.text().strip()
        preview = "".join(difflib.unified_diff(
            (self.loaded_content or "").splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile="a/" + path, tofile="b/" + path,
        ))
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Revisar alteração no GitHub")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(
            f"Criar um commit em {repo}, branch {branch}, arquivo {path}?\n\n"
            "Confira o resumo da alteração antes de continuar."
        )
        confirm.setDetailedText(preview[:20000] + ("\n… diff cortado" if len(preview) > 20000 else ""))
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        confirm.button(QMessageBox.StandardButton.Yes).setText("Criar commit")
        confirm.button(QMessageBox.StandardButton.No).setText("Cancelar")
        answer = confirm.exec()
        if answer != QMessageBox.StandardButton.Yes:
            return
        sha = self.loaded_sha
        for widget in (self.refresh_repos_button, self.load_button, self.save_button,
                       self.repo, self.branch, self.path, self.editor, self.commit_message):
            widget.setDisabled(True)

        def save():
            commit_url = github.update_file(repo, branch, path, sha, message, content)
            new_sha, refreshed = github.read_file(repo, branch, path)
            return commit_url, new_sha, refreshed

        def done(result):
            commit_url, self.loaded_sha, saved_content = result
            self.loaded_identity = (repo, branch, path)
            self.loaded_content = saved_content
            self.editor.setPlainText(saved_content)
            for widget in (self.refresh_repos_button, self.load_button, self.save_button,
                           self.repo, self.branch, self.path, self.editor, self.commit_message):
                widget.setDisabled(False)
            self.status.setText("Commit criado: " + commit_url)
            QMessageBox.information(self, "GitHub", "Arquivo salvo no GitHub.\n\n" + commit_url)

        self._start(save, done)

    def closeEvent(self, event):
        if self.workers:
            self.status.setText("Aguarde a tarefa do GitHub terminar para fechar esta janela.")
            event.ignore()
            return
        event.accept()
