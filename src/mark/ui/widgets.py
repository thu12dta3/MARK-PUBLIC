import json
import logging
import threading
from typing import Any

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..desktop_theme import message_html
from ..memory import source_label
from ..config.llm_config import LLMConfig, get_llm_config
from .theme_manager import ThemeManager

logger = logging.getLogger("mark.ui.widgets")



class MessageBubbleWidget(QFrame):
    """Widget de mensagem do MARK e do Usuario no estilo Minimalismo Tecnico Radical.
    
    As mensagens flutuam na grade, separadas exclusivamente por espacamento negativo,
    sem bolhas delimitadas, sem bordas visiveis e sem raio de curvatura.
    """

    def __init__(
        self,
        text: str,
        role: str = "assistant",
        source: str = "",
        link_handler: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self.text = text
        self.source = source
        self._link_handler = link_handler
        self._build_ui()

    def _build_ui(self) -> None:
        is_user = self.role == "user"
        self.setObjectName("userBubble" if is_user else "assistantBubble")
        self.setMaximumWidth(840)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred if is_user else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        # Soft Minimal Tech: sem bordas duras, usuario com fundo sutil #2D333B e raio 8px, MARK sobre fundo #1A1D21
        if is_user:
            self.setStyleSheet(
                "background: #2D333B; border: none; border-radius: 8px; padding: 10px 14px; margin: 4px 0px 4px 48px;"
            )
        else:
            self.setStyleSheet(
                "background: #1A1D21; border: none; border-radius: 0px; padding: 10px 0px; margin: 4px 48px 4px 0px;"
            )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        # Identificacao Discreta: Remetente/hora em texto secundario fino sans-serif apenas para o usuario
        if is_user:
            orig = source_label(self.source)
            sender_title = "Você" if not orig else f"Você · {orig}"
            self.who = QLabel(sender_title)
            self.who.setObjectName("messageSender")
            self.who.setStyleSheet(
                "color: #8B949E; font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; "
                "font-size: 11px; font-weight: 500; border: none; background: transparent; padding: 0px; margin: 0px;"
            )
            self.who.setAlignment(Qt.AlignmentFlag.AlignRight)
            layout.addWidget(self.who)
        else:
            self.who = None

        # Widget interno de mensagem: sem bordas, margens ou fundos, tipografia sans-serif suave e confortavel
        self.label = QLabel(message_html(self.text))
        self.label.setObjectName("messageBody")
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(
            "color: #EAEAEA; font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; "
            "font-size: 13px; font-weight: 400; border: none; background: transparent; "
            "selection-background-color: #58A6FF; selection-color: #1A1D21; margin: 0px; padding: 0px;"
        )
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.label.setOpenExternalLinks(False)
        if self._link_handler:
            self.label.linkActivated.connect(self._link_handler)

        if is_user:
            self.label.setAlignment(Qt.AlignmentFlag.AlignRight)
            first_line = self.text.splitlines()[0] if self.text else ""
            natural = self.label.fontMetrics().horizontalAdvance(first_line[:72]) + 42
            self.setMinimumWidth(min(560, max(200, natural)))
        else:
            self.label.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

            def context_menu(position, value=self.text, target=self.label):
                menu = QMenu(target)
                menu.setStyleSheet(
                    "background: #22262B; color: #EAEAEA; font-family: Inter, sans-serif; "
                    "border: 1px solid #30363D; border-radius: 8px; padding: 4px;"
                )
                menu.addAction("Copiar resposta", lambda: QApplication.clipboard().setText(value))
                menu.exec(target.mapToGlobal(position))

            self.label.customContextMenuRequested.connect(context_menu)

        layout.addWidget(self.label)


class ErrorCardWidget(QFrame):
    """Card de erro amigável, acolhedor e acionável para o fluxo de conversa.
    
    Apresenta explicação simplificada da falha e ações diretas de recuperação:
    - Botão [Tentar Novamente]
    - Botão [Alternar Modelo/Provedor]
    """

    retry_requested = pyqtSignal()
    switch_model_requested = pyqtSignal()

    def __init__(
        self,
        error_text: str,
        title: str = "Falha no Processamento",
        retry_callback: Any = None,
        switch_model_callback: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("errorCard")
        self.error_text = error_text
        self.title = title
        if retry_callback is not None:
            self.retry_requested.connect(retry_callback)
        if switch_model_callback is not None:
            self.switch_model_requested.connect(switch_model_callback)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setMaximumWidth(780)
        self.setStyleSheet("""
            QFrame#errorCard {
                background: #231C20;
                border: 1px solid #5C2D35;
                border-radius: 8px;
                padding: 12px 16px;
                margin: 4px 48px 4px 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header: Ícone e Título
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.icon_label = QLabel("⚠️")
        self.icon_label.setObjectName("errorIcon")
        self.icon_label.setStyleSheet("font-size: 14px; border: none; background: transparent;")

        self.title_label = QLabel(self.title)
        self.title_label.setObjectName("errorTitle")
        self.title_label.setStyleSheet(
            "color: #FF7B72; font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif; "
            "font-size: 13px; font-weight: 600; border: none; background: transparent;"
        )

        header_layout.addWidget(self.icon_label)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        # Corpo: Explicação clara e simplificada
        self.body_label = QLabel(self.error_text)
        self.body_label.setObjectName("errorBody")
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet(
            "color: #C9D1D9; font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif; "
            "font-size: 12px; font-weight: 400; line-height: 1.4; border: none; background: transparent;"
        )
        layout.addWidget(self.body_label)

        # Ações diretas de recuperação
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 4, 0, 0)
        actions_layout.setSpacing(8)

        self.retry_button = QPushButton("Tentar Novamente")
        self.retry_button.setObjectName("retryButton")
        self.retry_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_button.setStyleSheet("""
            QPushButton#retryButton {
                background: #2D333B;
                color: #58A6FF;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 14px;
                font-family: Inter, sans-serif;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton#retryButton:hover {
                background: #373E47;
                border-color: #58A6FF;
            }
            QPushButton#retryButton:pressed {
                background: #22262B;
            }
        """)
        self.retry_button.clicked.connect(self.retry_requested.emit)
        actions_layout.addWidget(self.retry_button)

        self.switch_model_button = QPushButton("Alternar Modelo/Provedor")
        self.switch_model_button.setObjectName("switchModelButton")
        self.switch_model_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.switch_model_button.setStyleSheet("""
            QPushButton#switchModelButton {
                background: #2D333B;
                color: #8B949E;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 14px;
                font-family: Inter, sans-serif;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton#switchModelButton:hover {
                background: #373E47;
                color: #C9D1D9;
                border-color: #8B949E;
            }
            QPushButton#switchModelButton:pressed {
                background: #22262B;
            }
        """)
        self.switch_model_button.clicked.connect(self.switch_model_requested.emit)
        actions_layout.addWidget(self.switch_model_button)

        actions_layout.addStretch(1)
        layout.addLayout(actions_layout)


class ThinkingIndicatorWidget(QFrame):
    """Widget de feedback visual para estado de pensamento ("MARK está pensando...").
    
    Apresenta estética Soft Minimal Tech com indicador de pulsação discreto
    até que o streaming de resposta comece.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("thinkingIndicator")
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._animate_dots)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("""
            QFrame#thinkingIndicator {
                background: #1A1D21;
                border: none;
                border-radius: 0px;
                padding: 8px 0px;
                margin: 4px 48px 4px 0px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.dot_label = QLabel("●")
        self.dot_label.setObjectName("thinkingDot")
        self.dot_label.setStyleSheet("color: #58A6FF; font-size: 11px; border: none; background: transparent;")

        self.text_label = QLabel("MARK está pensando...")
        self.text_label.setObjectName("thinkingText")
        self.text_label.setStyleSheet(
            "color: #8B949E; font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; "
            "font-size: 13px; font-weight: 400; border: none; background: transparent;"
        )

        layout.addWidget(self.dot_label)
        layout.addWidget(self.text_label)
        layout.addStretch(1)

    def _animate_dots(self) -> None:
        self._step = (self._step + 1) % 4
        dots = "." * (self._step + 1)
        self.text_label.setText(f"MARK está pensando{dots}")

    def start(self) -> None:
        self.show()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
        self.hide()

    def is_thinking(self) -> bool:
        return self._timer.isActive() and self.isVisible()


class ChatWidget(QWidget):
    """Widget container de mensagens com visual suave, acolhedor e espacamento fluido."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatContent")
        self._thinking_widget: ThinkingIndicatorWidget | None = None
        self.chat_layout = QVBoxLayout(self)
        self.chat_layout.setContentsMargins(16, 20, 16, 20)
        self.chat_layout.setSpacing(20)
        self.setStyleSheet("""
            QWidget { background: #1A1D21; border: none; }
            QLabel, QTextEdit, QTextBrowser {
                border: none;
                background: transparent;
                selection-background-color: #58A6FF;
                selection-color: #1A1D21;
            }
        """)

    def add_bubble(
        self,
        text: str,
        role: str = "assistant",
        source: str = "",
        link_handler: Any = None,
    ) -> MessageBubbleWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        bubble = MessageBubbleWidget(
            text=text,
            role=role,
            source=source,
            link_handler=link_handler,
        )
        if role == "user":
            row.addStretch(1)
        row.addWidget(bubble, 1 if role == "assistant" else 0)
        if role == "assistant":
            row.addStretch(1)
        wrapper = QWidget()
        wrapper.setLayout(row)
        self.chat_layout.addWidget(wrapper)
        return bubble

    def show_thinking(self) -> ThinkingIndicatorWidget:
        if self._thinking_widget is None:
            self._thinking_widget = ThinkingIndicatorWidget(self)
            self.chat_layout.addWidget(self._thinking_widget)
        self._thinking_widget.start()
        return self._thinking_widget

    def hide_thinking(self) -> None:
        if self._thinking_widget is not None:
            self._thinking_widget.stop()
            self.chat_layout.removeWidget(self._thinking_widget)
            self._thinking_widget.deleteLater()
            self._thinking_widget = None



