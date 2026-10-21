
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .memory import SharedMemory


SCOPE_LABELS = {
    "global": "Global",
    "specialization": "Especialização",
    "project": "Projeto",
    "task": "Tarefa",
}


class MemoryDialog(QDialog):
    def __init__(self, parent=None, memory: SharedMemory | None = None):
        super().__init__(parent)
        self.memory = memory or SharedMemory()
        self.setWindowTitle("Memórias explícitas do MARK")
        self.setMinimumSize(760, 560)
        root = QVBoxLayout(self)
        intro = QLabel("Fatos salvos por pedido explícito. O histórico e a Biblioteca não aparecem aqui.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("memorySearch")
        self.search.setPlaceholderText("Pesquisar memórias…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        self.scope_filter = QComboBox()
        self.scope_filter.setObjectName("memoryScopeFilter")
        self.scope_filter.addItem("Todos os escopos", None)
        for key, label in SCOPE_LABELS.items():
            self.scope_filter.addItem(label, key)
        self.scope_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.scope_filter)
        root.addLayout(filters)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("memoryTable")
        self.table.setHorizontalHeaderLabels(["Escopo", "Associação", "Memória", "Origem", "Atualizada"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(2, self.table.horizontalHeader().ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        self.status = QLabel()
        self.status.setObjectName("memoryStatus")
        root.addWidget(self.status)

        honcho_group = QGroupBox("Honcho · memória entre sessões (opcional)")
        honcho_layout = QVBoxLayout(honcho_group)
        from .honcho_memory import settings as honcho_settings, status as honcho_status
        initial = honcho_settings()
        self.honcho_enabled = QCheckBox("Sincronizar conversas novas com o Honcho")
        self.honcho_enabled.setObjectName("honchoSyncEnabled")
        self.honcho_enabled.setChecked(bool(initial.get("enabled") and initial.get("share_conversations")))
        honcho_layout.addWidget(self.honcho_enabled)
        disclosure = QLabel("Ao ativar, o MARK envia as próximas mensagens e respostas para o serviço configurado. O histórico antigo não é importado. Desative aqui para interromper novos envios.")
        disclosure.setWordWrap(True)
        honcho_layout.addWidget(disclosure)
        honcho_form = QFormLayout()
        self.honcho_url = QLineEdit(str(initial.get("base_url", "https://api.honcho.dev")))
        self.honcho_url.setObjectName("honchoBaseUrl")
        self.honcho_workspace = QLineEdit(str(initial.get("workspace_id", "mark")))
        self.honcho_workspace.setObjectName("honchoWorkspace")
        self.honcho_key = QLineEdit()
        self.honcho_key.setObjectName("honchoApiKey")
        self.honcho_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.honcho_key.setPlaceholderText("Chave opcional para Honcho hospedado")
        honcho_form.addRow("Endereço", self.honcho_url)
        honcho_form.addRow("Workspace", self.honcho_workspace)
        honcho_form.addRow("Chave de acesso", self.honcho_key)
        honcho_layout.addLayout(honcho_form)
        honcho_actions = QHBoxLayout()
        self.honcho_status = QLabel()
        self.honcho_status.setObjectName("honchoStatus")
        self.honcho_save = QPushButton("Salvar Honcho")
        self.honcho_save.setObjectName("saveHonchoSettings")
        self.honcho_save.clicked.connect(self.save_honcho_settings)
        honcho_actions.addWidget(self.honcho_status, 1)
        honcho_actions.addWidget(self.honcho_save)
        honcho_layout.addLayout(honcho_actions)
        root.addWidget(honcho_group)
        self._refresh_honcho_status(honcho_status())

        actions = QHBoxLayout()
        self.edit_button = QPushButton("Editar")
        self.edit_button.setObjectName("editMemory")
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button = QPushButton("Excluir")
        self.delete_button.setObjectName("deleteMemory")
        self.delete_button.clicked.connect(self.delete_selected)
        self.export_button = QPushButton("Exportar backup…")
        self.export_button.clicked.connect(self.export_backup)
        self.restore_button = QPushButton("Restaurar backup…")
        self.restore_button.clicked.connect(self.restore_backup)
        self.close_button = QPushButton("Fechar")
        self.close_button.clicked.connect(self.accept)
        for button in (self.edit_button, self.delete_button, self.export_button,
                       self.restore_button, self.close_button):
            actions.addWidget(button)
        root.addLayout(actions)
        self.refresh()

    def _refresh_honcho_status(self, current):
        if not current["enabled"]:
            self.honcho_status.setText("Desativado · nenhuma conversa é enviada")
        elif current["ready"]:
            self.honcho_status.setText("Ativo · sincroniza somente conversas novas")
        else:
            self.honcho_status.setText("Ativo, aguardando uma chave de acesso")

    def save_honcho_settings(self):
        from .honcho_memory import save_settings, set_api_key, status as honcho_status
        try:
            key = self.honcho_key.text()
            if key:
                set_api_key(key)
                self.honcho_key.clear()
            save_settings(enabled=self.honcho_enabled.isChecked(),
                          base_url=self.honcho_url.text(),
                          workspace_id=self.honcho_workspace.text())
            self._refresh_honcho_status(honcho_status())
            QMessageBox.information(self, "Honcho", "Configuração salva. O MARK não importou nenhuma conversa antiga.")
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            QMessageBox.warning(self, "Honcho", str(exc))

    def _selected_id(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self, *_):
        scope = self.scope_filter.currentData()
        try:
            rows = self.memory.list_facts(self.search.text(), scope=scope, limit=2000)
        except Exception as exc:
            self.status.setText(f"Não consegui ler as memórias ({type(exc).__name__}).")
            return
        self.table.setRowCount(0)
        for data in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            label = QTableWidgetItem(SCOPE_LABELS[data["scope"]])
            label.setData(Qt.ItemDataRole.UserRole, data["id"])
            association = "Geral" if data["scope"] == "global" else data["scope_id"]
            values = (label, QTableWidgetItem(association), QTableWidgetItem(data["content"]),
                      QTableWidgetItem(data["provenance"]),
                      QTableWidgetItem(str(data["updated_at"])))
            for column, item in enumerate(values):
                item.setToolTip(item.text())
                self.table.setItem(row, column, item)
        self.status.setText(f"{len(rows)} memórias explícitas · histórico e documentos preservados")
        enabled = bool(rows)
        self.edit_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def edit_selected(self):
        memory_id = self._selected_id()
        if memory_id is None:
            return
        current = self.table.item(self.table.currentRow(), 2).text()
        text, accepted = QInputDialog.getMultiLineText(self, "Editar memória", "Conteúdo:", current)
        if not accepted:
            return
        try:
            if not self.memory.update_fact(memory_id, text):
                self.status.setText("A memória não existe mais.")
            else:
                self.status.setText("Memória atualizada.")
            self.refresh()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Memória", str(exc))

    def delete_selected(self):
        memory_id = self._selected_id()
        if memory_id is None:
            return
        if QMessageBox.question(self, "Excluir memória", "Excluir somente esta memória explícita?") != QMessageBox.StandardButton.Yes:
            return
        self.memory.delete_fact(memory_id)
        self.refresh()

    def export_backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar memórias explícitas", "mark-memories.json",
                                              "Backup MARK (*.json)")
        if not path:
            return
        try:
            count = self.memory.export_facts(Path(path))
            self.status.setText(f"Backup criado: {count} memórias. Arquivo protegido com permissão privada.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Backup", str(exc))

    def restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, "Restaurar memórias explícitas", "",
                                              "Backup MARK (*.json)")
        if not path:
            return
        answer = QMessageBox.question(
            self, "Restaurar backup",
            "As memórias válidas serão mescladas. O histórico e as memórias atuais não serão apagados.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.memory.restore_facts(Path(path))
            self.status.setText(f"Restauradas {result['inserted']} · duplicadas ignoradas {result['duplicates']}.")
            self.refresh()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Restaurar backup", str(exc))
