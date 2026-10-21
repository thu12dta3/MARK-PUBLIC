
import json
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QPlainTextEdit, QVBoxLayout)
from .desktop_task import Task
from .desktop_dialogs import WorkerDialog
from .jetbrains_mcp import JetBrainsMCP, endpoint_from_clipboard, load_endpoints, save_endpoints


class JetBrainsDialog(WorkerDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MARK · IDEs JetBrains")
        self.resize(620, 620)
        self.worker = None
        self.tools = []
        self.client = None
        try:
            self.endpoints = load_endpoints()
            self.config_error = ""
        except ValueError as exc:
            self.endpoints = {"pycharm": "", "clion": ""}
            self.config_error = str(exc)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(11)
        title = QLabel("Conectar uma IDE")
        title.setStyleSheet("font-size: 21px; font-weight: 650;")
        layout.addWidget(title)
        intro = QLabel("Conecte ao servidor MCP local do PyCharm ou CLion. A conexão fica no computador e só atende localhost.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.ide = QComboBox()
        self.ide.addItem("PyCharm", "pycharm")
        self.ide.addItem("CLion", "clion")
        self.ide.currentIndexChanged.connect(self.load_selected)
        layout.addWidget(self.ide)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("Cole URL ou Copy HTTP Stream Config da IDE")
        layout.addWidget(self.endpoint)
        actions = QHBoxLayout()
        self.connect_button = QPushButton("Conectar e listar ferramentas")
        self.connect_button.clicked.connect(self.connect_ide)
        actions.addWidget(self.connect_button)
        self.status = QLabel(self.config_error or "Desconectado")
        self.status.setStyleSheet("color: #999999;")
        actions.addWidget(self.status, 1)
        layout.addLayout(actions)
        self.tool_picker = QComboBox()
        self.tool_picker.setPlaceholderText("Ferramentas da IDE")
        self.tool_picker.currentIndexChanged.connect(self.show_tool)
        layout.addWidget(self.tool_picker)
        self.description = QLabel("As ferramentas disponíveis aparecerão após conectar.")
        self.description.setWordWrap(True)
        self.description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.description)
        layout.addWidget(QLabel("Argumentos da ferramenta · JSON"))
        self.arguments = QPlainTextEdit()
        self.arguments.setPlaceholderText("{}")
        self.arguments.setFixedHeight(95)
        layout.addWidget(self.arguments)
        self.call_button = QPushButton("Executar ferramenta selecionada…")
        self.call_button.clicked.connect(self.call_tool)
        self.call_button.setEnabled(False)
        layout.addWidget(self.call_button)
        layout.addWidget(QLabel("Resposta"))
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumBlockCount(100)
        layout.addWidget(self.result, 1)
        note = QLabel("O servidor JetBrains deve ser ativado em Settings → Tools → MCP Server. Mantenha desligada a opção de executar comandos sem confirmação.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #999999; font-size: 11px;")
        layout.addWidget(note)
        self.load_selected()

    def load_selected(self, *_):
        key = self.ide.currentData()
        self.endpoint.setText(self.endpoints.get(key, ""))
        self.client = None
        self.tools = []
        self.tool_picker.clear()
        self.call_button.setEnabled(False)
        self.status.setText("Desconectado")

    def connect_ide(self):
        if self.worker:
            return
        try:
            endpoint = endpoint_from_clipboard(self.endpoint.text())
            self.endpoints[self.ide.currentData()] = endpoint
            save_endpoints(self.endpoints)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Conexão JetBrains", str(exc))
            return
        self.connect_button.setEnabled(False)
        self.status.setText("Conectando…")
        self.worker = Task(lambda: self._connect(endpoint), self)
        self.worker.completed.connect(self.connected)
        self.worker.failed.connect(self.failed)
        self.worker.start()

    def _connect(self, endpoint):
        client = JetBrainsMCP(endpoint)
        info = client.connect()
        tools = client.list_tools()
        return client, info, tools

    def connected(self, result):
        self.worker.deleteLater(); self.worker = None
        self.client, info, self.tools = result
        self.connect_button.setEnabled(True)
        name = info.get("name", "JetBrains IDE") if isinstance(info, dict) else "JetBrains IDE"
        self.status.setText(f"Conectado · {name} · {len(self.tools)} ferramentas")
        self.tool_picker.clear()
        for tool in self.tools:
            self.tool_picker.addItem(str(tool.get("name", "ferramenta")), tool)
        self.call_button.setEnabled(bool(self.tools))
        self.show_tool()

    def failed(self, message):
        self.worker.deleteLater(); self.worker = None
        self.connect_button.setEnabled(True)
        self.status.setText("Falha na conexão")
        QMessageBox.warning(self, "Conexão JetBrains", message)

    def show_tool(self, *_):
        tool = self.tool_picker.currentData()
        if not isinstance(tool, dict):
            return
        self.description.setText(str(tool.get("description", "Sem descrição"))[:1200])
        self.arguments.setPlainText("{}")

    def call_tool(self):
        if self.worker or not self.client:
            return
        tool = self.tool_picker.currentData()
        if not isinstance(tool, dict):
            return
        try:
            args = json.loads(self.arguments.toPlainText() or "{}")
            if not isinstance(args, dict):
                raise ValueError("Os argumentos precisam ser um objeto JSON.")
        except ValueError as exc:
            QMessageBox.warning(self, "Ferramenta JetBrains", str(exc))
            return
        name = tool.get("name", "ferramenta")
        warning = (f"Executar '{name}' na IDE conectada?\n\nO servidor poderá ler ou modificar o projeto conforme a ferramenta. "
                   "A IDE mantém as próprias confirmações para comandos e configurações de execução.")
        if QMessageBox.question(self, "Confirmar ação na IDE", warning,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.call_button.setEnabled(False)
        self.status.setText("Executando…")
        self.worker = Task(lambda: self.client.call_tool(name, args), self)
        self.worker.completed.connect(self.called)
        self.worker.failed.connect(self.failed)
        self.worker.start()

    def called(self, result):
        self.worker.deleteLater(); self.worker = None
        self.call_button.setEnabled(True)
        self.status.setText("Conectado")
        self.result.setPlainText(json.dumps(result, ensure_ascii=False, indent=2)[:12000])

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
            return
        event.accept()
