"""ThemeManager centralizando os tokens de design do Soft Minimal Tech (Minimalismo Suave e Executivo)."""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ThemeTokens:
    # Cores - Charcoal Dark (Carvão escuro e fosco, inspirado em Claude/Linear)
    BG_CANVAS: Final[str] = "#121417"       # Fundo principal da aplicação
    BG_SIDEBAR: Final[str] = "#0D0F11"      # Sidebar um tom mais escura
    BG_PANEL: Final[str] = "#16191D"        # Elementos elevados (input, menus, modal)
    BG_SURFACE: Final[str] = "#1A1D22"      # Superficie de elevacao
    BG_USER_BUBBLE: Final[str] = "#20252C"  # Mensagem do usuario discreta a direita
    BG_FOCUS: Final[str] = "#262B33"        # Superficie em foco/hover
    
    TEXT_PRIMARY: Final[str] = "#EDEDED"    # Branco suave, anti-fadiga visual
    TEXT_SECONDARY: Final[str] = "#8E95A0"  # Metadados e titulos sutis
    TEXT_MUTED: Final[str] = "#5F6672"      # Desabilitados e separadores
    
    # Acentos Ciano (Linear / Claude style)
    ACCENT_CYAN: Final[str] = "#00D8F6"     # Acento principal ciano luminoso
    ACCENT_BLUE: Final[str] = "#00D8F6"     # Ciano / azul luminoso
    ACCENT_GREEN: Final[str] = "#3FB950"    # Verde suave (status bwrap/seguranca)
    ACCENT_RED: Final[str] = "#F85149"      # Vermelho suave
    
    # Linhas e Separadores Sutis (quase sem bordas)
    BORDER_SUBTLE: Final[str] = "#1E2228"   # Borda de separacao suave
    BORDER_ACTIVE: Final[str] = "#00D8F6"   # Borda ativa/foco
    SCROLLBAR_COLOR: Final[str] = "#252A32" # Scrollbar suave em tom carvão
    
    # Tipografia Hibrida e Estrita (Pesos 400 e 500)
    FONT_FAMILY: Final[str] = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif"
    FONT_FAMILY_MONO: Final[str] = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace"

    # Espaçamento padronizado de mensagens do chat (token único)
    CHAT_MESSAGE_SPACING: Final[int] = 16


