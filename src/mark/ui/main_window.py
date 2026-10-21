"""Janela Principal (MainWindow) do MARK com Navegação Stacked e Soft Minimal Tech.

Implementa:
- QStackedWidget central:
  * Índice 0: Conversa Geral (com Composer completo, anexos, seletores dinâmicos de IA, histórico SQLite e renderização rica).
  * Índice 1: Configurações e Cofre Universal de Chaves (com botão evidente "← Voltar para Conversa").
  * Índice 2: Chamada de Voz In-App (com botão evidente "Encerrar e Voltar" sem pop-up modal solto).
- Alternador de Sidebar no rodapé que apenas expande/recolhe a barra lateral sem abrir modais.
- Feedback visual com ThinkingIndicatorWidget ("MARK está pensando...") integrado ao ciclo de vida de respostas.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config.llm_config import LLMKeyVault
from ..desktop_agenda_dialog import AgendaDialog
from ..desktop_attachments import AttachmentsMixin
from ..desktop_chat import ChatMixin
from ..desktop_composer import Composer
from ..desktop_email_dialog import EmailComposerDialog
from ..desktop_github_dialog import GitHubDialog
from ..desktop_helpers import (
    API,
    ICON_DIR,
    ROOT,
    _mark_icon,
    _mobile_pairing,
    _prepare_attachments,
    _request,
    _service,
    _token,
    _voice_request,
)
from ..desktop_history import HistoryMixin
from ..desktop_mobile_dialog import MobileDevicesDialog
from ..desktop_navigation import NavigationMixin
from ..desktop_packages import PackagesMixin
from ..desktop_professor import ProfessorMixin
from ..desktop_settings import SettingsMixin
from ..desktop_task import Task
from ..desktop_workers import WorkerMixin
from ..sandbox import _probe_bwrap_support
from ..url_security import validate_url_scheme
from .command_palette import CommandPaletteDialog
from .device_hub import DeviceHubDialog
from .settings import SettingsWidget
from .theme_manager import ThemeManager
from .widgets import (
    ActionConsentDialog,
    ChatWidget,
    ConsentBridge,
    GodsEyeStatusWidget,
    MessageBubbleWidget,
    ProviderSelectorWidget,
    ThinkingIndicatorWidget,
)

logger = logging.getLogger("mark.ui.main_window")

MEMORY_FILE = Path.home() / ".local/state/mark/conversation.sqlite3"
DESKTOP_CONVERSATIONS_FILE = Path.home() / ".local/state/mark/desktop-conversations.sqlite3"
VOICE_SERVICE = "mark-voice.service"
MOBILE_SERVICE = "mark-mobile.service"


class VoiceCallWidget(QWidget):
    """Interface de chamada de voz renderizada in-app (Soft Minimal Tech)."""

    back_requested = pyqtSignal()

    def __init__(self, on_back: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("voiceCallWidget")
        self.is_active = False
        if on_back:
            self.back_requested.connect(on_back)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("""
            QWidget#voiceCallWidget {
                background: #1A1D21;
                color: #EAEAEA;
                font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            }
            QPushButton#endCallButton {
                background: #22262B;
                color: #58A6FF;
                border: 1px solid #30363D;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#endCallButton:hover {
                background: #2D333B;
                border-color: #58A6FF;
            }
            QPushButton#toggleCallButton {
                background: #58A6FF;
                color: #1A1D21;
                border: none;
                border-radius: 24px;
                padding: 14px 32px;
                font-size: 15px;
                font-weight: 600;
            }
            QPushButton#toggleCallButton:hover {
                background: #79B8FF;
            }
            QPushButton#toggleCallButtonActive {
                background: #F85149;
                color: #FFFFFF;
                border: none;
                border-radius: 24px;
                padding: 14px 32px;
                font-size: 15px;
                font-weight: 600;
            }
            QPushButton#toggleCallButtonActive:hover {
                background: #FF6B6B;
            }
            QComboBox {
                background: #22262B;
                color: #EAEAEA;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QLabel#voiceOrb {
                font-size: 64px;
                color: #58A6FF;
            }
            QLabel#callStateLabel {
                font-size: 20px;
                font-weight: 600;
                color: #EAEAEA;
            }
            QLabel#callDetailLabel {
                color: #8B949E;
                font-size: 13px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 32)
        layout.setSpacing(20)

        header = QHBoxLayout()
        self.end_call_button = QPushButton("Encerrar e Voltar")
        self.end_call_button.setObjectName("endCallButton")
        self.end_call_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.end_call_button.clicked.connect(self._handle_end_call)
        header.addWidget(self.end_call_button, alignment=Qt.AlignmentFlag.AlignLeft)

        header.addStretch(1)

        title = QLabel("CHAMADA DE VOZ · IN-APP")
        title.setStyleSheet("color: #8B949E; font-size: 12px; font-weight: 600; letter-spacing: 1px;")
        header.addWidget(title)
        layout.addLayout(header)

        layout.addStretch(1)

        center_col = QVBoxLayout()
        center_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_col.setSpacing(14)

        self.voice_orb = QLabel("◉")
        self.voice_orb.setObjectName("voiceOrb")
        self.voice_orb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_col.addWidget(self.voice_orb)

        self.state_label = QLabel("Pronto para conversar")
        self.state_label.setObjectName("callStateLabel")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_col.addWidget(self.state_label)

        self.detail_label = QLabel("Fale normalmente. O MARK responde diretamente por voz no PC.")
        self.detail_label.setObjectName("callDetailLabel")
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_col.addWidget(self.detail_label)

        center_col.addSpacing(16)

        output_row = QHBoxLayout()
        output_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        out_lbl = QLabel("Saída de áudio:")
        out_lbl.setStyleSheet("color: #8B949E; font-size: 12px;")
        self.output_selector = QComboBox()
        self.output_selector.setObjectName("audioOutputSelector")
        self.output_selector.addItem("Alto-falante Padrão do Sistema")
        self.output_selector.addItem("Fone de Ouvido")
        output_row.addWidget(out_lbl)
        output_row.addWidget(self.output_selector)
        center_col.addLayout(output_row)

        center_col.addSpacing(20)

        self.toggle_call_button = QPushButton("Iniciar chamada")
        self.toggle_call_button.setObjectName("toggleCallButton")
        self.toggle_call_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_call_button.clicked.connect(self.toggle_call)
        center_col.addWidget(self.toggle_call_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addLayout(center_col)
        layout.addStretch(2)

    def toggle_call(self) -> None:
        self.is_active = not self.is_active
        if self.is_active:
            self.toggle_call_button.setObjectName("toggleCallButtonActive")
            self.toggle_call_button.setText("Encerrar chamada")
            self.state_label.setText("Ouvindo... (Voz ativa)")
            self.voice_orb.setStyleSheet("font-size: 64px; color: #3FB950;")
        else:
            self.toggle_call_button.setObjectName("toggleCallButton")
            self.toggle_call_button.setText("Iniciar chamada")
            self.state_label.setText("Chamada encerrada")
            self.voice_orb.setStyleSheet("font-size: 64px; color: #58A6FF;")
        self.toggle_call_button.style().unpolish(self.toggle_call_button)
        self.toggle_call_button.style().polish(self.toggle_call_button)

    def _handle_end_call(self) -> None:
        if self.is_active:
            self.toggle_call()
        self.back_requested.emit()


class MainWindow(
    HistoryMixin,
    WorkerMixin,
    NavigationMixin,
    ChatMixin,
    ProfessorMixin,
    AttachmentsMixin,
    SettingsMixin,
    PackagesMixin,
    QMainWindow,
):
    """Janela Principal (MainWindow) do MARK com Navegação Stacked, Composer Avançado e Soft Minimal Tech."""

    def __init__(self, vault: LLMKeyVault | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MARK · Assistente Pessoal")
        self.setWindowIcon(QIcon(str(ROOT / "assets/mark-desktop.svg")))
        self.setMinimumSize(960, 680)
        self.resize(1280, 850)
        self.vault = vault or LLMKeyVault()

        # Atributos de estado do assistente
        self.tasks: set[Task] = set()
        self.shared_session_id = uuid4().hex
        self._memory_file = Path(MEMORY_FILE)
        self._shared_turn_watermark = 0
        self.current_specialization = "general"
        self.current_conversation_id = "general-main"
        self.specialization_conversations = {"general": "general-main"}
        self._conversation_switching = False
        self.current_project_id: str | None = None
        self.current_task_id: str | None = None
        self._engineer_profile_restore = None
        self.engineer_status_loading = False
        self.evolution_status_loading = False
        self.busy = False
        self.last_assistant_reply = ""
        self.history_signature = None
        self.follow_latest = True
        self.attachments: list[Path] = []
        self.voice_active = False
        self.closing_when_idle = False
        self.engineer_project_input = QLineEdit()

        self._build_ui()
        self._load_history()
        self._set_style()

        QTimer.singleShot(0, self.input.setFocus)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(6000)
        self.history_timer = QTimer(self)
        self.history_timer.timeout.connect(self._sync_history)
        self.history_timer.start(6000)
        QTimer.singleShot(100, self.refresh_status)

    def _build_ui(self) -> None:
        self.setStyleSheet(ThemeManager.get_stylesheet())

        root_container = QWidget()
        root_container.setObjectName("appRoot")
        root_layout = QHBoxLayout(root_container)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root_container)

        # ---------------------------------------------------------------------
        # 1. Sidebar Retrátil (#sidebar / #sidebarWidget)
        # ---------------------------------------------------------------------
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebarWidget")
        self.sidebarWidget = self.sidebar
        self.sidebar.setFixedWidth(224)
        self.sidebar.setStyleSheet("""
            QFrame#sidebarWidget, QFrame#sidebar {
                background: #1A1D21;
                border-right: 1px solid #30363D;
            }
            QPushButton.navButton {
                background: transparent;
                color: #8B949E;
                border: none;
                border-radius: 6px;
                padding: 10px 14px;
                text-align: left;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton.navButton:hover {
                background: #22262B;
                color: #EAEAEA;
            }
            QPushButton.navButtonActive {
                background: #2D333B;
                color: #58A6FF;
                border: none;
                border-radius: 6px;
                padding: 10px 14px;
                text-align: left;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#navButtonActive {
                background: #2D333B;
                color: #58A6FF;
                border: none;
                border-radius: 6px;
                padding: 10px 14px;
                text-align: left;
                font-size: 13px;
                font-weight: 600;
            }
        """)

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(14, 20, 14, 16)
        sidebar_layout.setSpacing(6)

        brand_label = QLabel("MARK")
        brand_label.setStyleSheet("color: #EAEAEA; font-size: 16px; font-weight: 700; letter-spacing: 1px;")
        caption_label = QLabel("ASSISTENTE PESSOAL")
        caption_label.setStyleSheet("color: #6E7681; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;")
        sidebar_layout.addWidget(brand_label)
        sidebar_layout.addWidget(caption_label)
        sidebar_layout.addSpacing(18)

        section_conv = QLabel("CONVERSAR")
        section_conv.setStyleSheet("color: #6E7681; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;")
        sidebar_layout.addWidget(section_conv)

        # Botão "Geral" (muda o perfil no chat no índice 0)
        self.chat_nav_button = QPushButton("Geral")
        self.chat_nav_button.setObjectName("chatNavButton")
        self.chat_nav_button.setProperty("class", "navButtonActive")
        self.chat_nav_button.setIcon(_mark_icon("chat"))
        self.chat_nav_button.setIconSize(QSize(17, 17))
        self.chat_nav_button.setFixedHeight(38)
        self.chat_nav_button.clicked.connect(lambda: self.open_specialized_chat("general"))
        self.shared_chat_button = self.chat_nav_button
        sidebar_layout.addWidget(self.chat_nav_button)

        # Botão "Engenheiro" (muda o perfil no chat no índice 0)
        self.engineer_button = QPushButton("Engenheiro")
        self.engineer_button.setObjectName("engineerNavButton")
        self.engineer_button.setProperty("class", "navButton")
        self.engineer_button.setIcon(_mark_icon("github"))
        self.engineer_button.setIconSize(QSize(17, 17))
        self.engineer_button.setFixedHeight(38)
        self.engineer_button.clicked.connect(lambda: self.open_specialized_chat("engineer"))
        sidebar_layout.addWidget(self.engineer_button)

        # Botão "Professor" (muda o perfil no chat no índice 0)
        self.professor_button = QPushButton("Professor")
        self.professor_button.setObjectName("professorNavButton")
        self.professor_button.setProperty("class", "navButton")
        self.professor_button.setIcon(_mark_icon("chat"))
        self.professor_button.setIconSize(QSize(17, 17))
        self.professor_button.setFixedHeight(38)
        self.professor_button.clicked.connect(lambda: self.open_specialized_chat("professor"))
        sidebar_layout.addWidget(self.professor_button)

        sidebar_layout.addSpacing(16)
        section_nav = QLabel("SISTEMA & ATIVIDADES")
        section_nav.setStyleSheet("color: #6E7681; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;")
        sidebar_layout.addWidget(section_nav)

        # Botão "Configurações" (muda para o índice 1 do QStackedWidget)
        self.settings_nav_button = QPushButton("Configurações")
        self.settings_nav_button.setObjectName("settingsNavButton")
        self.settings_nav_button.setProperty("class", "navButton")
        self.settings_nav_button.setIcon(_mark_icon("menu"))
        self.settings_nav_button.setIconSize(QSize(17, 17))
        self.settings_nav_button.setFixedHeight(38)
        self.settings_nav_button.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(self.settings_nav_button)

        # Botão "Chamada de Voz" (muda para o índice 2 do QStackedWidget)
        self.call_nav_button = QPushButton("Chamada de Voz")
        self.call_nav_button.setObjectName("callNavButton")
        self.call_nav_button.setProperty("class", "navButton")
        self.call_nav_button.setIcon(_mark_icon("call"))
        self.call_nav_button.setIconSize(QSize(17, 17))
        self.call_nav_button.setFixedHeight(38)
        self.call_nav_button.clicked.connect(self.open_call)
        sidebar_layout.addWidget(self.call_nav_button)

        # Botão "Dispositivos" (abre o Hub de Dispositivos e Pareamento)
        self.devices_nav_button = QPushButton("Dispositivos")
        self.devices_nav_button.setObjectName("devicesNavButton")
        self.devices_nav_button.setProperty("class", "navButton")
        self.devices_nav_button.setIcon(_mark_icon("device"))
        self.devices_nav_button.setIconSize(QSize(17, 17))
        self.devices_nav_button.setFixedHeight(38)
        self.devices_nav_button.clicked.connect(self.open_mobile_devices)
        sidebar_layout.addWidget(self.devices_nav_button)

        # Botão Menu de Ferramentas Categorizado
        self.tools_button = QToolButton()
        self.tools_button.setObjectName("toolsButton")
        self.tools_button.setText("Ferramentas")
        self.tools_button.setIcon(_mark_icon("menu"))
        self.tools_button.setIconSize(QSize(17, 17))
        self.tools_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tools_button.setFixedHeight(38)
        self.tools_button.setAccessibleName("Abrir ferramentas do MARK")
        self._setup_tools_menu()
        sidebar_layout.addWidget(self.tools_button)

        sidebar_layout.addStretch(1)

        # Status
        self.api_status_label = QLabel("MARK · Operacional")
        self.api_status_label.setStyleSheet("color: #3FB950; font-size: 11px;")
        sidebar_layout.addWidget(self.api_status_label)

        self.api_status = self._status_row("MARK", "Verificando…")
        self.voice_status = self._status_row("Microfone", "Pronto")
        sidebar_layout.addWidget(self.api_status)
        sidebar_layout.addWidget(self.voice_status)

        root_layout.addWidget(self.sidebar)

        # ---------------------------------------------------------------------
        # 2. QStackedWidget Central (Índice 0: Chat, 1: Settings, 2: Voz)
        # ---------------------------------------------------------------------
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setObjectName("centralStackedWidget")
        self.page_stack = self.stacked_widget
        self.content_layout = self.stacked_widget

        # Índice 0: Chat
        self.chat_page = self._build_chat_page()
        self.stacked_widget.addWidget(self.chat_page)

        # Índice 1: Área de Trabalho / Configurações
        self.settings_page = self._build_settings_page()
        self.stacked_widget.addWidget(self.settings_page)

        # Índice 2: Chamada de Voz In-App
        self.voice_page = self._build_voice_page()
        self.stacked_widget.addWidget(self.voice_page)

        root_layout.addWidget(self.stacked_widget, 1)

        # Setup Command Palette (Ctrl+K / Cmd+K)
        self.command_palette = CommandPaletteDialog(self)
        self._setup_command_palette()
        self.cmd_k_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.cmd_k_shortcut.activated.connect(self.open_command_palette)
        self.cmd_k_mac_shortcut = QShortcut(QKeySequence("Meta+K"), self)
        self.cmd_k_mac_shortcut.activated.connect(self.open_command_palette)

        # Mapeamento para controle de navegação
        self.area_buttons = {
            "general": self.chat_nav_button,
            "engineer": self.engineer_button,
            "professor": self.professor_button,
            "settings": self.settings_nav_button,
            "call": self.call_nav_button,
        }

    def _build_chat_page(self) -> QWidget:
        content = QWidget()
        content.setObjectName("contentArea")
        main = QVBoxLayout(content)
        main.setContentsMargins(42, 22, 42, 20)
        main.setSpacing(12)

        # Top Header
        top = QHBoxLayout()
        top.setSpacing(12)

        toggle = QPushButton()
        toggle.setObjectName("iconButton")
        toggle.setIcon(_mark_icon("menu"))
        toggle.setIconSize(QSize(19, 19))
        toggle.setToolTip("Menu de Comandos (Ctrl+K)")
        toggle.setAccessibleName("Abrir menu de comandos")
        toggle.clicked.connect(self.open_command_palette)
        top.addWidget(toggle)

        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("Conversa")
        title.setObjectName("title")
        heading.addWidget(title)
        self.page_heading = title
        self.connection_label = QLabel("MARK operacional · memória compartilhada ativa")
        self.connection_label.setObjectName("connectionStatus")
        heading.addWidget(self.connection_label)
        top.addLayout(heading)

        self.engineer_chat_badge = QLabel("ENGINEER · QWEN CODER")
        self.engineer_chat_badge.setObjectName("engineerBadge")
        self.engineer_chat_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.engineer_chat_badge.hide()
        top.addWidget(self.engineer_chat_badge)

        self.conversation_selector = QComboBox()
        self.conversation_selector.setObjectName("chatSelector")
        self.conversation_selector.setMinimumWidth(190)
        self.conversation_selector.setToolTip("Conversas separadas por perfil; memórias salvas continuam compartilhadas")
        self.conversation_selector.currentIndexChanged.connect(self._select_conversation_from_header)
        self.conversation_selector.hide()
        top.addWidget(self.conversation_selector)

        self.new_conversation_button = QPushButton("Nova conversa")
        self.new_conversation_button.setObjectName("headerAction")
        self.new_conversation_button.clicked.connect(self._create_specialized_conversation)
        self.new_conversation_button.hide()
        top.addWidget(self.new_conversation_button)

        top.addStretch()

        # Botão Chamada no cabeçalho (muda para o índice 2 do QStackedWidget)
        self.call_button = QPushButton("Chamada")
        self.call_button.setObjectName("callButton")
        self.call_button.setIcon(_mark_icon("call"))
        self.call_button.setIconSize(QSize(17, 17))
        self.call_button.setToolTip("Iniciar chamada de voz in-app")
        self.call_button.clicked.connect(self.open_call)
        top.addWidget(self.call_button)

        # Botão Configurações no cabeçalho (muda para o índice 1 do QStackedWidget)
        self.settings_button = QPushButton("Configurações")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setIcon(_mark_icon("menu"))
        self.settings_button.setIconSize(QSize(17, 17))
        self.settings_button.setToolTip("Abrir painel de configurações")
        self.settings_button.clicked.connect(self.open_settings)
        top.addWidget(self.settings_button)

        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("iconButton")
        self.refresh_button.setIcon(_mark_icon("refresh"))
        self.refresh_button.setIconSize(QSize(18, 18))
        self.refresh_button.setToolTip("Atualizar estado do MARK")
        self.refresh_button.clicked.connect(self.refresh_status)
        top.addWidget(self.refresh_button)

        main.addLayout(top)

        divider = QFrame()
        divider.setObjectName("headerDivider")
        divider.setFixedHeight(1)
        main.addWidget(divider)

        # Feedback do Engineer
        self.engineer_work_feedback = QFrame()
        self.engineer_work_feedback.setObjectName("engineerWorkFeedback")
        feedback_layout = QVBoxLayout(self.engineer_work_feedback)
        feedback_layout.setContentsMargins(12, 6, 12, 6)
        feedback_layout.setSpacing(4)
        self.engineer_work_label = QLabel("Engineer está analisando sua solicitação…")
        self.engineer_work_label.setObjectName("engineerWorkLabel")
        feedback_layout.addWidget(self.engineer_work_label)
        self.engineer_work_bar = QProgressBar()
        self.engineer_work_bar.setObjectName("engineerWorkBar")
        self.engineer_work_bar.setRange(0, 0)
        self.engineer_work_bar.setTextVisible(False)
        self.engineer_work_bar.setMaximumHeight(4)
        self.engineer_work_feedback.hide()
        feedback_layout.addWidget(self.engineer_work_bar)
        main.addWidget(self.engineer_work_feedback)

        # Área de Mensagens (ScrollArea + Layout de Balões Soft Minimal Tech)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("chatScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.chat = QWidget()
        self.chat.setObjectName("chatContent")
        self.chat_layout = QVBoxLayout(self.chat)
        self.chat_layout.setContentsMargins(8, 24, 8, 24)
        self.chat_layout.setSpacing(18)

        # Empty State
        self.empty_state = QWidget()
        self.empty_state.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        empty = QVBoxLayout(self.empty_state)
        empty.setSpacing(10)
        empty.addStretch(2)
        eyebrow = QLabel("UM ASSISTENTE. TODOS OS SEUS DISPOSITIVOS.")
        eyebrow.setObjectName("welcomeEyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(eyebrow)
        welcome = QLabel("O que vamos fazer hoje?")
        welcome.setObjectName("welcomeTitle")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(welcome)
        hint = QLabel("Converse, planeje ou explore uma ideia com o MARK.")
        hint.setObjectName("welcomeHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(hint)
        empty.addSpacing(22)

        cards = QWidget()
        cards.setMaximumWidth(570)
        grid = QGridLayout(cards)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        suggestions = (
            ("Organizar meu dia", "calendar", "Me ajude a organizar meu dia e priorizar minhas tarefas."),
            ("Explorar uma ideia", "chat", "Quero desenvolver uma ideia. Me ajude a pensar nas opções."),
            ("Entender um arquivo", "attach", "Vou anexar um arquivo. Me ajude a entender e resumir o conteúdo."),
            ("Resolver um problema", "github", "Me ajude a investigar um problema passo a passo."),
        )
        for index, (caption, icon, prompt) in enumerate(suggestions):
            button = QPushButton(caption)
            button.setObjectName("suggestionCard")
            button.setIcon(_mark_icon(icon))
            button.setIconSize(QSize(22, 22))
            button.setMinimumHeight(70)
            button.setToolTip(prompt)
            button.clicked.connect(lambda checked=False, text=prompt: self._prefill_message(text))
            grid.addWidget(button, index // 2, index % 2)
        empty.addWidget(cards, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty.addStretch(3)

        self.chat_layout.addWidget(self.empty_state)
        self.chat_layout.addStretch(1)
        self.scroll.setWidget(self.chat)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._chat_range_changed)
        self.scroll.verticalScrollBar().sliderMoved.connect(
            lambda value: setattr(self, "follow_latest", self.scroll.verticalScrollBar().maximum() - value < 80)
        )
        self.scroll.verticalScrollBar().actionTriggered.connect(
            lambda _: QTimer.singleShot(0, self._record_manual_scroll)
        )
        self.scroll.viewport().installEventFilter(self)
        main.addWidget(self.scroll, 1)

        # Thinking Indicator Widget (oculto por padrão até ser chamado pelo chat)
        self.thinking_indicator = ThinkingIndicatorWidget(self)
        self.thinking_indicator.hide()
        main.addWidget(self.thinking_indicator)

        # God's Eye View - Painel de Execução Agentizada e Consent Bridge (Fase 2)
        self.gods_eye_widget = GodsEyeStatusWidget(self)
        self.consent_bridge = ConsentBridge(self)
        self.gods_eye_widget.cancel_requested.connect(self._cancel_active_agent_task)
        main.addWidget(self.gods_eye_widget)

        # Composer Box Completo (com botão Anexar à esquerda, entrada multilinha, seletores dinâmicos e botão cápsula)
        self.composer_box = QFrame()
        self.composer_box.setObjectName("composerBox")
        compose = QVBoxLayout(self.composer_box)
        compose.setContentsMargins(14, 10, 14, 10)
        compose.setSpacing(8)

        # Linha de entrada: Botão Anexar à esquerda da caixa de texto
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(10)

        self.attach_button = QPushButton()
        self.attach_button.setObjectName("attachButton")
        self.attach_button.setIcon(_mark_icon("attach"))
        self.attach_button.setIconSize(QSize(20, 20))
        self.attach_button.setToolTip("Anexar arquivos (imagens, documentos, código)")
        self.attach_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_button.setFixedHeight(44)
        self.attach_button.setFixedWidth(44)
        self.attach_button.clicked.connect(self.add_attachments)
        input_row.addWidget(self.attach_button, alignment=Qt.AlignmentFlag.AlignTop)

        self.input = Composer()
        self.input.setObjectName("messageInput")
        self.input.setPlaceholderText("Escreva para o MARK ou arraste um arquivo aqui… (Shift+Enter nova linha)")
        self.input.setFixedHeight(66)
        self.input.send_requested.connect(self.send_message)
        self.input.files_dropped.connect(self._add_attachment_paths)
        self.input.drag_hover_changed.connect(self._set_drop_target_state)
        input_row.addWidget(self.input, 1)

        compose.addLayout(input_row)

        self.attachment_list = QListWidget()
        self.attachment_list.setObjectName("attachmentList")
        self.attachment_list.setMaximumHeight(76)
        self.attachment_list.hide()
        compose.addWidget(self.attachment_list)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)

        self.remove_attachment_button = QPushButton()
        self.remove_attachment_button.setObjectName("attachButton")
        self.remove_attachment_button.setIcon(_mark_icon("trash"))
        self.remove_attachment_button.setIconSize(QSize(16, 16))
        self.remove_attachment_button.setToolTip("Remover o arquivo selecionado")
        self.remove_attachment_button.setDisabled(True)
        self.remove_attachment_button.hide()
        self.remove_attachment_button.clicked.connect(self.remove_selected_attachment)
        actions.addWidget(self.remove_attachment_button)
        self.attachment_list.currentRowChanged.connect(
            lambda row: self.remove_attachment_button.setEnabled(row >= 0)
        )

        self.composer_hint = QLabel("Enter envia · Shift+Enter nova linha")
        self.composer_hint.setObjectName("composerHint")
        actions.addWidget(self.composer_hint)

        actions.addStretch(1)

        # Seletores Dinâmicos de IA
        self.model_selector = ProviderSelectorWidget()
        self.model_selector.setObjectName("modelSelector")
        self.model_selector.populate_from_config()

        self.processing_badge = QLabel(self.model_selector.processing_type())
        self.processing_badge.setObjectName("processingBadge")
        self.model_selector.set_badge_widget(self.processing_badge)
        actions.addWidget(self.processing_badge)
        actions.addWidget(self.model_selector)

        self.reasoning_selector = QComboBox()
        self.reasoning_selector.setObjectName("reasoningSelector")
        self.reasoning_selector.addItem("Raciocínio Instantâneo", "instant")
        self.reasoning_selector.addItem("Raciocínio Balanceado", "medium")
        self.reasoning_selector.addItem("Raciocínio Profundo", "high")
        self.reasoning_selector.setCurrentIndex(1)
        self.reasoning_selector.setToolTip(
            "Instantâneo: sem raciocínio estendido, contexto 4k e até 512 tokens.\n"
            "Balanceado: contexto 6k e até 2048 tokens.\n"
            "Profundo: contexto 8k e até 4096 tokens, com explicação mais aprofundada."
        )
        self.model_selector.currentIndexChanged.connect(
            lambda _index: self.reasoning_selector.setEnabled(
                self.model_selector.currentData() == "mark" or self.model_selector.processing_type() == "Local Ollama"
            )
        )
        actions.addWidget(self.reasoning_selector)

        # Indicador de Destino Ativo (Pre-flight Target Disclosure)
        self.routing_target_badge = QLabel()
        self.routing_target_badge.setObjectName("routingTargetBadge")
        actions.addWidget(self.routing_target_badge)

        self.model_selector.currentIndexChanged.connect(lambda _i: self._update_routing_target_badge())
        self.input.textChanged.connect(self._update_routing_target_badge)

        # Botão de Envio (Design em cápsula)
        self.send_button = QPushButton()
        self.send_button.setObjectName("sendButton")
        self.send_button.setIcon(_mark_icon("send"))
        self.send_button.setIconSize(QSize(20, 20))
        self.send_button.setToolTip("Enviar mensagem")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.send_message)
        actions.addWidget(self.send_button)

        self._update_routing_target_badge()

        compose.addLayout(actions)
        main.addWidget(self.composer_box)

        # Barra de Telemetria e Alternador da Sidebar
        self.telemetry_box = QWidget()
        self.telemetry_box.setObjectName("telemetryBar")
        telemetry_layout = QHBoxLayout(self.telemetry_box)
        telemetry_layout.setContentsMargins(0, 4, 0, 0)
        telemetry_layout.setSpacing(14)

        self.sidebar_toggle_button = QPushButton("☰ Alternar Menu")
        self.sidebar_toggle_button.setObjectName("sidebarToggleButton")
        self.sidebar_toggle_button.setToolTip("Expandir ou recolher barra lateral")
        self.sidebar_toggle_button.clicked.connect(self.toggle_sidebar)
        telemetry_layout.addWidget(self.sidebar_toggle_button)

        self.cmd_palette_btn = QPushButton("⌘K Comandos")
        self.cmd_palette_btn.setObjectName("cmdPaletteButton")
        self.cmd_palette_btn.setToolTip("Abrir Menu de Comandos (Ctrl+K / Cmd+K)")
        self.cmd_palette_btn.clicked.connect(self.open_command_palette)
        telemetry_layout.addWidget(self.cmd_palette_btn)

        telemetry_layout.addStretch(1)

        self.bwrap_status_label = QLabel()
        self.bwrap_status_label.setObjectName("bwrapStatusLabel")
        bwrap_ok = _probe_bwrap_support()
        bwrap_color = "#3FB950" if bwrap_ok else "#6E7681"
        bwrap_text = "ATIVO" if bwrap_ok else "INATIVO"
        self.bwrap_status_label.setText(f"<span style='color:{bwrap_color};'>●</span> BWRAP: {bwrap_text}")
        self.bwrap_status_label.setStyleSheet("color: #8B949E; font-family: 'JetBrains Mono', monospace; font-size: 11px;")
        telemetry_layout.addWidget(self.bwrap_status_label)

        self.mtls_status_label = QLabel()
        self.mtls_status_label.setObjectName("mtlsStatusLabel")
        has_ts = bool(shutil.which("tailscale"))
        mtls_color = "#58A6FF" if has_ts else "#6E7681"
        mtls_text = "ATIVO" if has_ts else "INATIVO"
        self.mtls_status_label.setText(f"<span style='color:{mtls_color};'>●</span> mTLS: {mtls_text}")
        self.mtls_status_label.setStyleSheet("color: #8B949E; font-family: 'JetBrains Mono', monospace; font-size: 11px;")
        telemetry_layout.addWidget(self.mtls_status_label)

        self.ctx_status_label = QLabel("CTX: 8K")
        self.ctx_status_label.setObjectName("ctxStatusLabel")
        self.ctx_status_label.setStyleSheet("color: #8B949E; font-family: 'JetBrains Mono', monospace; font-size: 11px;")
        telemetry_layout.addWidget(self.ctx_status_label)

        main.addWidget(self.telemetry_box)

        return content

    def _build_settings_page(self) -> QWidget:
        self.settings_widget = SettingsWidget(vault=self.vault, on_back=self.open_chat, parent=self)
        self.back_button = self.settings_widget.back_button
        return self.settings_widget

    def _build_voice_page(self) -> QWidget:
        self.voice_widget = VoiceCallWidget(on_back=self.open_chat, parent=self)
        self.end_call_button = self.voice_widget.end_call_button
        return self.voice_widget

    # -------------------------------------------------------------------------
    # Métodos de Navegação
    # -------------------------------------------------------------------------
    def open_chat(self) -> None:
        """Retorna ao índice 0 (Chat)."""
        self.open_specialized_chat("general")

    def open_settings(self) -> None:
        """Navega para o índice 1 (Configurações / Cofre Universal de Chaves)."""
        if hasattr(self, "settings_widget"):
            self.settings_widget.refresh_keys_table()
        self.stacked_widget.setCurrentIndex(1)
        self._set_active_area("settings")

    def open_call(self) -> None:
        """Navega para o índice 2 (Chamada de Voz In-App)."""
        self.stacked_widget.setCurrentIndex(2)
        self._set_active_area("call")

    def toggle_sidebar(self) -> None:
        """Apenas expande ou recolhe a Sidebar sem abrir modais incorretos."""
        self.sidebar.setVisible(not self.sidebar.isVisible())

    def _set_active_area(self, active: str) -> None:
        if not hasattr(self, "area_buttons"):
            return
        for key, button in self.area_buttons.items():
            is_active = key == active
            button.setObjectName("navButtonActive" if is_active else "navButton")
            button.setProperty("class", "navButtonActive" if is_active else "navButton")
            button.style().unpolish(button)
            button.style().polish(button)

    def _update_nav_selection(self, active_tab: str) -> None:
        self._set_active_area(active_tab)

    # -------------------------------------------------------------------------
    # Menu de Ferramentas Categorizado e Command Palette
    # -------------------------------------------------------------------------
    def _setup_command_palette(self) -> None:
        cp = self.command_palette
        cp.add_category_header("--- SEGURANÇA E AGENTES ---")
        cp.register_command("Antivírus e ameaças · Sentinel", self.open_sentinel, "Segurança")
        cp.register_command("Dispositivos e segurança", self.open_mobile_devices, "Segurança")

        cp.add_category_header("--- ENGENHARIA E CÓDIGO ---")
        cp.register_command("Tarefas do Engineer", self.open_missions, "Engenharia")
        cp.register_command("Engenharia Autônoma · Sandbox", self.run_autonomous_engineering, "Engenharia")
        cp.register_command("Código · GitHub", self.open_github, "Engenharia")

        cp.add_category_header("--- UTILITÁRIOS E MÍDIA ---")
        cp.register_command("Agenda e lembretes", self.open_agenda, "Utilitários")
        cp.register_command("E-mail · Tuta", self.open_email_composer, "Utilitários")
        cp.register_command("Visão ao vivo / Câmera", self.open_vision, "Utilitários")
        cp.register_command("Alternar escuta por voz", self.toggle_voice, "Utilitários")

        cp.add_category_header("--- SISTEMA ---")
        cp.register_command("Configurações", self.open_settings, "Sistema")
        cp.register_command("Chamada de Voz", self.open_call, "Sistema")
        cp.register_command("Biblioteca de Memórias", self.open_library, "Sistema")
        cp.register_command("Evolução do MARK", self.open_evolution, "Sistema")
        cp.register_command("Plugins...", self.open_plugins, "Sistema")
        cp.register_command("Iniciar MARK", self.start_mark, "Sistema")
        cp.register_command("Limpar conversa", self.clear_memory, "Sistema")

    def _setup_tools_menu(self) -> None:
        self.tools_menu = QMenu(self.tools_button)

        def _add_category_header(menu: QMenu, title: str):
            header = menu.addAction(title)
            header.setEnabled(False)
            return header

        # --- SEGURANÇA E AGENTES ---
        _add_category_header(self.tools_menu, "--- SEGURANÇA E AGENTES ---")
        sentinel_action = self.tools_menu.addAction(_mark_icon("sentinel"), "Antivírus e ameaças · Sentinel")
        sentinel_action.triggered.connect(self.open_sentinel)
        mobile_action = self.tools_menu.addAction(_mark_icon("device"), "Dispositivos e segurança")
        mobile_action.triggered.connect(self.open_mobile_devices)

        # --- ENGENHARIA E CÓDIGO ---
        _add_category_header(self.tools_menu, "--- ENGENHARIA E CÓDIGO ---")
        missions_action = self.tools_menu.addAction(_mark_icon("github"), "Tarefas do Engineer")
        missions_action.triggered.connect(self.open_missions)
        sandbox_action = self.tools_menu.addAction(_mark_icon("github"), "Engenharia Autônoma · Sandbox")
        sandbox_action.triggered.connect(self.run_autonomous_engineering)
        github_action = self.tools_menu.addAction(_mark_icon("github"), "Código · GitHub")
        github_action.triggered.connect(self.open_github)

        # --- UTILITÁRIOS E MÍDIA ---
        _add_category_header(self.tools_menu, "--- UTILITÁRIOS E MÍDIA ---")
        agenda_action = self.tools_menu.addAction(_mark_icon("calendar"), "Agenda e lembretes")
        agenda_action.triggered.connect(self.open_agenda)
        email_action = self.tools_menu.addAction(_mark_icon("mail"), "E-mail · Tuta")
        email_action.triggered.connect(self.open_email_composer)
        vision_action = self.tools_menu.addAction(_mark_icon("camera"), "Visão ao vivo / Câmera")
        vision_action.triggered.connect(self.open_vision)
        self.voice_button = self.tools_menu.addAction(_mark_icon("microphone"), "Escuta por voz")
        self.voice_button.triggered.connect(self.toggle_voice)

        # --- SISTEMA ---
        _add_category_header(self.tools_menu, "--- SISTEMA ---")
        plugins_action = self.tools_menu.addAction(_mark_icon("menu"), "Plugins...")
        plugins_action.triggered.connect(self.open_plugins)
        self.connect_button = self.tools_menu.addAction(_mark_icon("power"), "Iniciar MARK")
        self.connect_button.triggered.connect(self.start_mark)
        clear_action = self.tools_menu.addAction(_mark_icon("trash"), "Limpar conversa")
        clear_action.triggered.connect(self.clear_memory)

        self.tools_button.setMenu(self.tools_menu)

    def open_command_palette(self) -> None:
        self.command_palette.search_input.clear()
        self.command_palette.populate()
        self.command_palette.search_input.setFocus()
        self.command_palette.exec()

    # -------------------------------------------------------------------------
    # Métodos Auxiliares de UI e Status
    # -------------------------------------------------------------------------
    def _status_row(self, name: str, value: str) -> QFrame:
        box = QFrame()
        box.setObjectName("statusBox")
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(8)
        label = QLabel(name)
        label.setObjectName("statusTitle")
        label.setStyleSheet("color: #8B949E; font-size: 11px;")
        text = QLabel(value)
        text.setObjectName("statusValue")
        text.setStyleSheet("color: #EAEAEA; font-size: 11px;")
        text.setWordWrap(True)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(text)
        box.setFixedHeight(34)
        box.value_label = text
        return box

    def _set_style(self) -> None:
        self.setStyleSheet(ThemeManager.get_stylesheet())

    def _scroll_to_latest(self) -> None:
        if self.follow_latest and hasattr(self, "scroll") and hasattr(self, "chat_layout"):
            self.chat_layout.activate()
            self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _schedule_bottom_scroll(self) -> None:
        for delay in (0, 30, 100):
            QTimer.singleShot(delay, self._scroll_to_latest)

    def _chat_range_changed(self, *_) -> None:
        if self.follow_latest:
            self._schedule_bottom_scroll()

    def _record_manual_scroll(self) -> None:
        if hasattr(self, "scroll"):
            bar = self.scroll.verticalScrollBar()
            self.follow_latest = bar.maximum() - bar.value() < 80

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if hasattr(self, "scroll") and watched is self.scroll.viewport() and event.type() == QEvent.Type.Wheel:
            self.follow_latest = False
            QTimer.singleShot(0, self._record_manual_scroll)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "composer_hint"):
            self.composer_hint.setVisible(self.width() >= 1100)

    # -------------------------------------------------------------------------
    # Métodos de Armazenamento e Histórico de Conversas (SQLite)
    # -------------------------------------------------------------------------
    def _conversations_file(self) -> Path:
        memory_file = Path(getattr(self, "_memory_file", MEMORY_FILE))
        return memory_file.with_name("desktop-conversations.sqlite3")

    def _ensure_conversation_store(self) -> None:
        db = self._conversation_db()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, specialization TEXT NOT NULL, title TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
                created_at INTEGER NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS conversation_messages_order ON conversation_messages(conversation_id,id)")
            db.execute("""CREATE TABLE IF NOT EXISTS imported_remote_turns (
                conversation_id TEXT NOT NULL, turn_id INTEGER NOT NULL, source TEXT NOT NULL,
                PRIMARY KEY(conversation_id,turn_id))""")
            now = int(time.time())
            db.execute("INSERT OR IGNORE INTO conversations VALUES('general-main','general','Geral',?,?)", (now, now))
            for specialization, title in (("engineer", "Nova conversa"), ("professor", "Nova conversa")):
                exists = db.execute("SELECT 1 FROM conversations WHERE specialization=? LIMIT 1", (specialization,)).fetchone()
                if not exists:
                    db.execute("INSERT INTO conversations VALUES(?,?,?,?,?)",
                               (uuid4().hex, specialization, title, now, now))
            general_count = db.execute(
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id='general-main'"
            ).fetchone()[0]
            memory_file = Path(getattr(self, "_memory_file", MEMORY_FILE))
            if not general_count and memory_file.exists():
                info = memory_file.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077:
                    legacy = sqlite3.connect(f"file:{memory_file}?mode=ro", uri=True, timeout=0.2)
                    try:
                        old_turns = legacy.execute(
                            "SELECT id,created_at,user_text,assistant_text FROM turns ORDER BY id DESC LIMIT 30"
                        ).fetchall()
                    except sqlite3.Error:
                        old_turns = []
                    finally:
                        legacy.close()
                    for turn_id, created, user_text, assistant_text in reversed(old_turns):
                        db.execute("INSERT OR IGNORE INTO imported_remote_turns(conversation_id,turn_id,source) VALUES('general-main',?, 'legacy')",
                                   (turn_id,))
                        db.execute("INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES('general-main','user',?,?)",
                                   (user_text, created))
                        db.execute("INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES('general-main','assistant',?,?)",
                                   (assistant_text, created))
            db.commit()
        finally:
            db.close()

    def _select_conversation_from_header(self, _index: int) -> None:
        if self._conversation_switching or self.current_specialization == "general":
            return
        conversation_id = self.conversation_selector.currentData()
        if conversation_id:
            self.current_conversation_id = conversation_id
            self._load_active_conversation()

    def _create_specialized_conversation(self) -> None:
        specialization = self.current_specialization
        if specialization not in {"engineer", "professor"}:
            return
        conversation_id = uuid4().hex
        now = int(time.time())
        db = self._conversation_db()
        try:
            db.execute("INSERT INTO conversations VALUES(?,?,?,?,?)",
                       (conversation_id, specialization, "Nova conversa", now, now))
            db.commit()
        finally:
            db.close()
        self.current_conversation_id = conversation_id
        self.specialization_conversations[specialization] = conversation_id
        self._refresh_conversation_selector(conversation_id)
        self._load_active_conversation()
        self.input.setFocus()

    def _load_active_conversation(self) -> None:
        if not hasattr(self, "chat_layout"):
            return
        try:
            db = self._conversation_db()
            try:
                rows = db.execute(
                    "SELECT role,content,created_at FROM conversation_messages WHERE conversation_id=? ORDER BY id",
                    (self.current_conversation_id,),
                ).fetchall()
            finally:
                db.close()
        except sqlite3.Error:
            rows = []
        if not rows:
            if self.empty_state is not None:
                self.empty_state.show()
                self.last_assistant_reply = ""
                self.history_signature = None
                return
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget is self.empty_state:
                    self.empty_state = None
                widget.deleteLater()
        self.last_assistant_reply = ""
        for role, content, created in rows:
            self._append_bubble(content, role, created, auto_scroll=False)
            if role == "assistant":
                self.last_assistant_reply = content
        self.history_signature = None
        if rows:
            self._schedule_bottom_scroll()

    def _import_shared_turns(self) -> None:
        memory_file = Path(getattr(self, "_memory_file", MEMORY_FILE))
        if not memory_file.exists():
            return
        info = memory_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return
        legacy = sqlite3.connect(f"file:{memory_file}?mode=ro", uri=True, timeout=0.2)
        try:
            turns = legacy.execute(
                "SELECT id,created_at,source,user_text,assistant_text FROM turns WHERE id>? ORDER BY id",
                (self._shared_turn_watermark,),
            ).fetchall()
        except sqlite3.Error:
            turns = []
        finally:
            legacy.close()
        if not turns:
            return
        db = self._conversation_db()
        try:
            for turn_id, created, source, user_text, assistant_text in turns:
                db.execute("INSERT OR IGNORE INTO imported_remote_turns(conversation_id,turn_id,source) VALUES(?,?,?)",
                           (self.current_conversation_id, turn_id, source or ""))
                if db.execute("SELECT changes()").fetchone()[0]:
                    db.execute("INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                               (self.current_conversation_id, "user", user_text, created))
                    db.execute("INSERT INTO conversation_messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                               (self.current_conversation_id, "assistant", assistant_text, created))
                self._shared_turn_watermark = max(self._shared_turn_watermark, turn_id)
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (int(time.time()), self.current_conversation_id))
            db.commit()
        finally:
            db.close()

    # -------------------------------------------------------------------------
    # Operações de Serviços e Diálogos de Sistema
    # -------------------------------------------------------------------------
    def refresh_status(self) -> None:
        def check():
            states = {}
            for key, unit in (("api", MOBILE_SERVICE), ("voice", VOICE_SERVICE)):
                result = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=5)
                states[key] = result.stdout.strip() == "active"
            try:
                status, _ = _request("/health", timeout=4)
                states["reachable"] = status == 200
            except Exception:
                states["reachable"] = False
            return states

        def update(states):
            if hasattr(self, "api_status") and hasattr(self.api_status, "value_label"):
                self.api_status.value_label.setText("Conectado" if states["reachable"] else "Offline")
                self.api_status.value_label.setStyleSheet("color: #3FB950" if states["reachable"] else "color: #F85149")
            if hasattr(self, "api_status_label"):
                self.api_status_label.setText("MARK · Conectado" if states["reachable"] else "MARK · Offline")
                self.api_status_label.setStyleSheet("color: #3FB950; font-size: 11px;" if states["reachable"] else "color: #F85149; font-size: 11px;")
            if hasattr(self, "connect_button"):
                self.connect_button.setText("MARK conectado" if states["reachable"] else "Iniciar MARK")
                self.connect_button.setEnabled(not states["reachable"])
            self.voice_active = states["voice"]
            if hasattr(self, "voice_status") and hasattr(self.voice_status, "value_label"):
                self.voice_status.value_label.setText("Ativa" if self.voice_active else "Desligada")
                self.voice_status.value_label.setStyleSheet("color: #3FB950" if self.voice_active else "color: #8B949E")
            if hasattr(self, "voice_button"):
                self.voice_button.setText("Parar escuta" if self.voice_active else "Iniciar escuta")
            if hasattr(self, "connection_label"):
                self.connection_label.setText(
                    "MARK está pensando…" if self.busy else "Conectado · memória compartilhada ativa" if states["reachable"] else "Offline · inicie o serviço do MARK"
                )

        self._task(check, update)

    def toggle_voice(self) -> None:
        starting = not self.voice_active
        if hasattr(self, "voice_button"):
            self.voice_button.setDisabled(True)
        action = "start" if starting else "stop"

        def done(_):
            if hasattr(self, "voice_button"):
                self.voice_button.setDisabled(False)
            self.refresh_status()

        def failed(message):
            if hasattr(self, "voice_button"):
                self.voice_button.setDisabled(False)
            QMessageBox.warning(self, "MARK", "Não consegui alterar a escuta por voz.\n\n" + message)
            self.refresh_status()

        self._task(lambda: _service(action, VOICE_SERVICE), done, failed)

    def start_mark(self) -> None:
        if hasattr(self, "connect_button"):
            self.connect_button.setDisabled(True)

        def start():
            _service("start", MOBILE_SERVICE)
            return True

        def done(_):
            self.refresh_status()

        def failed(message):
            if hasattr(self, "connect_button"):
                self.connect_button.setEnabled(True)
            QMessageBox.warning(self, "MARK", "Não consegui iniciar o serviço do MARK.\n\n" + message)

        self._task(start, done, failed)

    def open_agenda(self) -> None:
        dialog = AgendaDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_email_composer(self) -> None:
        dialog = EmailComposerDialog(self.last_assistant_reply, self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_github(self) -> None:
        from ..config import load_config
        from ..plugins import enabled as plugin_enabled
        if not plugin_enabled("github", load_config()):
            QMessageBox.information(self, "Plugins", "Ative o plugin GitHub em Ferramentas → Plugins.")
            return
        dialog = GitHubDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_mobile_devices(self) -> None:
        dialog = DeviceHubDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_library(self) -> None:
        if not hasattr(self, "library_page"):
            from ..cognition.knowledge import DEFAULT_DB
            from ..config import load_config
            from ..desktop_library import LibraryPage
            cfg = load_config()
            self.library_page = LibraryPage(self, Path(cfg.get("cognition", {}).get("knowledge_db", str(DEFAULT_DB))))
            self.page_stack.addWidget(self.library_page)
        self.page_stack.setCurrentWidget(self.library_page)

    def open_sentinel(self) -> None:
        from ..desktop_sentinel import SentinelDialog
        dialog = SentinelDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_missions(self) -> None:
        from ..desktop_missions import MissionDialog
        dialog = MissionDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_evolution(self) -> None:
        self.open_evolution_dialog()

    def open_evolution_dialog(self) -> None:
        from ..desktop_evolution import EvolutionDialog
        dialog = EvolutionDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def _cancel_active_agent_task(self) -> None:
        """Interrompe graciosamente a execução da tarefa agentizada ativa."""
        if getattr(self, "current_chat_task", None) is not None:
            self.current_chat_task.cancel()
        if hasattr(self, "gods_eye_widget") and self.gods_eye_widget is not None:
            self.gods_eye_widget.update_state("Cancelado", "", "Cancelamento solicitado pelo operador...")

    def gods_eye_mcp(self) -> Any:
        from ..mcp_gods_eye import GodsEyeMCPBridge
        if getattr(self, "_gods_eye_bridge", None) is None:
            self._gods_eye_bridge = GodsEyeMCPBridge()
        return self._gods_eye_bridge

    def sandbox_mcp(self, workspace: Path | None = None) -> Any:
        from ..mcp_sandbox import SandboxMCPBridge
        if workspace is not None:
            return SandboxMCPBridge(workspace=workspace)
        if getattr(self, "_sandbox_bridge", None) is None:
            self._sandbox_bridge = SandboxMCPBridge(workspace=ROOT)
        return self._sandbox_bridge

    def run_autonomous_engineering(self, task: str | None = None, target_file: str | None = None) -> None:
        if not task:
            task, ok = QInputDialog.getText(
                self,
                "Engenharia Autônoma · Sandbox",
                "Descreva a tarefa de código para o EngineerAgent:",
            )
            if not ok or not task.strip():
                return
        if not target_file:
            target_file, ok = QInputDialog.getText(
                self,
                "Engenharia Autônoma · Sandbox",
                "Caminho relativo do arquivo alvo no workspace:",
            )
            if not ok or not target_file.strip():
                return

        bridge = self.sandbox_mcp()

        def execute():
            return bridge.execute_tool(
                "engineer_solve_task",
                {"task": task.strip(), "target_file": target_file.strip()},
            )

        def done(payload):
            report = payload.get("result", {})
            msg = report.get("message", "Execução concluída.")
            QMessageBox.information(self, "Engenharia Autônoma", msg)

        def failed(error_msg):
            QMessageBox.warning(self, "Engenharia Autônoma", "Falha no sandbox autônomo:\n\n" + error_msg)

        self._task(execute, done, failed)

    def open_vision(self) -> None:
        from ..config import load_config
        from ..plugins import enabled as plugin_enabled
        if not plugin_enabled("gods_eye", load_config()):
            QMessageBox.information(self, "Plugins", "Ative o plugin God’s Eye View em Ferramentas → Plugins.")
            return
        from ..desktop_vision import VisionDialog
        dialog = VisionDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def test_camera(self) -> None:
        button = self.sender()
        if isinstance(button, QPushButton):
            button.setDisabled(True)
            button.setText("Testando câmera…")

        def check():
            result = subprocess.run([str(ROOT / "scripts/test_camera.sh")], capture_output=True, text=True, timeout=35)
            if result.returncode:
                raise RuntimeError((result.stdout or "A câmera não respondeu.").strip())
            return (result.stdout or "Captura da câmera: OK.").strip()

        def done(message):
            button.setDisabled(False)
            button.setText("Testar câmera")
            QMessageBox.information(self, "Teste da câmera", message)

        def failed(message):
            button.setDisabled(False)
            button.setText("Testar câmera")
            QMessageBox.warning(self, "Teste da câmera", message)

        self._task(check, done, failed)

    def clear_memory(self) -> None:
        phrase, accepted = QInputDialog.getText(
            self,
            "Apagar conversa",
            "Esta ação apaga a conversa compartilhada em todos os dispositivos.\n\n"
            "Para confirmar, digite MARK:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted or phrase != "MARK":
            return
        self.busy = True
        self.send_button.setDisabled(True)

        def clear():
            _, body = _request("/v1/chat/completions", {
                "model": "mark", "stream": False,
                "messages": [{"role": "user", "content": "limpa a memória"}],
            })
            return json.loads(body)["choices"][0]["message"]["content"]

        def done(reply):
            self.busy = False
            self.send_button.setDisabled(False)
            while self.chat_layout.count() > 1:
                item = self.chat_layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            self._append_bubble(reply, "assistant")

        def failed(message):
            self.busy = False
            self.send_button.setDisabled(False)
            QMessageBox.warning(self, "MARK", "Não foi possível limpar a conversa.\n\n" + message)

        self._task(clear, done, failed)

    def _update_routing_target_badge(self, decision: Any | None = None) -> None:
        """Atualiza dinamicamente o Indicador de Destino Ativo com a resolução de pré-voo."""
        if not hasattr(self, "routing_target_badge") or self.routing_target_badge is None:
            return

        if decision is None:
            from ..cognition.brain_routing import get_brain_router
            router = get_brain_router()
            text = self.input.toPlainText().strip() if hasattr(self, "input") else ""
            ctx = {}
            if hasattr(self, "model_selector") and self.model_selector is not None:
                if self.model_selector.processing_type() == "Nuvem":
                    ctx["selected_provider"] = self.model_selector.current_provider()
                    ctx["selected_model"] = self.model_selector.current_model_id()
                else:
                    ctx["selected_provider"] = "ollama"
                    ctx["selected_model"] = self.model_selector.current_model_id()
            decision = router.resolve_target(text, context=ctx)

        self.routing_target_badge.setText(decision.badge_text)
        self.routing_target_badge.setToolTip(f"{decision.reason}\nNível de privacidade: {decision.privacy_level}")

        target_prop = "local"
        pt = str(decision.provider_type)
        if decision.is_sensitive and "CLOUD" in pt:
            target_prop = "error"
        elif "CLOUD" in pt:
            target_prop = "cloud" if decision.session_authorized or not decision.requires_user_confirmation else "warning"
        elif "SECONDARY" in pt:
            target_prop = "local"
        else:
            target_prop = "local"

        self.routing_target_badge.setProperty("target", target_prop)
        style = self.routing_target_badge.style()
        if style:
            style.unpolish(self.routing_target_badge)
            style.polish(self.routing_target_badge)

    def confirm_cloud_egress(self, decision: Any) -> bool:
        """Trava de consentimento prévio: Pop-up de Consentimento Rápido antes do envio para a nuvem."""
        if getattr(decision, "is_sensitive", False):
            QMessageBox.critical(
                self,
                "Bloqueio de Segurança Estrita",
                "A mensagem contém dados confidenciais/sensíveis detectados no payload.\n\n"
                "A saída externa para provedores em nuvem foi cancelada e bloqueada por política de privacidade estrita.",
            )
            return False

        prov_name = str(getattr(decision, "provider_name", "Nuvem")).capitalize()
        model_name = str(getattr(decision, "model_name", "padrão"))
        msg = (
            f"O MARK identificou que esta requisição será processada via API de Nuvem ({prov_name}: {model_name}).\n\n"
            "Por padrão de privacidade restrita, nenhuma informação é transmitida externamente sem o seu consentimento explícito.\n\n"
            "Deseja autorizar a transmissão externa para esta requisição?"
        )
        reply = QMessageBox.question(
            self,
            "Autorização de Rota Externa (Nuvem)",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes
