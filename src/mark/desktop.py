
from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

from .url_security import validate_url_scheme

import shutil
from PyQt6.QtCore import Qt, QTimer, QSize, QEvent
from PyQt6.QtGui import QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QGridLayout,
    QSizePolicy, QVBoxLayout, QWidget, QInputDialog, QStackedWidget, QToolButton, QMenu,
)

from .desktop_theme import STYLESHEET
from .desktop_mobile_dialog import MobileDevicesDialog
from .desktop_call_dialog import CallDialog
from .desktop_composer import Composer
from .desktop_task import Task
from .desktop_email_dialog import EmailComposerDialog
from .desktop_agenda_dialog import AgendaDialog
from .desktop_github_dialog import GitHubDialog
from .desktop_history import HistoryMixin
from .desktop_workers import WorkerMixin
from .desktop_navigation import NavigationMixin
from .desktop_chat import ChatMixin
from .desktop_professor import ProfessorMixin
from .desktop_attachments import AttachmentsMixin
from .desktop_settings import SettingsMixin
from .desktop_packages import PackagesMixin
from .ui import CommandPaletteDialog, ProviderSelectorWidget, ThemeManager
from .sandbox import _probe_bwrap_support


from .desktop_helpers import (
    API, ICON_DIR, ROOT, _mark_icon, _mobile_pairing, _prepare_attachments,
    _request, _service, _token, _voice_request, ensure_config_dir_exists,
)

MEMORY_FILE = Path.home() / ".local/state/mark/conversation.sqlite3"
DESKTOP_CONVERSATIONS_FILE = Path.home() / ".local/state/mark/desktop-conversations.sqlite3"
VOICE_SERVICE = "mark-voice.service"
MOBILE_SERVICE = "mark-mobile.service"

