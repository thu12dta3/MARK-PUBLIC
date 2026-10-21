
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from .desktop_dialogs import WorkerDialog


class SentinelWorker(QThread):
    completed = pyqtSignal(str)

    def __init__(self, operation: str, target: str = "", parent=None):
        super().__init__(parent)
        self.operation = operation
        self.target = target

    def run(self):
        try:
            from . import sentinel
            if self.operation == "status":
                text = sentinel.status()
            elif self.operation == "scan":
                text = sentinel.scan_path([self.target])
                if Path(self.target).expanduser() == Path.home():
                    text = "Escopo: pasta pessoal do usuário; isso não percorre todo o sistema nem caminhos sem permissão.\n\n" + text
            elif self.operation == "file":
                from .config import load_config
                text = sentinel.investigate_file(self.target, cfg=load_config())
            elif self.operation == "processes":
                text = sentinel.investigate_process()
            else:
                text = sentinel.latest_report()
        except Exception as exc:
            text = f"Não foi possível concluir a operação ({type(exc).__name__}): {exc}"
        self.completed.emit(text)


class SentinelDialog(WorkerDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MARK · Sentinel")
        self.resize(760, 570)
        self.worker: SentinelWorker | None = None
        self.current_operation = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        title = QLabel("Sentinel")
        title.setStyleSheet("font-size: 23px; font-weight: 650;")
        subtitle = QLabel("Verifique arquivos e investigue o sistema quando você pedir.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #a7abad;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.scope = QLineEdit()
        self.scope.setPlaceholderText("Arquivo ou pasta para verificar")
        self.scope.setClearButtonEnabled(True)
        self.scope.setAccessibleName("Arquivo ou pasta para verificar")
        layout.addWidget(self.scope)

        choose_row = QHBoxLayout()
        self.file_button = QPushButton("Escolher arquivo…")
        self.folder_button = QPushButton("Escolher pasta…")
        self.file_button.clicked.connect(self.choose_file)
        self.folder_button.clicked.connect(self.choose_folder)
        choose_row.addWidget(self.file_button)
        choose_row.addWidget(self.folder_button)
        choose_row.addStretch(1)
        layout.addLayout(choose_row)

        actions = QVBoxLayout()
        first_action_row = QHBoxLayout()
        second_action_row = QHBoxLayout()
        self.pc_button = QPushButton("Verificar PC")
        self.scan_button = QPushButton("Verificar com ClamAV")
        self.file_info_button = QPushButton("Investigar arquivo")
        self.status_button = QPushButton("Estado do ClamAV")
        self.process_button = QPushButton("Investigar processos")
        self.report_button = QPushButton("Último relatório")
        self.action_buttons = []
        for index, (button, operation) in enumerate((
            (self.pc_button, "pc"), (self.scan_button, "scan"), (self.file_info_button, "file"),
            (self.status_button, "status"), (self.process_button, "processes"),
            (self.report_button, "report"),
        )):
            if operation == "pc":
                button.clicked.connect(lambda checked=False: self.scan_home())
            else:
                button.clicked.connect(lambda checked=False, op=operation: self.start(op))
            (first_action_row if index < 3 else second_action_row).addWidget(button)
            self.action_buttons.append(button)
        actions.addLayout(first_action_row)
        actions.addLayout(second_action_row)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)

        status_row = QHBoxLayout()
        self.status = QLabel("Pronto · nenhuma análise é iniciada automaticamente")
        self.status.setObjectName("secondaryText")
        self.cancel_button = QPushButton("Cancelar verificação")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.cancel_button)
        layout.addLayout(status_row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Os resultados aparecem aqui. Arquivos não são removidos.")
        self.output.setMinimumHeight(220)
        layout.addWidget(self.output, 1)

        note = QLabel("A verificação do PC cobre os caminhos que seu usuário consegue ler. O Sentinel não percorre o sistema inteiro com privilégios elevados.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #929798; font-size: 12px;")
        layout.addWidget(note)

    def choose_file(self):
        name, _ = QFileDialog.getOpenFileName(self, "Escolher arquivo para o Sentinel")
        if name:
            self.scope.setText(name)

    def choose_folder(self):
        name = QFileDialog.getExistingDirectory(self, "Escolher pasta para verificar")
        if name:
            self.scope.setText(name)

    def scan_home(self):
        self.scope.setText(str(Path.home()))
        self.start("scan")

    def start(self, operation: str):
        if self.worker is not None and self.worker.isRunning():
            return
        target = self.scope.text().strip()
        if operation in {"scan", "file"} and not target:
            QMessageBox.information(self, "Sentinel", "Escolha ou informe um arquivo ou pasta.")
            return
        if operation == "file" and Path(target).is_dir():
            QMessageBox.information(self, "Sentinel", "Para investigar metadados, escolha um arquivo. Para verificar uma pasta, use “Verificar com ClamAV”.")
            return
        self.current_operation = operation
        labels = {
            "scan": "Verificando com o ClamAV…",
            "file": "Calculando hash e investigando o arquivo…",
            "status": "Consultando o estado local…",
            "processes": "Lendo processos e persistência…",
            "report": "Abrindo o último relatório…",
        }
        self.status.setText(labels[operation])
        self.output.clear()
        self.progress.show()
        self.cancel_button.setEnabled(operation == "scan")
        for button in (self.file_button, self.folder_button, *self.action_buttons):
            button.setEnabled(False)
        self.worker = SentinelWorker(operation, target, self)
        self.worker.completed.connect(self.show_result)
        self.worker.finished.connect(self.finish)
        self.worker.start()

    def show_result(self, text: str):
        self.output.setPlainText(text)
        if self.current_operation == "scan":
            self.status.setText("Verificação concluída · consulte o resultado abaixo")
        elif self.current_operation == "file":
            self.status.setText("Investigação concluída · consulta externa não solicitada")
        else:
            self.status.setText("Consulta concluída")

    def finish(self):
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        for button in (self.file_button, self.folder_button, *self.action_buttons):
            button.setEnabled(True)

    def cancel(self):
        if self.current_operation != "scan":
            return
        from .sentinel import cancel_scan
        self.status.setText(cancel_scan())

    def closeEvent(self, event):
        worker = self.worker
        if worker is not None and worker.isRunning():
            if self.current_operation == "scan":
                self.cancel()
            worker.wait(5000)
            if worker.isRunning():
                QMessageBox.information(self, "Sentinel", "A tarefa ainda está encerrando. Aguarde o resultado antes de fechar.")
                event.ignore()
                return
        event.accept()
