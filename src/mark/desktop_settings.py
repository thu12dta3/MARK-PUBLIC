class SettingsMixin:
    def refresh_settings_summary(self):
        try:
            from . import desktop as desktop_module
            from .config import load_config
            cfg = load_config()
            models = cfg.get("models", {})
            memory = cfg.get("memory", {})
            lines = (
                f"Conversa: {models.get('chat', 'não configurado')}",
                f"Resposta rápida: {models.get('call', 'não configurado')}",
                f"Contexto curto: até {memory.get('max_messages', '—')} mensagens",
                f"Memória compartilhada local: {desktop_module.MEMORY_FILE}",
                "GitHub: integração existente, chamada somente quando solicitada",
                "Câmera e microfone: controles permanecem nas áreas Geral e Conexões",
            )
            self.settings_summary.setText("\n".join(lines))
        except Exception as exc:
            self.settings_summary.setText(f"Não consegui ler o resumo local ({type(exc).__name__}).")

    def open_memory_manager(self):
        from .desktop_memory import MemoryDialog
        dialog = MemoryDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_provider_manager(self):
        from .desktop_providers import ProviderDialog
        dialog = ProviderDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_plugins(self):
        from .desktop_plugins import PluginsDialog
        dialog = PluginsDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()
