"""Pacote de componentes de interface e UI do MARK."""

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

__all__ = [
    "ActionConsentDialog",
    "ChatWidget",
    "CommandPaletteDialog",
    "ConsentBridge",
    "GodsEyeStatusWidget",
    "MainWindow",
    "MessageBubbleWidget",
    "ProviderSelectorWidget",
    "SettingsWidget",
    "ThemeManager",
    "ThinkingIndicatorWidget",
    "UniversalKeyVaultDialog",
    "VoiceCallWidget",
]


def __getattr__(name: str):
    if name in ("MainWindow", "VoiceCallWidget"):
        from .main_window import MainWindow, VoiceCallWidget
        return locals()[name]
    if name in ("SettingsWidget", "UniversalKeyVaultDialog"):
        from .settings import SettingsWidget, UniversalKeyVaultDialog
        return locals()[name]
    if name == "CommandPaletteDialog":
        from .command_palette import CommandPaletteDialog
        return CommandPaletteDialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