class ModelDiscoveryWorker(QThread):
    """Worker assíncrono para verificar modelos locais do Ollama e chaves de nuvem sem travar a interface."""

    discovery_finished = pyqtSignal(list, dict)

    def __init__(self, config: LLMConfig | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config

    def run(self) -> None:
        try:
            from ..cognition.provider_adapter import OllamaProviderAdapter

            installed_ollama = OllamaProviderAdapter.get_installed_models(timeout=1.5)
        except Exception as exc:
            logger.debug("Falha na descoberta de modelos Ollama: %s", exc)
            installed_ollama = []

        try:
            cfg = self.config or get_llm_config()
            available_cloud = cfg.get_available_providers()
        except Exception as exc:
            logger.debug("Falha na descoberta de provedores em nuvem: %s", exc)
            available_cloud = {}

        self.discovery_finished.emit(installed_ollama, available_cloud)


class ProviderSelectorWidget(QComboBox):
    """Seletor de modelos e provedores de IA com deteccao dinamica em tempo real."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modelSelector")
        self._badge_widget: QLabel | None = None
        self._models_meta: list[dict[str, Any]] = []
        self._config: LLMConfig | None = None
        self._worker: ModelDiscoveryWorker | None = None
        self.currentIndexChanged.connect(self._on_index_changed)

    def set_badge_widget(self, badge: QLabel) -> None:
        self._badge_widget = badge
        self._update_badge()

    def start_async_discovery(self) -> None:
        """Inicia deteccao em segundo plano para nao congelar a UI."""
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = ModelDiscoveryWorker(self._config, parent=self)
        self._worker.discovery_finished.connect(self._on_discovery_finished)
        self._worker.start()

    def _on_discovery_finished(self, installed_ollama: list[str], available_cloud: dict[str, Any]) -> None:
        self._apply_discovered_models(installed_ollama, available_cloud)

    def showPopup(self) -> None:
        self.start_async_discovery()
        super().showPopup()

    def focusInEvent(self, event: Any) -> None:
        self.start_async_discovery()
        super().focusInEvent(event)

    def populate_from_config(
        self,
        config: LLMConfig | None = None,
        installed_models: list[str] | None = None,
        available_providers: dict[str, Any] | None = None,
        async_discovery: bool = False,
    ) -> None:
        """Popula o seletor com modelos reais instalados no Ollama e provedores em nuvem com chaves validas."""
        self._config = config or get_llm_config()

        if installed_models is not None or available_providers is not None:
            self._apply_discovered_models(
                installed_models if installed_models is not None else [],
                available_providers if available_providers is not None else {},
            )
            return

        if async_discovery:
            self.start_async_discovery()
            return

        # Sincrono (para testes headless e renderizacao imediata)
        try:
            from ..cognition.provider_adapter import OllamaProviderAdapter

            ollama_models = OllamaProviderAdapter.get_installed_models(timeout=1.0)
        except Exception:
            ollama_models = []

        try:
            cloud_provs = self._config.get_available_providers()
        except Exception:
            cloud_provs = {}

        self._apply_discovered_models(ollama_models, cloud_provs)

    def _format_local_model_name(self, tag: str) -> str:
        """Formata o nome de exibicao amigavel de um modelo Ollama baixado."""
        lower = tag.lower()
        clean = tag.split("/")[-1]
        if "qwen2.5-coder-32b" in lower:
            return "Qwen 2.5 Coder 32B (Ollama Local)"
        if "qwen2.5-coder-7b" in lower or ("qwen2.5-coder" in lower and "7b" in lower):
            return "Qwen 2.5 Coder 7B (Ollama Local)"
        if "qwen2.5-coder" in lower:
            return f"Qwen 2.5 Coder ({clean}) (Ollama Local)"
        if "qwen3-vl" in lower:
            return f"Qwen3 VL ({clean}) (Ollama Local)"
        if "llama-3.3" in lower:
            return "Llama 3.3 (Ollama Local)"
        if "llama3" in lower:
            return f"Llama 3 ({clean}) (Ollama Local)"
        if "deepseek" in lower:
            return f"DeepSeek ({clean}) (Ollama Local)"
        return f"{tag} (Ollama Local)"

    def _format_cloud_model_name(self, provider: str, model_id: str) -> str:
        """Formata o nome exato do modelo de nuvem configurado."""
        mid_lower = model_id.lower()
        if "claude-3-5-sonnet" in mid_lower or "claude-3.5-sonnet" in mid_lower:
            return "Claude 3.5 Sonnet"
        if "gpt-4o" in mid_lower:
            return "GPT-4o"
        if "gemini-2.0-flash" in mid_lower or "gemini-2" in mid_lower:
            return "Gemini 2.0 Flash"
        if "deepseek" in mid_lower:
            return "DeepSeek V3"
        if "llama-3.3" in mid_lower:
            return f"Llama 3.3 ({provider.capitalize()})"
        if "mistral-large" in mid_lower:
            return "Mistral Large"
        if "openrouter" in mid_lower or model_id == "openrouter/auto":
            return "OpenRouter Auto"
        return f"{model_id} ({provider.capitalize()})"

    def _apply_discovered_models(
        self, installed_ollama: list[str], available_cloud: dict[str, Any]
    ) -> None:
        self.blockSignals(True)
        prev_data = self.currentData()
        prev_text = self.currentText()
        self.clear()
        self._models_meta = []

        valid_items_count = 0

        # 1. Modelos Locais (Ollama)
        if installed_ollama:
            for idx, tag in enumerate(installed_ollama):
                display_name = self._format_local_model_name(tag)
                # O primeiro modelo local recebe data='mark' para compatibilidade das rotas locais
                data_val = "mark" if idx == 0 else f"ollama:{tag}"
                self.addItem(display_name, data_val)
                item_meta = {
                    "id": tag,
                    "name": display_name,
                    "provider": "ollama",
                    "type": "Local Ollama",
                    "data": data_val,
                    "enabled": True,
                }
                self._models_meta.append(item_meta)
                item_idx = self.count() - 1
                tooltip = f"Modelo: {tag} · Processamento: Local Ollama (Ollama)"
                self.setItemData(item_idx, tooltip, Qt.ItemDataRole.ToolTipRole)
                valid_items_count += 1
        else:
            self.addItem("Nenhum modelo Ollama instalado", None)
            item_idx = self.count() - 1
            std_item = self.model().item(item_idx)
            if std_item is not None:
                std_item.setEnabled(False)
            self._models_meta.append({
                "id": "none_ollama",
                "name": "Nenhum modelo Ollama instalado",
                "provider": "ollama",
                "type": "Local Ollama",
                "data": None,
                "enabled": False,
            })

        # 2. Modelos em Nuvem com chaves validas
        for prov_name, profile in available_cloud.items():
            model_id = getattr(profile, "model", "") or prov_name
            display_name = self._format_cloud_model_name(prov_name, model_id)
            data_val = f"{prov_name}:{model_id}"
            self.addItem(display_name, data_val)
            item_meta = {
                "id": model_id,
                "name": display_name,
                "provider": prov_name,
                "type": "Nuvem",
                "data": data_val,
                "enabled": True,
            }
            self._models_meta.append(item_meta)
            item_idx = self.count() - 1
            tooltip = f"Modelo: {display_name} · Processamento: Nuvem ({prov_name.capitalize()})"
            self.setItemData(item_idx, tooltip, Qt.ItemDataRole.ToolTipRole)
            valid_items_count += 1

        # 3. Se nenhum provedor/modelo estiver ativo/configurado:
        if valid_items_count == 0:
            self.clear()
            self._models_meta = []
            self.addItem("Nenhum provedor ativo - Configure as Chaves", None)
            item_idx = self.count() - 1
            std_item = self.model().item(item_idx)
            if std_item is not None:
                std_item.setEnabled(False)
            self._models_meta.append({
                "id": "none_active",
                "name": "Nenhum provedor ativo - Configure as Chaves",
                "provider": "none",
                "type": "Indisponível",
                "data": None,
                "enabled": False,
            })

        # Restaura selecao anterior se ainda valida, ou seleciona o primeiro habilitado
        restored = False
        if prev_data is not None:
            for i, meta in enumerate(self._models_meta):
                if meta.get("enabled", True) and (meta.get("data") == prev_data or meta.get("name") == prev_text):
                    self.setCurrentIndex(i)
                    restored = True
                    break

        if not restored:
            for i, meta in enumerate(self._models_meta):
                if meta.get("enabled", True) and meta.get("data") is not None:
                    self.setCurrentIndex(i)
                    restored = True
                    break

        if not restored:
            self.setCurrentIndex(0)

        self.blockSignals(False)
        self._update_badge()

    def _on_index_changed(self, index: int) -> None:
        self._update_badge()

    def _update_badge(self) -> None:
        idx = self.currentIndex()
        if 0 <= idx < len(self._models_meta):
            meta = self._models_meta[idx]
            ptype = meta.get("type", "Nuvem")
            mname = meta.get("name", self.currentText())
            prov = meta.get("provider", "")
            enabled = meta.get("enabled", True)

            if not enabled or meta.get("data") is None:
                self.setToolTip("Nenhum provedor ou modelo ativo.")
                if self._badge_widget is not None:
                    self._badge_widget.setText("Indisponível")
                    self._badge_widget.setToolTip("Configure modelos no Ollama ou chaves de API no cofre.")
                    self._badge_widget.setProperty("processing", "disabled")
                    style = self._badge_widget.style()
                    if style:
                        style.unpolish(self._badge_widget)
                        style.polish(self._badge_widget)
                return

            self.setToolTip(f"{mname} · Processamento: {ptype}")
            if self._badge_widget is not None:
                self._badge_widget.setText(ptype)
                self._badge_widget.setToolTip(f"Processamento {ptype} via provedor {prov.capitalize()}")
                self._badge_widget.setProperty("processing", "local" if ptype == "Local Ollama" else "cloud")
                style = self._badge_widget.style()
                if style:
                    style.unpolish(self._badge_widget)
                    style.polish(self._badge_widget)

    def current_model(self) -> str:
        idx = self.currentIndex()
        if 0 <= idx < len(self._models_meta):
            return self._models_meta[idx]["name"]
        return self.currentText()

    def current_provider(self) -> str:
        idx = self.currentIndex()
        if 0 <= idx < len(self._models_meta):
            return str(self._models_meta[idx].get("provider", "ollama"))
        return "ollama"

    def current_model_id(self) -> str:
        idx = self.currentIndex()
        if 0 <= idx < len(self._models_meta):
            return str(self._models_meta[idx].get("id", ""))
        return ""

    def processing_type(self) -> str:
        idx = self.currentIndex()
        if 0 <= idx < len(self._models_meta):
            return self._models_meta[idx]["type"]
        return "Local Ollama" if "Local" in self.currentText() else "Nuvem"

    def select_model_by_name(self, name: str) -> bool:
        name_lower = name.lower()
        for i in range(self.count()):
            meta = self._models_meta[i] if i < len(self._models_meta) else {}
            if not meta.get("enabled", True):
                continue
            if name_lower in self.itemText(i).lower() or name_lower in meta.get("id", "").lower():
                self.setCurrentIndex(i)
                return True
        return False


class ActionConsentDialog(QDialog):
    """Modal de confirmação Human-in-the-Loop para operações mutantes/sensíveis de ferramentas."""

    def __init__(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tool_name = tool_name
        self.arguments = arguments or {}
        self.granted: bool = False
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("Confirmação de Ação · Human-in-the-Loop")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet("""
            QDialog {
                background: #1A1D21;
                color: #EAEAEA;
                font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                border: 1px solid #30363D;
                border-radius: 8px;
            }
            QLabel#dialogTitle {
                color: #EAEAEA;
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#dialogPrompt {
                color: #8B949E;
                font-size: 13px;
            }
            QLabel#toolHighlight {
                color: #58A6FF;
                font-family: monospace;
                font-weight: 600;
            }
            QTextEdit#argsPreview {
                background: #0D1117;
                color: #C9D1D9;
                border: 1px solid #30363D;
                border-radius: 6px;
                font-family: monospace;
                font-size: 11px;
                padding: 8px;
            }
            QPushButton#authorizeBtn {
                background: #238636;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#authorizeBtn:hover {
                background: #2EA043;
            }
            QPushButton#cancelBtn {
                background: #21262D;
                color: #8B949E;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#cancelBtn:hover {
                background: #30363D;
                color: #EAEAEA;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Permissão de Execução Agentizada")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        prompt = QLabel(f"Permitir execução da ferramenta <b>{self.tool_name}</b>?")
        prompt.setObjectName("dialogPrompt")
        prompt.setTextFormat(Qt.TextFormat.RichText)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        desc = QLabel(
            "Esta operação pode alterar arquivos externos, realizar commits ou disparar chamadas externas que exigem consentimento."
        )
        desc.setObjectName("dialogPrompt")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        if self.arguments:
            layout.addWidget(QLabel("Parâmetros solicitados:"))
            args_box = QTextEdit()
            args_box.setObjectName("argsPreview")
            args_box.setReadOnly(True)
            args_box.setPlainText(json.dumps(self.arguments, indent=2, ensure_ascii=False))
            args_box.setMaximumHeight(120)
            layout.addWidget(args_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)

        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel_btn)

        auth_btn = QPushButton("Autorizar")
        auth_btn.setObjectName("authorizeBtn")
        auth_btn.clicked.connect(self._on_authorize)
        btn_row.addWidget(auth_btn)

        layout.addLayout(btn_row)

    def _on_authorize(self) -> None:
        self.granted = True
        self.accept()

    def _on_cancel(self) -> None:
        self.granted = False
        self.reject()


