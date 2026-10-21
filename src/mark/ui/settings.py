"""Painel de APIs Universal e Dinâmico do MARK.

Substitui os formulários de provedores hardcoded por um Cofre Universal de Chaves
integrado ao LLMKeyVault (criptografia AES-256-GCM com permissões estritas POSIX 0600).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config.llm_config import LLMKeyVault
from .theme_manager import ThemeManager

logger = logging.getLogger("mark.ui.settings")


def mask_api_key(secret: str) -> str:
    """Oculta chave de API no formato seguro (ex: sk-...****)."""
    if not secret:
        return ""
    if secret.startswith("sk-"):
        return "sk-...****"
    if len(secret) >= 6:
        return f"{secret[:3]}...****"
    return "sk-...****" if len(secret) >= 4 else "****"


class SettingsWidget(QWidget):
    """Painel de Configurações e Gerenciamento Universal de Chaves de API."""

    back_requested = pyqtSignal()
    key_saved = pyqtSignal(str)
    key_removed = pyqtSignal(str)

    COMMON_PROVIDERS = [
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "OLLAMA_HOST",
        "X_API_KEY",
    ]

    def __init__(
        self,
        vault: LLMKeyVault | None = None,
        on_back: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsWidget")
        self.vault = vault or LLMKeyVault()
        if on_back:
            self.back_requested.connect(on_back)
        self._build_ui()
        self.refresh_keys_table()

    def _build_ui(self) -> None:
        self.setStyleSheet("""
            QWidget#settingsWidget {
                background: #1A1D21;
                color: #EAEAEA;
                font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            }
            QLabel#settingsTitle {
                color: #EAEAEA;
                font-size: 18px;
                font-weight: 600;
                letter-spacing: -0.2px;
            }
            QLabel#settingsSubtitle {
                color: #8B949E;
                font-size: 12px;
            }
            QPushButton#backToChatButton {
                background: #22262B;
                color: #58A6FF;
                border: 1px solid #30363D;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#backToChatButton:hover {
                background: #2D333B;
                border-color: #58A6FF;
            }
            QGroupBox {
                color: #EAEAEA;
                border: 1px solid #30363D;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 14px;
                font-size: 13px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
                color: #58A6FF;
            }
            QTableWidget#keysTable {
                background: #22262B;
                color: #EAEAEA;
                border: 1px solid #30363D;
                border-radius: 8px;
                gridline-color: #30363D;
                font-size: 12px;
                selection-background-color: #2D333B;
                selection-color: #58A6FF;
            }
            QHeaderView::section {
                background: #1A1D21;
                color: #8B949E;
                border: none;
                border-bottom: 1px solid #30363D;
                padding: 6px;
                font-weight: 500;
            }
            QComboBox, QLineEdit {
                background: #22262B;
                color: #EAEAEA;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QComboBox:focus, QLineEdit:focus {
                border-color: #58A6FF;
            }
            QPushButton#saveKeyButton {
                background: #58A6FF;
                color: #1A1D21;
                border: none;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton#saveKeyButton:hover {
                background: #79B8FF;
            }
            QPushButton#removeKeyButton {
                background: #22262B;
                color: #F85149;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton#removeKeyButton:hover {
                background: #2D333B;
                border-color: #F85149;
            }
            QLabel#vaultStatusLabel {
                font-size: 12px;
                padding: 4px 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(16)

        # Cabeçalho fixo com botão evidente "← Voltar para Conversa"
        header_bar = QHBoxLayout()
        header_bar.setSpacing(16)

        self.back_button = QPushButton("← Voltar para Conversa")
        self.back_button.setObjectName("backToChatButton")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self._handle_back_clicked)
        header_bar.addWidget(self.back_button, alignment=Qt.AlignmentFlag.AlignLeft)

        header_bar.addSpacing(8)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        self.title_label = QLabel("Configurações · Cofre Universal de Chaves")
        self.title_label.setObjectName("settingsTitle")
        title_layout.addWidget(self.title_label)

        self.subtitle_label = QLabel("Armazenamento local criptografado (AES-256-GCM / 0600) para qualquer provedor de IA.")
        self.subtitle_label.setObjectName("settingsSubtitle")
        title_layout.addWidget(self.subtitle_label)

        header_bar.addLayout(title_layout, 1)
        layout.addLayout(header_bar)

        # Tabela dinâmica de chaves salvas
        table_label = QLabel("Chaves Registradas no Cofre")
        table_label.setStyleSheet("font-weight: 600; color: #EAEAEA; font-size: 13px;")
        layout.addWidget(table_label)

        self.keys_table = QTableWidget()
        self.keys_table.setObjectName("keysTable")
        self.keys_table.setColumnCount(3)
        self.keys_table.setHorizontalHeaderLabels(["Nome da Variável / Provedor", "Chave Protegida", "Segurança"])
        self.keys_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.keys_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.keys_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.keys_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.keys_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.keys_table.itemSelectionChanged.connect(self._handle_table_selection_changed)
        layout.addWidget(self.keys_table, 1)

        # Seção "Adicionar Nova Chave"
        add_group = QGroupBox("Adicionar ou Atualizar Chave no Cofre")
        add_group.setObjectName("addKeySection")
        add_layout = QVBoxLayout(add_group)
        add_layout.setContentsMargins(16, 16, 16, 16)
        add_layout.setSpacing(12)

        fields_row = QHBoxLayout()
        fields_row.setSpacing(12)

        # Combobox editável para Nome da Variável / Provedor
        provider_col = QVBoxLayout()
        provider_col.setSpacing(4)
        provider_label = QLabel("Nome da Variável / Provedor:")
        provider_label.setStyleSheet("color: #8B949E; font-size: 11px;")
        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("providerCombo")
        self.provider_combo.setAccessibleName("Nome do provedor ou variável de API")
        self.provider_combo.setEditable(True)
        for p in self.COMMON_PROVIDERS:
            self.provider_combo.addItem(p)
        self.provider_combo.setCurrentText("DEEPSEEK_API_KEY")
        provider_col.addWidget(provider_label)
        provider_col.addWidget(self.provider_combo)
        fields_row.addLayout(provider_col, 1)

        # Campo de senha para o valor da chave
        key_col = QVBoxLayout()
        key_col.setSpacing(4)
        key_label = QLabel("Valor Secreto da Chave:")
        key_label.setStyleSheet("color: #8B949E; font-size: 11px;")
        self.key_input = QLineEdit()
        self.key_input.setObjectName("keyInput")
        self.key_input.setAccessibleName("Valor da chave secreta de API")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("Cole o token ou chave secreta aqui...")
        key_col.addWidget(key_label)
        key_col.addWidget(self.key_input)
        fields_row.addLayout(key_col, 2)

        add_layout.addLayout(fields_row)

        # Botões de ação
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self.save_button = QPushButton("Salvar Chave")
        self.save_button.setObjectName("saveKeyButton")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.clicked.connect(self.save_key)
        actions_row.addWidget(self.save_button)

        self.remove_button = QPushButton("Remover")
        self.remove_button.setObjectName("removeKeyButton")
        self.remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_button.clicked.connect(self.remove_key)
        actions_row.addWidget(self.remove_button)

        actions_row.addStretch(1)
        add_layout.addLayout(actions_row)

        # Status feedback
        self.status_label = QLabel("")
        self.status_label.setObjectName("vaultStatusLabel")
        add_layout.addWidget(self.status_label)

        layout.addWidget(add_group)

    def _handle_back_clicked(self) -> None:
        self.back_requested.emit()

    def _handle_table_selection_changed(self) -> None:
        selected_rows = self.keys_table.selectedItems()
        if selected_rows:
            provider_name = selected_rows[0].text()
            self.provider_combo.setEditText(provider_name)

    def refresh_keys_table(self) -> None:
        """Lê os dados criptografados do cofre e preenche a tabela dinâmica com chaves ocultas."""
        try:
            raw_vault = self.vault._read_vault()
        except Exception as exc:
            logger.error(f"Erro ao ler cofre para a tabela: {exc}")
            raw_vault = {}

        self.keys_table.setRowCount(0)
        row = 0
        for provider_key, entry in sorted(raw_vault.items()):
            display_name = provider_key.upper()
            secret_key = ""
            if isinstance(entry, dict):
                secret_key = entry.get("key", "")
                display_name = entry.get("metadata", {}).get("display_name", display_name)
            elif isinstance(entry, str):
                secret_key = entry

            masked = mask_api_key(secret_key)
            self.keys_table.insertRow(row)

            item_provider = QTableWidgetItem(display_name)
            item_provider.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)

            item_key = QTableWidgetItem(masked)
            item_key.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)

            item_sec = QTableWidgetItem("AES-256-GCM (0600)")
            item_sec.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            item_sec.setForeground(Qt.GlobalColor.darkGray)

            self.keys_table.setItem(row, 0, item_provider)
            self.keys_table.setItem(row, 1, item_key)
            self.keys_table.setItem(row, 2, item_sec)
            row += 1

    def save_key(self) -> bool:
        """Salva a chave no LLMKeyVault dinamicamente sem limite de provedor."""
        provider = self.provider_combo.currentText().strip()
        key_val = self.key_input.text().strip()

        if not provider:
            self.status_label.setStyleSheet("color: #F85149;")
            self.status_label.setText("Informe o nome do provedor ou variável de API.")
            return False

        if not key_val:
            self.status_label.setStyleSheet("color: #F85149;")
            self.status_label.setText("O valor da chave de API não pode ser vazio.")
            return False

        try:
            self.vault.set_key(provider, key_val, metadata={"display_name": provider})
            self.key_input.clear()
            self.refresh_keys_table()
            self.status_label.setStyleSheet("color: #3FB950;")
            self.status_label.setText(f"Chave '{provider}' salva com sucesso no cofre.")
            self.key_saved.emit(provider)
            return True
        except Exception as exc:
            self.status_label.setStyleSheet("color: #F85149;")
            self.status_label.setText(f"Erro ao salvar chave: {exc}")
            return False

    def remove_key(self) -> bool:
        """Remove a chave selecionada ou digitada do cofre."""
        provider = self.provider_combo.currentText().strip()
        selected_items = self.keys_table.selectedItems()
        if selected_items:
            provider = selected_items[0].text().strip()

        if not provider:
            self.status_label.setStyleSheet("color: #F85149;")
            self.status_label.setText("Selecione ou informe uma chave para remover.")
            return False

        try:
            removed = self.vault.delete_key(provider)
            self.refresh_keys_table()
            if removed:
                self.status_label.setStyleSheet("color: #3FB950;")
                self.status_label.setText(f"Chave '{provider}' removida do cofre.")
                self.key_removed.emit(provider)
                return True
            else:
                self.status_label.setStyleSheet("color: #E3B341;")
                self.status_label.setText(f"Chave '{provider}' não encontrada no cofre.")
                return False
        except Exception as exc:
            self.status_label.setStyleSheet("color: #F85149;")
            self.status_label.setText(f"Erro ao remover chave: {exc}")
            return False


class UniversalKeyVaultDialog(QDialog):
    """Diálogo modal para o Cofre de Chaves Universal."""

    def __init__(self, vault: LLMKeyVault | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MARK · Cofre Universal de Chaves de API")
        self.resize(780, 560)
        self.setStyleSheet("background: #1A1D21;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.settings_widget = SettingsWidget(vault=vault, on_back=self.accept, parent=self)
        layout.addWidget(self.settings_widget)