class ThemeManager:
    """Gerenciador central de tokens de design e QSS do MARK no estilo Soft Minimal Tech."""
    
    TOKENS = ThemeTokens()

    @classmethod
    def get_user_bubble_qss(cls) -> str:
        """Estilo da mensagem do usuario: fundo suave #20252C, border-radius 8px, sem bordas duras."""
        t = cls.TOKENS
        return (
            "QFrame#userBubble {"
            f"  background: {t.BG_USER_BUBBLE};"
            "  border: none;"
            "  border-radius: 8px;"
            "  padding: 10px 14px;"
            "  margin: 0px 0px 0px 48px;"
            "}"
            "QFrame#userBubble QLabel#messageSender {"
            f"  color: {t.TEXT_SECONDARY};"
            f"  font-family: {t.FONT_FAMILY};"
            "  font-size: 11px;"
            "  font-weight: 500;"
            "  border: none;"
            "  background: transparent;"
            "}"
            "QFrame#userBubble QTextBrowser, QFrame#userBubble QLabel#messageBody {"
            f"  color: {t.TEXT_PRIMARY};"
            f"  font-family: {t.FONT_FAMILY};"
            "  font-size: 13px;"
            "  font-weight: 400;"
            "  border: none;"
            "  background: transparent;"
            f"  selection-background-color: {t.ACCENT_CYAN};"
            f"  selection-color: {t.BG_CANVAS};"
            "}"
        )

    @classmethod
    def get_assistant_bubble_qss(cls) -> str:
        """Estilo da mensagem do MARK: repousa sobre o fundo #121417, sem caixa ou borda visivel."""
        t = cls.TOKENS
        return (
            "QFrame#assistantBubble {"
            f"  background: {t.BG_CANVAS};"
            "  border: none;"
            "  border-radius: 0px;"
            "  padding: 10px 0px;"
            "  margin: 0px 48px 0px 0px;"
            "}"
            "QFrame#assistantBubble QLabel#messageSender {"
            f"  color: {t.TEXT_SECONDARY};"
            f"  font-family: {t.FONT_FAMILY};"
            "  font-size: 11px;"
            "  font-weight: 500;"
            "  border: none;"
            "  background: transparent;"
            "}"
            "QFrame#assistantBubble QTextBrowser, QFrame#assistantBubble QLabel#messageBody {"
            f"  color: {t.TEXT_PRIMARY};"
            f"  font-family: {t.FONT_FAMILY};"
            "  font-size: 13px;"
            "  font-weight: 400;"
            "  border: none;"
            "  background: transparent;"
            f"  selection-background-color: {t.ACCENT_CYAN};"
            f"  selection-color: {t.BG_CANVAS};"
            "}"
        )

    @classmethod
    def get_stylesheet(cls) -> str:
        """QSS global completo com estetica Soft Minimal Tech."""
        t = cls.TOKENS
        return f"""
/* Root & Window */
QMainWindow, #appRoot, #contentArea, #chatColumnContainer {{
    background: {t.BG_CANVAS};
}}

QFrame#sidebar, QFrame#sidebarWidget {{
    background: {t.BG_SIDEBAR};
    border-right: 1px solid {t.BORDER_SUBTLE};
}}

QWidget {{
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 13px;
    font-weight: 400;
    background: transparent;
}}

/* Scrollbars suaves com cantos arredondados */
QScrollBar:vertical {{
    width: 6px;
    background: transparent;
    margin: 0px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: {t.SCROLLBAR_COLOR};
    min-height: 24px;
    border-radius: 3px;
    border: none;
}}

QScrollBar::handle:vertical:hover {{
    background: {t.ACCENT_CYAN};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
    height: 0px;
    border: none;
}}

QScrollBar:horizontal {{
    height: 6px;
    background: transparent;
    margin: 0px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: {t.SCROLLBAR_COLOR};
    min-width: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {t.ACCENT_CYAN};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
    width: 0px;
}}

/* Top Status / Header */
#title {{
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 15px;
    font-weight: 500;
    letter-spacing: -0.2px;
}}

#connectionStatus {{
    color: {t.TEXT_SECONDARY};
    font-family: {t.FONT_FAMILY};
    font-size: 11px;
    font-weight: 400;
}}

#headerDivider {{
    background: {t.BORDER_SUBTLE};
    border: none;
    height: 1px;
}}

/* Welcome & Empty State */
#welcomeEyebrow {{
    color: {t.ACCENT_CYAN};
    font-family: {t.FONT_FAMILY};
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.8px;
}}

#welcomeTitle {{
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 20px;
    font-weight: 500;
}}

#welcomeHint {{
    color: {t.TEXT_SECONDARY};
    font-family: {t.FONT_FAMILY};
    font-size: 13px;
    font-weight: 400;
}}

#suggestionCard {{
    background: {t.BG_PANEL};
    color: {t.TEXT_PRIMARY};
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 10px;
    padding: 12px 16px;
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 500;
    text-align: left;
}}

#suggestionCard:hover {{
    background: {t.BG_FOCUS};
    border-color: {t.ACCENT_CYAN};
}}

/* Chat & Scroll */
#chatScroll, #chatContent {{
    background: {t.BG_CANVAS};
    border: none;
}}

/* Mensagens (Soft Minimal Tech) */
#userBubble {{
    background: {t.BG_USER_BUBBLE};
    border: none;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 4px 0px 4px 48px;
}}

#assistantBubble {{
    background: {t.BG_CANVAS};
    border: none;
    border-radius: 0px;
    padding: 10px 0px;
    margin: 4px 48px 4px 0px;
}}

#errorCard {{
    background: #231C20;
    border: 1px solid #5C2D35;
    border-radius: 8px;
    padding: 12px 16px;
    margin: 4px 48px 4px 0px;
}}

#errorTitle {{
    color: #FF7B72;
    font-family: {t.FONT_FAMILY};
    font-size: 13px;
    font-weight: 500;
}}

#errorBody {{
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 400;
}}

#retryButton {{
    background: {t.BG_USER_BUBBLE};
    color: {t.ACCENT_CYAN};
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 6px;
    padding: 6px 14px;
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 500;
}}

#retryButton:hover {{
    background: {t.BG_FOCUS};
    border-color: {t.ACCENT_CYAN};
}}

#switchModelButton {{
    background: {t.BG_USER_BUBBLE};
    color: {t.TEXT_SECONDARY};
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 6px;
    padding: 6px 14px;
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 500;
}}

#switchModelButton:hover {{
    background: {t.BG_FOCUS};
    color: {t.TEXT_PRIMARY};
    border-color: {t.TEXT_SECONDARY};
}}

#navButtonActive {{
    background: {t.BG_PANEL};
    border: 1px solid {t.BORDER_ACTIVE};
    color: {t.ACCENT_CYAN};
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 500;
    border-radius: 8px;
}}

/* Footer de Comando Integrado e Suave */
#commandFooter {{
    background: {t.BG_CANVAS};
    border: none;
    padding: 8px 16px 14px 16px;
}}

#composerBox {{
    background-color: {t.BG_PANEL};
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 20px;
    padding: 6px 12px;
}}

#composerBox:hover {{
    border-color: {t.BG_FOCUS};
}}

#messageInput {{
    background: transparent;
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 14px;
    font-weight: 400;
    border: none;
    padding: 6px 8px;
    selection-background-color: {t.ACCENT_CYAN};
    selection-color: {t.BG_CANVAS};
}}

#composerHint {{
    color: {t.TEXT_SECONDARY};
    font-family: {t.FONT_FAMILY};
    font-size: 11px;
    font-weight: 400;
}}

/* Telemetria Suave */
#telemetryBar {{
    background: transparent;
    border: none;
    margin-top: 6px;
}}

.telemetryItem {{
    color: {t.TEXT_SECONDARY};
    font-family: {t.FONT_FAMILY_MONO};
    font-size: 11px;
    font-weight: 400;
}}

.statusDotGreen {{
    color: {t.ACCENT_GREEN};
    font-size: 10px;
}}

.statusDotCyan {{
    color: {t.ACCENT_CYAN};
    font-size: 10px;
}}

.statusDotMuted {{
    color: {t.TEXT_MUTED};
    font-size: 10px;
}}

#cmdPaletteButton, #sidebarToggleButton {{
    background: transparent;
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 6px;
    color: {t.TEXT_SECONDARY};
    font-family: {t.FONT_FAMILY};
    font-size: 11px;
    font-weight: 500;
    padding: 3px 8px;
}}

#cmdPaletteButton:hover, #sidebarToggleButton:hover {{
    background: {t.BG_FOCUS};
    color: {t.ACCENT_CYAN};
    border-color: {t.ACCENT_CYAN};
}}

/* Botoes de Acao Harmoniosos */
QPushButton, QToolButton {{
    background: {t.BG_PANEL};
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 500;
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 8px;
    padding: 6px 12px;
}}

QPushButton:hover, QToolButton:hover {{
    color: #FFFFFF;
    border-color: {t.ACCENT_CYAN};
    background: {t.BG_FOCUS};
}}

QPushButton:pressed, QToolButton:pressed {{
    background: {t.BG_CANVAS};
}}

QPushButton:disabled, QToolButton:disabled {{
    color: {t.TEXT_MUTED};
    border-color: transparent;
}}

#sendButton {{
    background: {t.ACCENT_CYAN};
    color: {t.BG_CANVAS};
    border: none;
    border-radius: 16px;
    padding: 0px;
    min-width: 40px;
    max-width: 40px;
    min-height: 32px;
    max-height: 32px;
    font-weight: 500;
}}

#sendButton:hover {{
    background: #33E1FA;
}}

#sendButton:pressed {{
    background: #00B8D4;
}}

#sendButton:disabled {{
    background: {t.BG_USER_BUBBLE};
    color: {t.TEXT_MUTED};
}}

#attachButton {{
    background: transparent;
    color: {t.TEXT_SECONDARY};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
    font-weight: 500;
}}

#attachButton:hover {{
    color: {t.TEXT_PRIMARY};
    background: {t.BG_FOCUS};
    border-color: {t.BORDER_SUBTLE};
}}

/* Model Selector Harmonioso */
#modelSelector {{
    background: {t.BG_CANVAS};
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 11px;
    font-weight: 400;
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 8px;
    padding: 4px 8px;
    min-width: 150px;
}}

#modelSelector:hover {{
    border-color: {t.ACCENT_CYAN};
}}

#modelSelector:focus {{
    border-color: {t.ACCENT_CYAN};
}}

#processingBadge {{
    color: {t.ACCENT_CYAN};
    background: {t.BG_USER_BUBBLE};
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 6px;
    font-family: {t.FONT_FAMILY};
    font-size: 10px;
    font-weight: 500;
    padding: 3px 8px;
}}

#processingBadge[processing="local"] {{
    color: {t.ACCENT_GREEN};
    background: #1C2721;
    border-color: #23422E;
}}

#processingBadge[processing="cloud"] {{
    color: {t.ACCENT_CYAN};
    background: #162228;
    border-color: #1F3540;
}}

#processingBadge[processing="disabled"] {{
    color: {t.TEXT_MUTED};
    background: {t.BG_CANVAS};
    border-color: {t.BORDER_SUBTLE};
}}

#routingTargetBadge {{
    color: {t.ACCENT_GREEN};
    background: #1C2721;
    border: 1px solid #23422E;
    border-radius: 6px;
    font-family: {t.FONT_FAMILY};
    font-size: 11px;
    font-weight: 500;
    padding: 4px 10px;
}}

#routingTargetBadge[target="local"] {{
    color: {t.ACCENT_GREEN};
    background: #1C2721;
    border-color: #23422E;
}}

#routingTargetBadge[target="cloud"] {{
    color: #E3B341;
    background: #2D2619;
    border-color: #5C481A;
}}

#routingTargetBadge[target="warning"] {{
    color: #E3B341;
    background: #2D2619;
    border-color: #5C481A;
}}

#routingTargetBadge[target="error"] {{
    color: {t.ACCENT_RED};
    background: #2A181A;
    border-color: #592225;
}}

#reasoningSelector {{
    background: {t.BG_CANVAS};
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 11px;
    font-weight: 400;
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 8px;
    padding: 4px 8px;
    min-width: 148px;
}}

#reasoningSelector:hover {{
    border-color: {t.ACCENT_CYAN};
}}

/* QComboBox Dropdown Menu */
QComboBox::drop-down {{
    border: none;
    background: transparent;
    width: 16px;
}}

QComboBox QAbstractItemView {{
    background: {t.BG_PANEL};
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 8px;
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 400;
    selection-background-color: {t.BG_FOCUS};
    selection-color: {t.ACCENT_CYAN};
    padding: 4px;
}}

/* Dialogs & Command Palette Suave */
QDialog, QDialog#commandPaletteDialog {{
    background: {t.BG_PANEL};
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 12px;
}}

#commandPaletteSearch {{
    background: {t.BG_CANVAS};
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 13px;
    font-weight: 400;
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 8px;
    padding: 8px 12px;
}}

#commandPaletteSearch:focus {{
    border-color: {t.ACCENT_CYAN};
}}

#commandPaletteList {{
    background: {t.BG_PANEL};
    border: none;
    color: {t.TEXT_PRIMARY};
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 400;
    selection-background-color: {t.BG_FOCUS};
    selection-color: {t.ACCENT_CYAN};
}}

/* QMenu Popup */
QMenu {{
    background: {t.BG_PANEL};
    color: {t.TEXT_PRIMARY};
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 10px;
    padding: 6px;
}}

QMenu::item {{
    padding: 7px 16px;
    border-radius: 6px;
    font-family: {t.FONT_FAMILY};
    font-size: 12px;
    font-weight: 400;
}}

QMenu::item:selected {{
    background: {t.BG_FOCUS};
    color: {t.ACCENT_CYAN};
}}

QMenu::item:disabled {{
    color: {t.TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 500;
    padding: 8px 12px 4px 12px;
}}

/* Code Pre Blocks */
pre {{
    font-family: {t.FONT_FAMILY_MONO};
    font-size: 12px;
    font-weight: 400;
    color: {t.TEXT_PRIMARY};
    background-color: #0B0D0F;
    border: 1px solid {t.BORDER_SUBTLE};
    border-radius: 8px;
    padding: 10px;
    white-space: pre-wrap;
}}
"""