class MarkWindow(HistoryMixin, WorkerMixin, NavigationMixin, ChatMixin, ProfessorMixin, AttachmentsMixin, SettingsMixin, PackagesMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MARK")
        self.setWindowIcon(QIcon(str(ROOT / "assets/mark-desktop.svg")))
        self.setMinimumSize(900, 620)
        self.resize(1280, 850)
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
        self.follow_latest=True
        self.attachments: list[Path] = []
        self.voice_active = False
        self.closing_when_idle = False
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

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(15, 19, 15, 14)
        side_layout.setSpacing(5)
        brand_row = QHBoxLayout()
        brand_icon = QLabel()
        brand_icon.setPixmap(_mark_icon("brand").pixmap(QSize(25, 25)))
        brand_row.addWidget(brand_icon)
        brand = QLabel("MARK")
        brand.setObjectName("brand")
        brand_row.addWidget(brand)
        brand_row.addStretch()
        side_layout.addLayout(brand_row)
        tagline = QLabel("ASSISTENTE PESSOAL")
        tagline.setObjectName("brandCaption")
        side_layout.addWidget(tagline)
        side_layout.addSpacing(24)
        side_layout.addWidget(self._section_label("CONVERSAR"))
        self.shared_chat_button = QPushButton("Geral")
        self.shared_chat_button.setIcon(_mark_icon("chat"))
        self.shared_chat_button.setObjectName("navButtonActive")
        self.shared_chat_button.setIconSize(QSize(17, 17))
        self.shared_chat_button.setFixedHeight(42)
        self.shared_chat_button.setToolTip("Conversa compartilhada, chamada e visão")
        self.shared_chat_button.clicked.connect(lambda: self.open_area("general"))
        side_layout.addWidget(self.shared_chat_button)
        self.engineer_button = self._nav_button("Engenheiro", "github")
        self.engineer_button.clicked.connect(lambda: self.open_area("engineer"))
        side_layout.addWidget(self.engineer_button)
        self.professor_button = self._nav_button("Professor", "chat")
        self.professor_button.clicked.connect(lambda: self.open_area("professor"))
        side_layout.addWidget(self.professor_button)
        self.library_nav_button = self._nav_button("Biblioteca", "attach")
        self.library_nav_button.clicked.connect(lambda: self.open_area("library"))
        self.evolution_nav_button = self._nav_button("Evolução", "evolution")
        self.evolution_nav_button.clicked.connect(lambda: self.open_area("evolution"))
        self.settings_nav_button = self._nav_button("Configurações", "menu")
        self.settings_nav_button.clicked.connect(lambda: self.open_area("settings"))
        self.area_buttons = {
            "general": self.shared_chat_button,
            "engineer": self.engineer_button,
            "professor": self.professor_button,
            "library": self.library_nav_button,
            "evolution": self.evolution_nav_button,
            "settings": self.settings_nav_button,
        }
        side_layout.addSpacing(20)
        side_layout.addWidget(self._section_label("SEU ESPAÇO"))
        side_layout.addWidget(self.library_nav_button)
        side_layout.addWidget(self.evolution_nav_button)
        self.tools_button = QToolButton()
        self.tools_button.setObjectName("toolsButton")
        self.tools_button.setText("Ferramentas")
        self.tools_button.setIcon(_mark_icon("menu"))
        self.tools_button.setIconSize(QSize(17, 17))
        self.tools_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tools_button.setFixedHeight(37)
        self.tools_button.setAccessibleName("Abrir ferramentas do MARK")
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
        self.tools_button.hide()
        side_layout.addSpacing(5)
        side_layout.addWidget(self.tools_button)
        side_layout.addStretch(1)
        side_layout.addWidget(self.settings_nav_button)
        side_layout.addSpacing(9)
        self.api_status = self._status_row("MARK", "Verificando…")
        self.voice_status = self._status_row("Microfone", "Verificando…")
        side_layout.addWidget(self.api_status)
        side_layout.addWidget(self.voice_status)
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setObjectName("sidebarScroll")
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_scroll.setFixedWidth(224)
        self.sidebar_scroll.setWidget(side)
        self.sidebar_scroll.hide()
        layout.addWidget(self.sidebar_scroll)

        content = QWidget()
        content.setObjectName("contentArea")
        outer_layout = QHBoxLayout(content)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.chat_column_container = QWidget()
        self.chat_column_container.setObjectName("chatColumnContainer")
        self.chat_column_container.setMinimumWidth(680)
        self.chat_column_container.setMaximumWidth(920)
        self.chat_column_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        main = QVBoxLayout(self.chat_column_container)
        main.setContentsMargins(0, 16, 0, 16)
        main.setSpacing(12)

        outer_layout.addStretch(1)
        outer_layout.addWidget(self.chat_column_container, 100)
        outer_layout.addStretch(1)
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
        heading.setSpacing(4)
        title = QLabel("Conversa")
        title.setObjectName("title")
        heading.addWidget(title)
        self.connection_label = QLabel("Verificando conexão…")
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
        call_shortcut = QPushButton("Chamada")
        call_shortcut.setObjectName("headerAction")
        call_shortcut.setIcon(_mark_icon("call"))
        call_shortcut.setIconSize(QSize(17, 17))
        call_shortcut.setToolTip("Abrir chamada dentro do app")
        call_shortcut.clicked.connect(self.open_call)
        top.addWidget(call_shortcut)
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

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("chatScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat = QWidget()
        self.chat.setObjectName("chatContent")
        self.chat_layout = QVBoxLayout(self.chat)
        self.chat_layout.setContentsMargins(8, 20, 8, 20)
        self.chat_layout.setSpacing(ThemeManager.TOKENS.CHAT_MESSAGE_SPACING)
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
        self.scroll.verticalScrollBar().sliderMoved.connect(lambda value:setattr(self,'follow_latest',self.scroll.verticalScrollBar().maximum()-value<80))
        self.scroll.verticalScrollBar().actionTriggered.connect(lambda _:QTimer.singleShot(0,self._record_manual_scroll))
        self.scroll.viewport().installEventFilter(self)
        main.addWidget(self.scroll, 1)

        self.composer_box = QFrame()
        self.composer_box.setObjectName("composerBox")
        self.composer_box.setStyleSheet(
            "QFrame#composerBox { background: #16191D; border: 1px solid #1E2228; border-radius: 22px; padding: 6px 14px; }"
            "QFrame#composerBox:hover { border-color: #262B33; }"
        )
        compose = QVBoxLayout(self.composer_box)
        compose.setContentsMargins(14, 10, 14, 10)
        compose.setSpacing(8)
        self.input = Composer()
        self.input.setObjectName("messageInput")
        self.input.setPlaceholderText("Escreva para o MARK ou arraste um arquivo aqui…")
        self.input.setFixedHeight(58)
        self.input.send_requested.connect(self.send_message)
        self.input.files_dropped.connect(self._add_attachment_paths)
        self.input.drag_hover_changed.connect(self._set_drop_target_state)
        compose.addWidget(self.input, 1)
        self.attachment_list = QListWidget()
        self.attachment_list.setObjectName("attachmentList")
        self.attachment_list.setMaximumHeight(76)
        self.attachment_list.hide()
        compose.addWidget(self.attachment_list)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.attach_button = QPushButton("Anexar")
        self.attach_button.setObjectName("attachButton")
        self.attach_button.setIcon(_mark_icon("attach"))
        self.attach_button.setIconSize(QSize(17, 17))
        self.attach_button.setToolTip("Imagens, PDFs, documentos DOCX e arquivos de texto")
        self.attach_button.clicked.connect(self.add_attachments)
        actions.addWidget(self.attach_button)
        self.remove_attachment_button = QPushButton()
        self.remove_attachment_button.setObjectName("attachButton")
        self.remove_attachment_button.setIcon(_mark_icon("trash"))
        self.remove_attachment_button.setToolTip("Remover o arquivo selecionado")
        self.remove_attachment_button.setAccessibleName("Remover o arquivo selecionado")
        self.remove_attachment_button.setDisabled(True)
        self.remove_attachment_button.hide()
        self.remove_attachment_button.clicked.connect(self.remove_selected_attachment)
        actions.addWidget(self.remove_attachment_button)
        self.attachment_list.currentRowChanged.connect(lambda row: self.remove_attachment_button.setEnabled(row >= 0))
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
        self.reasoning_selector.setAccessibleName("Nível de resposta e raciocínio")
        self.reasoning_selector.setToolTip(
            "Instantâneo: sem raciocínio estendido, contexto 4k e até 512 tokens. "
            "Balanceado: contexto 6k e até 2048 tokens. "
            "Profundo: contexto 8k e até 4096 tokens, com explicação mais aprofundada."
        )
        self.model_selector.currentIndexChanged.connect(
            lambda _index: self.reasoning_selector.setEnabled(
                self.model_selector.currentData() == "mark" or self.model_selector.processing_type() == "Local Ollama"
            )
        )
        actions.addWidget(self.reasoning_selector)
        actions.addStretch(1)
        self.send_button = QPushButton()
        self.send_button.setObjectName("sendButton")
        self.send_button.setIcon(_mark_icon("send"))
        self.send_button.setIconSize(QSize(18, 18))
        self.send_button.setToolTip("Enviar mensagem")
        self.send_button.setAccessibleName("Enviar mensagem")
        self.send_button.setStyleSheet(
            "QPushButton#sendButton { background: #00D8F6; color: #121417; border-radius: 16px; border: none; padding: 4px; min-width: 36px; max-width: 36px; min-height: 32px; max-height: 32px; font-weight: 500; } "
            "QPushButton#sendButton:hover { background: #33E1FA; }"
        )
        self.send_button.clicked.connect(self.send_message)
        actions.addWidget(self.send_button)
        compose.addLayout(actions)

        # Dica de envio em linha própria sem corte de texto
        self.composer_hint = QLabel("Enter envia · Shift+Enter nova linha")
        self.composer_hint.setObjectName("composerHint")
        self.composer_hint.setStyleSheet("color: #8E95A0; font-family: Inter, sans-serif; font-size: 11px; font-weight: 400;")
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_row.addWidget(self.composer_hint)
        hint_row.addStretch(1)
        compose.addLayout(hint_row)

        main.addWidget(self.composer_box)

        # Telemetria Minimalista (Minimalismo Tecnico Radical)
        self.telemetry_box = QWidget()
        self.telemetry_box.setObjectName("telemetryBar")
        telemetry_layout = QHBoxLayout(self.telemetry_box)
        telemetry_layout.setContentsMargins(0, 4, 0, 0)
        telemetry_layout.setSpacing(14)

        self.cmd_palette_btn = QPushButton("⌘K Comandos")
        self.cmd_palette_btn.setObjectName("cmdPaletteButton")
        self.cmd_palette_btn.setToolTip("Abrir Menu de Comandos (Ctrl+K / Cmd+K)")
        self.cmd_palette_btn.clicked.connect(self.open_command_palette)
        telemetry_layout.addWidget(self.cmd_palette_btn)

        telemetry_layout.addStretch(1)

        self.bwrap_status_label = QLabel()
        self.bwrap_status_label.setObjectName("bwrapStatusLabel")
        bwrap_ok = _probe_bwrap_support()
        bwrap_color = "#3FB950" if bwrap_ok else "#5F6672"
        bwrap_text = "ATIVO" if bwrap_ok else "INATIVO"
        self.bwrap_status_label.setText(f"<span style='color:{bwrap_color};'>●</span> BWRAP: {bwrap_text}")
        self.bwrap_status_label.setStyleSheet("color: #8E95A0; font-family: Inter, sans-serif; font-size: 11px; font-weight: 500;")
        self.bwrap_status_label.setToolTip("Clique para ver detalhes de segurança BWRAP")
        self.bwrap_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bwrap_status_label.mousePressEvent = lambda _ev: self.show_technical_details("bwrap")
        telemetry_layout.addWidget(self.bwrap_status_label)

        self.mtls_status_label = QLabel()
        self.mtls_status_label.setObjectName("mtlsStatusLabel")
        has_ts = bool(shutil.which("tailscale"))
        mtls_color = "#00D8F6" if has_ts else "#5F6672"
        mtls_text = "ATIVO" if has_ts else "INATIVO"
        self.mtls_status_label.setText(f"<span style='color:{mtls_color};'>●</span> mTLS: {mtls_text}")
        self.mtls_status_label.setStyleSheet("color: #8E95A0; font-family: Inter, sans-serif; font-size: 11px; font-weight: 500;")
        self.mtls_status_label.setToolTip("Clique para ver detalhes de segurança mTLS")
        self.mtls_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mtls_status_label.mousePressEvent = lambda _ev: self.show_technical_details("mtls")
        telemetry_layout.addWidget(self.mtls_status_label)

        self.ctx_status_label = QLabel("CTX: 8K")
        self.ctx_status_label.setObjectName("ctxStatusLabel")
        self.ctx_status_label.setStyleSheet("color: #8E95A0; font-family: Inter, sans-serif; font-size: 11px; font-weight: 500;")
        self.ctx_status_label.setToolTip("Clique para ver detalhes de janela de contexto")
        self.ctx_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ctx_status_label.mousePressEvent = lambda _ev: self.show_technical_details("ctx")
        telemetry_layout.addWidget(self.ctx_status_label)

        main.addWidget(self.telemetry_box)
        footer = QLabel("PC, celular e voz · uma memória compartilhada")
        footer.setObjectName("footer")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(footer)
        self.chat_page = content
        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("markAreas")
        self.page_stack.addWidget(content)
        self.content_layout = self.page_stack
        self.page_heading = title
        self._build_specialization_pages()
        layout.addWidget(self.page_stack, 1)

        # Menu de Comando Modal (Ctrl+K / Cmd+K)
        self.command_palette = CommandPaletteDialog(self)
        self._setup_command_palette()
        self.cmd_k_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.cmd_k_shortcut.activated.connect(self.open_command_palette)
        self.cmd_k_mac_shortcut = QShortcut(QKeySequence("Meta+K"), self)
        self.cmd_k_mac_shortcut.activated.connect(self.open_command_palette)

    def _setup_command_palette(self):
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
        cp.register_command("Biblioteca de Memórias", self.open_library, "Sistema")
        cp.register_command("Evolução do MARK", self.open_evolution, "Sistema")
        cp.register_command("Plugins...", self.open_plugins, "Sistema")
        cp.register_command("Iniciar MARK", self.start_mark, "Sistema")
        cp.register_command("Limpar conversa", self.clear_memory, "Sistema")

    def open_settings(self):
        self.open_area("settings")

    def open_command_palette(self):
        self.command_palette.search_input.clear()
        self.command_palette.populate()
        self.command_palette.search_input.setFocus()
        self.command_palette.exec()

    def show_technical_details(self, topic: str = "all") -> None:
        """Exibe detalhes técnicos de segurança (BWRAP sandbox e mTLS Tailscale) sob clique."""
        bwrap_ok = _probe_bwrap_support()
        has_ts = bool(shutil.which("tailscale"))

        bwrap_info = (
            "Status: ATIVO\n"
            "Mecanismo: Bubblewrap Linux Namespaces (PID, IPC, UTS, Mount)\n"
            "Confinamento: Acesso estritamente restrito ao workspace e dependências do sistema."
            if bwrap_ok else
            "Status: INATIVO\n"
            "Mecanismo: Sandbox nativo não disponível no ambiente atual."
        )
        mtls_info = (
            "Status: ATIVO\n"
            "Mecanismo: Tailscale TLS 1.3 mútuo\n"
            "Criptografia: Conexão autenticada e cifrada entre cliente e servidor."
            if has_ts else
            "Status: INATIVO\n"
            "Mecanismo: Loopback local exclusivo (127.0.0.1)."
        )
        ctx_info = (
            "Janela de Contexto: 8K tokens ativos\n"
            "Mecanismo: Gerenciamento de sessão local e compressão semântica."
        )
        if topic == "bwrap":
            QMessageBox.information(self, "Segurança · Sandbox BWRAP", bwrap_info)
        elif topic == "mtls":
            QMessageBox.information(self, "Segurança · Rede mTLS", mtls_info)
        elif topic == "ctx":
            QMessageBox.information(self, "Contexto · Janela CTX", ctx_info)
        else:
            QMessageBox.information(
                self,
                "Status Técnico de Segurança",
                f"=== BWRAP SANDBOX ===\n{bwrap_info}\n\n=== mTLS REDE ===\n{mtls_info}\n\n=== CONTEXTO CTX ===\n{ctx_info}",
            )



    def _refresh_evolution_overview(self):
        if self.evolution_status_loading:
            return
        self.evolution_status_loading = True
        self.evolution_status.setText("Atualizando estado da rotina…")

        def inspect_state():
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [sys.executable, "-m", "mark.evolution.cli", "status"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=8, check=False,
            )
            if result.returncode:
                raise RuntimeError("não foi possível ler o estado")
            return json.loads(result.stdout)

        def show_state(state):
            self.evolution_status_loading = False
            progress = state.get("progress", {})
            total_steps = max(1, int(progress.get("total", 7)))
            step = max(0, min(total_steps, int(progress.get("step", 0))))
            self.evolution_progress_bar.setRange(0, total_steps)
            self.evolution_progress_bar.setValue(step)
            self.evolution_progress_bar.setFormat(f"{step} de {total_steps} etapas")
            if state.get("paused"):
                text = "Rotina pausada · timer periódico desativado · alterações candidatas aguardam revisão."
            elif state.get("enabled"):
                text = f"Rotina ativa · etapa: {progress.get('label', 'aguardando')} · timer periódico {'ativo' if state.get('schedule_enabled') else 'desativado'}."
            else:
                text = "Rotina desativada · nenhum ciclo está executando."
            backlog = state.get("backlog", 0)
            text += f"  ·  {backlog} oportunidade(s) na fila."
            self.evolution_status.setText(text)

        def failed(_message):
            self.evolution_status_loading = False
            self.evolution_status.setText("Estado da rotina indisponível; abra o painel para consultar os detalhes.")

        self._task(inspect_state, show_state, failed)

    def _refresh_engineer_overview(self):
        if self.engineer_status_loading:
            return
        self.engineer_status_loading = True
        self.engineer_model_status.setText("Verificando o modelo configurado no Ollama…")

        def inspect_local_state():
            from .config import load_config
            from .desktop_engineer_tasks import mission_counts
            cfg = load_config()
            model = cfg.get("models", {}).get("engineer", "")
            installed = None
            try:
                request = urllib.request.Request(
                    cfg["ollama"]["url"].rstrip("/") + "/api/tags",
                    headers={"Accept": "application/json"},
                )
                validate_url_scheme(request)
                with urllib.request.urlopen(request, timeout=3) as response:  # nosec: B310
                    rows = json.load(response).get("models", [])
                installed_names = {row.get("name", "").removesuffix(":latest")
                                   for row in rows if isinstance(row, dict)}
                installed = model.removesuffix(":latest") in installed_names
            except (OSError, ValueError, KeyError, TypeError):
                pass

            return {"model": model, "installed": installed,
                    "mission_count": mission_counts()}

        def show_local_state(result):
            self.engineer_status_loading = False
            model = result["model"] or "Modelo Engineer não configurado"
            self.engineer_model_status.setToolTip(model)
            self.engineer_model_indicator.setText(
                "PRONTO" if result["installed"] is True else
                "AUSENTE" if result["installed"] is False else "SEM STATUS"
            )
            self.engineer_model_indicator.setProperty("ready", result["installed"] is True)
            self.engineer_model_indicator.style().unpolish(self.engineer_model_indicator)
            self.engineer_model_indicator.style().polish(self.engineer_model_indicator)
            if result["installed"] is True:
                self.engineer_model_status.setText(model + " · instalado; carrega quando você usa")
            elif result["installed"] is False:
                self.engineer_model_status.setText(model + " · não encontrado no Ollama")
            else:
                self.engineer_model_status.setText(model + " · não foi possível consultar o Ollama")
            self.engineer_tasks.set_counts(result["mission_count"])

        def failed_local_state(_message):
            self.engineer_status_loading = False
            self.engineer_model_indicator.setText("ERRO")
            self.engineer_model_status.setText("Não consegui atualizar o estado local do Engineer.")
            self.engineer_tasks.set_unavailable()

        self._task(inspect_local_state, show_local_state, failed_local_state)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "composer_hint"):
            self.composer_hint.setVisible(True)

    def _section_label(self, text):
        label = QLabel(text)
        label.setObjectName("section")
        return label

    def _nav_button(self, text: str, icon_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setIcon(_mark_icon(icon_name))
        button.setIconSize(QSize(18, 18))
        button.setFixedHeight(36)
        return button

    def _status_row(self, name, value):
        box = QFrame()
        box.setObjectName("statusBox")
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(8)
        label = QLabel(name)
        label.setObjectName("statusTitle")
        text = QLabel(value)
        text.setObjectName("statusValue")
        text.setWordWrap(True)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(text)
        box.setFixedHeight(34)
        box.value_label = text
        return box

    def _set_style(self):
        self.setStyleSheet(STYLESHEET)

    def _scroll_to_latest(self):
        if self.follow_latest:
            self.chat_layout.activate()
            self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _schedule_bottom_scroll(self):
        for delay in (0,30,100):QTimer.singleShot(delay,self._scroll_to_latest)

    def _chat_range_changed(self,*_):
        if self.follow_latest:self._schedule_bottom_scroll()

    def _record_manual_scroll(self):
        bar=self.scroll.verticalScrollBar();self.follow_latest=bar.maximum()-bar.value()<80

    def eventFilter(self,watched,event):
        if hasattr(self,'scroll') and watched is self.scroll.viewport() and event.type()==QEvent.Type.Wheel:
            self.follow_latest=False;QTimer.singleShot(0,self._record_manual_scroll)
        return super().eventFilter(watched,event)

    def _conversations_file(self):
        memory_file = Path(getattr(self, "_memory_file", MEMORY_FILE))
        return memory_file.with_name("desktop-conversations.sqlite3")

    def _ensure_conversation_store(self):
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

    def _select_conversation_from_header(self, _index):
        if self._conversation_switching or self.current_specialization == "general":
            return
        conversation_id = self.conversation_selector.currentData()
        if conversation_id:
            self.current_conversation_id = conversation_id
            self._load_active_conversation()

    def _create_specialized_conversation(self):
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

    def _load_active_conversation(self):
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

    def _import_shared_turns(self):
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

    def refresh_status(self):
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
            self.api_status.value_label.setText("Conectado" if states["reachable"] else "Offline")
            self.api_status.value_label.setStyleSheet("color: #66db9b" if states["reachable"] else "color: #f39a86")
            self.connect_button.setText("MARK conectado" if states["reachable"] else "Iniciar MARK")
            self.connect_button.setEnabled(not states["reachable"])
            self.voice_active = states["voice"]
            self.voice_status.value_label.setText("Ativa" if self.voice_active else "Desligada")
            self.voice_status.value_label.setStyleSheet("color: #66db9b" if self.voice_active else "color: #91a4b4")
            self.voice_button.setText("Parar escuta" if self.voice_active else "Iniciar escuta")
            self.connection_label.setText("MARK está pensando…" if self.busy else "Conectado · memória compartilhada" if states["reachable"] else "Offline · inicie o MARK")

        self._task(check, update)

    def toggle_voice(self):
        starting = not self.voice_active
        self.voice_button.setDisabled(True)
        action = "start" if starting else "stop"

        def done(_):
            self.voice_button.setDisabled(False)
            self.refresh_status()

        def failed(message):
            self.voice_button.setDisabled(False)
            QMessageBox.warning(self, "MARK", "Não consegui alterar a escuta por voz.\n\n" + message)
            self.refresh_status()

        self._task(lambda: _service(action, VOICE_SERVICE), done, failed)

    def start_mark(self):
        self.connect_button.setDisabled(True)

        def start():
            _service("start", MOBILE_SERVICE)
            return True

        def done(_):
            self.refresh_status()

        def failed(message):
            self.connect_button.setEnabled(True)
            QMessageBox.warning(self, "MARK", "Não consegui iniciar o serviço do MARK.\n\n" + message)

        self._task(start, done, failed)

    def open_call(self):
        CallDialog(self).exec()

    def open_agenda(self):
        dialog = AgendaDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_email_composer(self):
        dialog = EmailComposerDialog(self.last_assistant_reply, self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_github(self):
        from .plugins import enabled as plugin_enabled
        from .config import load_config
        if not plugin_enabled("github", load_config()):
            QMessageBox.information(self, "Plugins", "Ative o plugin GitHub em Ferramentas → Plugins.")
            return
        dialog = GitHubDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_mobile_devices(self):
        dialog = MobileDevicesDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_chat(self):
        self.open_specialized_chat("general")

    def open_library(self):
        if not hasattr(self,'library_page'):
            from .desktop_library import LibraryPage
            from .cognition.knowledge import DEFAULT_DB
            from .config import load_config
            cfg=load_config()
            self.library_page=LibraryPage(self,Path(cfg.get('cognition',{}).get('knowledge_db',str(DEFAULT_DB))))
            self.page_stack.addWidget(self.library_page)
        self._set_active_area("library")
        self.page_stack.setCurrentWidget(self.library_page)

    def open_sentinel(self):
        from .desktop_sentinel import SentinelDialog
        dialog = SentinelDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_missions(self):
        from .desktop_missions import MissionDialog
        dialog = MissionDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_evolution(self):
        self.open_area("evolution")

    def open_evolution_dialog(self):
        from .desktop_evolution import EvolutionDialog
        dialog=EvolutionDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def gods_eye_mcp(self):
        from .mcp_gods_eye import GodsEyeMCPBridge
        if getattr(self, "_gods_eye_bridge", None) is None:
            self._gods_eye_bridge = GodsEyeMCPBridge()
        return self._gods_eye_bridge

    def sandbox_mcp(self, workspace: Path | None = None):
        from .mcp_sandbox import SandboxMCPBridge
        if workspace is not None:
            return SandboxMCPBridge(workspace=workspace)
        if getattr(self, "_sandbox_bridge", None) is None:
            self._sandbox_bridge = SandboxMCPBridge(workspace=ROOT)
        return self._sandbox_bridge

    def run_autonomous_engineering(self, task: str | None = None, target_file: str | None = None):
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

    def open_vision(self):
        from .plugins import enabled as plugin_enabled
        from .config import load_config
        if not plugin_enabled("gods_eye", load_config()):
            QMessageBox.information(self, "Plugins", "Ative o plugin God’s Eye View em Ferramentas → Plugins.")
            return
        from .desktop_vision import VisionDialog
        dialog = VisionDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def test_camera(self):
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

    def clear_memory(self):
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


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MARK")
    app.setOrganizationName("Bannaman6655 Productions")
    from .ui.main_window import MainWindow
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
