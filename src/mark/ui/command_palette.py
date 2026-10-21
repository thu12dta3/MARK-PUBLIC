"""Command Palette modal (Ctrl+K) exibindo ferramentas e acoes do MARK de forma minimalista."""

from typing import Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .theme_manager import ThemeManager


class CommandPaletteDialog(QDialog):
    """Menu de Comando modal (Ctrl+K) com pesquisa em tempo real e navegacao por teclado."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("commandPaletteDialog")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMaximumWidth(700)
        self.setMinimumHeight(380)

        self._actions_map: dict[str, Callable[[], None]] = {}
        self._items_meta: list[dict[str, Any]] = []

        self._build_ui()
        self._apply_theme()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Header com indicador de atalho
        header = QHBoxLayout()
        title = QLabel("MENU DE COMANDO")
        title.setStyleSheet(
            f"color: {ThemeManager.TOKENS.ACCENT_CYAN}; font-family: {ThemeManager.TOKENS.FONT_FAMILY}; "
            "font-size: 11px; font-weight: 600; letter-spacing: 0.8px;"
        )
        hint = QLabel("[Esc] Fechar · [Enter] Executar")
        hint.setStyleSheet(
            f"color: {ThemeManager.TOKENS.TEXT_SECONDARY}; font-family: {ThemeManager.TOKENS.FONT_FAMILY}; "
            "font-size: 11px; font-weight: 400;"
        )
        header.addWidget(title)
        header.addStretch()
        header.addWidget(hint)
        layout.addLayout(header)

        # Campo de Busca
        self.search_input = QLineEdit()
        self.search_input.setObjectName("commandPaletteSearch")
        self.search_input.setPlaceholderText("Digite um comando ou ferramenta…")
        self.search_input.textChanged.connect(self._filter_items)
        layout.addWidget(self.search_input)

        # Lista de Comandos
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("commandPaletteList")
        self.list_widget.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self.list_widget, 1)

    def _apply_theme(self) -> None:
        t = ThemeManager.TOKENS
        self.setStyleSheet(
            f"QDialog#commandPaletteDialog {{"
            f"  background: {t.BG_PANEL};"
            f"  border: 1px solid {t.BORDER_SUBTLE};"
            f"  border-radius: 12px;"
            f"}}"
            f"#commandPaletteSearch {{"
            f"  background: {t.BG_CANVAS};"
            f"  color: {t.TEXT_PRIMARY};"
            f"  font-family: {t.FONT_FAMILY};"
            f"  font-size: 13px;"
            f"  border: 1px solid {t.BORDER_SUBTLE};"
            f"  border-radius: 8px;"
            f"  padding: 8px 12px;"
            f"}}"
            f"#commandPaletteSearch:focus {{"
            f"  border-color: {t.ACCENT_CYAN};"
            f"}}"
            f"#commandPaletteList {{"
            f"  background: {t.BG_PANEL};"
            f"  border: none;"
            f"  color: {t.TEXT_PRIMARY};"
            f"  font-family: {t.FONT_FAMILY};"
            f"  font-size: 12px;"
            f"  selection-background-color: {t.BG_FOCUS};"
            f"  selection-color: {t.ACCENT_CYAN};"
            f"}}"
            f"#commandPaletteList::item {{"
            f"  padding: 8px 12px;"
            f"  border-radius: 6px;"
            f"}}"
        )

    def register_command(self, title: str, callback: Callable[[], None], category: str = "") -> None:
        """Registra uma acao executavel no menu de comandos."""
        self._actions_map[title] = callback
        self._items_meta.append({"title": title, "category": category, "is_header": False})

    def add_category_header(self, header: str) -> None:
        """Adiciona um cabecalho estilizado de separacao de categoria."""
        self._items_meta.append({"title": header, "category": header, "is_header": True})

    def populate(self) -> None:
        """Renderiza os comandos registrados na lista."""
        self.list_widget.clear()
        t = ThemeManager.TOKENS

        for meta in self._items_meta:
            title = meta["title"]
            is_header = meta["is_header"]

            item = QListWidgetItem(title)
            if is_header:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setForeground(Qt.GlobalColor.darkGray)
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            self.list_widget.addItem(item)

        # Seleciona o primeiro comando disponivel
        self._select_first_valid()

    def _select_first_valid(self) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.flags() & Qt.ItemFlag.ItemIsEnabled:
                self.list_widget.setCurrentRow(i)
                break

    def _filter_items(self, query: str) -> None:
        """Filtra itens em tempo real de acordo com o texto digitado."""
        q = query.strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item:
                continue
            text = item.text()
            if text.startswith("---") and text.endswith("---"):
                # Header de categoria: visivel se houver busca vazia ou gerenciado
                item.setHidden(bool(q))
            else:
                matches = q in text.lower()
                item.setHidden(not matches)

        self._select_first_valid()

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        text = item.text()
        callback = self._actions_map.get(text)
        if callback:
            self.accept()
            callback()

    def keyPressEvent(self, event: Any) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            curr = self.list_widget.currentItem()
            if curr and curr.flags() & Qt.ItemFlag.ItemIsEnabled:
                self._on_item_activated(curr)
        elif key == Qt.Key.Key_Down:
            curr_row = self.list_widget.currentRow()
            for r in range(curr_row + 1, self.list_widget.count()):
                it = self.list_widget.item(r)
                if it and not it.isHidden() and it.flags() & Qt.ItemFlag.ItemIsEnabled:
                    self.list_widget.setCurrentRow(r)
                    break
        elif key == Qt.Key.Key_Up:
            curr_row = self.list_widget.currentRow()
            for r in range(curr_row - 1, -1, -1):
                it = self.list_widget.item(r)
                if it and not it.isHidden() and it.flags() & Qt.ItemFlag.ItemIsEnabled:
                    self.list_widget.setCurrentRow(r)
                    break
        else:
            super().keyPressEvent(event)