class ConsentBridge(QObject):
    """Ponte thread-safe entre tarefas em background (QThread) e a UI principal para obter consentimento."""

    request_consent = pyqtSignal(str, dict, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.parent_widget = parent
        self.request_consent.connect(self._handle_request)

    def _handle_request(self, tool_name: str, arguments: dict[str, Any], event: Any) -> None:
        dialog = ActionConsentDialog(tool_name, arguments, parent=self.parent_widget)
        dialog.exec()
        event.is_approved = dialog.granted
        event.set()

    def ask_consent(self, tool_name: str, arguments: dict[str, Any], timeout: float = 60.0) -> bool:
        event = threading.Event()
        event.is_approved = False
        self.request_consent.emit(tool_name, arguments, event)
        signaled = event.wait(timeout=timeout)
        if not signaled:
            return False
        return bool(event.is_approved)


class GodsEyeStatusWidget(QFrame):
    """Painel de visibilidade em tempo real para o ciclo de execução agentizada (God's Eye View).

    Reflete as fases:
    - Ferramenta Solicitada ("Aguardando Ferramenta")
    - Ação Executada ("Lendo Repositório" / execução em andamento)
    - Sucesso Confirmado ("Analisando Resultado" / observação anexada com sucesso)
    """

    cancel_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("godsEyeStatusWidget")
        self._current_phase = "IDLE"
        self._build_ui()
        self.hide()

    def _build_ui(self) -> None:
        self.setStyleSheet("""
            QFrame#godsEyeStatusWidget {
                background: #161B22;
                border: 1px solid #30363D;
                border-radius: 8px;
                padding: 10px 14px;
                margin: 4px 0px;
            }
            QLabel#godsEyeTitle {
                color: #58A6FF;
                font-family: Inter, sans-serif;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }
            QLabel#phaseBadge {
                color: #1A1D21;
                font-size: 11px;
                font-weight: 600;
                padding: 2px 8px;
                border-radius: 4px;
            }
            QLabel#phaseBadge[phase="requested"] {
                background: #D29922;
                color: #1A1D21;
            }
            QLabel#phaseBadge[phase="executing"] {
                background: #58A6FF;
                color: #1A1D21;
            }
            QLabel#phaseBadge[phase="confirmed"] {
                background: #2EA043;
                color: #FFFFFF;
            }
            QLabel#godsEyeAction {
                color: #EAEAEA;
                font-size: 13px;
                font-weight: 500;
            }
            QLabel#godsEyeStep {
                color: #8B949E;
                font-size: 11px;
            }
            QPushButton#godsEyeCancelBtn {
                background: #21262D;
                color: #F85149;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton#godsEyeCancelBtn:hover {
                background: #F85149;
                color: #FFFFFF;
                border-color: #F85149;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        col_info = QVBoxLayout()
        col_info.setSpacing(2)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self.title_label = QLabel("GOD'S EYE VIEW · EXECUÇÃO AGENTIZADA")
        self.title_label.setObjectName("godsEyeTitle")
        header_row.addWidget(self.title_label)

        self.phase_badge = QLabel("Ferramenta Solicitada")
        self.phase_badge.setObjectName("phaseBadge")
        self.phase_badge.setProperty("phase", "requested")
        header_row.addWidget(self.phase_badge)

        self.step_label = QLabel("Passo 1/3")
        self.step_label.setObjectName("godsEyeStep")
        header_row.addWidget(self.step_label)

        header_row.addStretch(1)
        col_info.addLayout(header_row)

        self.action_label = QLabel("Aguardando Ferramenta")
        self.action_label.setObjectName("godsEyeAction")
        col_info.addWidget(self.action_label)

        layout.addLayout(col_info, 1)

        self.cancel_button = QPushButton("Cancelar Tarefa")
        self.cancel_button.setObjectName("godsEyeCancelBtn")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(self.cancel_button)

    def _on_cancel_clicked(self) -> None:
        self.action_label.setText("Cancelando tarefa...")
        self.cancel_button.setEnabled(False)
        self.cancel_requested.emit()

    def update_state(self, phase_category: str, tool_name: str, action_detail: str, step_info: str = "") -> None:
        """Atualiza o painel com as três fases canônicas:

        - 'Ferramenta Solicitada' -> 'Aguardando Ferramenta' (ou detalhe da solicitação)
        - 'Ação Executada' -> 'Lendo Repositório' / execução em andamento
        - 'Sucesso Confirmado' -> 'Analisando Resultado' / resultado obtido
        """
        self.show()
        self.cancel_button.setEnabled(True)

        cat_lower = phase_category.lower()
        if "solicitada" in cat_lower or "request" in cat_lower or "waiting" in cat_lower:
            self.phase_badge.setText("Ferramenta Solicitada")
            self.phase_badge.setProperty("phase", "requested")
            display_action = action_detail or "Aguardando Ferramenta"
        elif "executada" in cat_lower or "executando" in cat_lower or "execut" in cat_lower:
            self.phase_badge.setText("Ação Executada")
            self.phase_badge.setProperty("phase", "executing")
            display_action = action_detail or (f"Executando {tool_name}…" if tool_name else "Lendo Repositório")
        elif "confirmado" in cat_lower or "sucesso" in cat_lower or "confirm" in cat_lower:
            self.phase_badge.setText("Sucesso Confirmado")
            self.phase_badge.setProperty("phase", "confirmed")
            display_action = action_detail or "Analisando Resultado"
        else:
            self.phase_badge.setText(phase_category)
            self.phase_badge.setProperty("phase", "requested")
            display_action = action_detail

        style = self.phase_badge.style()
        if style:
            style.unpolish(self.phase_badge)
            style.polish(self.phase_badge)

        self.action_label.setText(display_action)
        if step_info:
            self.step_label.setText(step_info)
            self.step_label.show()
        else:
            self.step_label.hide()

    def reset(self) -> None:
        self.hide()
        self.action_label.setText("")
        self.step_label.setText("")
        self.cancel_button.setEnabled(True)


class CitationViewerDialog(QDialog):
    """Diálogo modal para visualização do trecho original e metadados de uma citação."""

    def __init__(
        self,
        doc_id: str,
        excerpt: str,
        title: str = "",
        page: int | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Citação da Biblioteca — [{doc_id}]")
        self.setMinimumSize(540, 360)
        self.setStyleSheet("""
            QDialog {
                background-color: #1A1D21;
                color: #EAEAEA;
                font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
            QLabel {
                color: #EAEAEA;
            }
            QPushButton {
                background: #2D333B;
                color: #EAEAEA;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #373E47;
                border-color: #58A6FF;
            }
            QTextEdit {
                background: #101214;
                color: #EAEAEA;
                border: 1px solid #30363D;
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                line-height: 1.5;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Cabeçalho
        header_layout = QHBoxLayout()
        header_title = QLabel(f"Documento: {title or doc_id}")
        header_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #58A6FF;")
        header_layout.addWidget(header_title)
        header_layout.addStretch()

        badge = QLabel("✓ Ancoragem Verificada")
        badge.setStyleSheet("""
            background: rgba(35, 134, 54, 0.2);
            color: #3FB950;
            border: 1px solid rgba(35, 134, 54, 0.4);
            border-radius: 4px;
            padding: 3px 8px;
            font-size: 11px;
            font-weight: 600;
        """)
        header_layout.addWidget(badge)
        layout.addLayout(header_layout)

        # Metadados
        meta_parts = [f"DocID: <code style='color:#58A6FF;'>{doc_id}</code>"]
        if page:
            meta_parts.append(f"Página: <b>{page}</b>")
        meta_label = QLabel(" · ".join(meta_parts))
        meta_label.setStyleSheet("color: #8B949E; font-size: 12px;")
        layout.addWidget(meta_label)

        # Trecho original
        section_label = QLabel("Trecho Original Ancorado:")
        section_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #8B949E; margin-top: 4px;")
        layout.addWidget(section_label)

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setPlainText(excerpt)
        layout.addWidget(self.text_area, 1)

        # Botão fechar
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Fechar")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)



