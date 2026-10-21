"""Testes unitarios e de integracao para os componentes de UI (PyQt6/QSS) do MARK."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QMenu, QWidget

from mark.desktop import MarkWindow
from mark.desktop_theme import STYLESHEET
from mark.ui import ChatWidget, MessageBubbleWidget, ProviderSelectorWidget


class UIComponentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def _make_window(self):
        with patch.object(MarkWindow, "_load_history"), \
             patch.object(MarkWindow, "refresh_status"):
            win = MarkWindow()
        win.status_timer.stop()
        win.history_timer.stop()
        return win

    # -------------------------------------------------------------------------
    # Passo 1: Limpeza e Estilizacao de Baloes de Mensagem
    # -------------------------------------------------------------------------
    def test_user_bubble_styling_and_clean_children(self):
        """Card do usuario deve ter fundo #2D333B, border: none, border-radius: 8px."""
        bubble = MessageBubbleWidget(text="Ola MARK", role="user")
        try:
            self.assertEqual(bubble.objectName(), "userBubble")
            ss = bubble.styleSheet()
            self.assertIn("#2D333B", ss)
            self.assertIn("border: none", ss)
            self.assertIn("border-radius: 8px", ss)

            # Widgets filhos internos nao devem ter bordas nem fundos
            self.assertIsNotNone(bubble.label)
            label_ss = bubble.label.styleSheet()
            self.assertIn("border: none", label_ss)
            self.assertIn("background: transparent", label_ss)
            self.assertIn("selection-background-color: #58A6FF", label_ss)
        finally:
            bubble.deleteLater()

    def test_assistant_bubble_styling_and_clean_children(self):
        """Card do MARK deve ter fundo #1A1D21, border: none, border-radius: 0px."""
        bubble = MessageBubbleWidget(text="Resposta executiva do MARK", role="assistant")
        try:
            self.assertEqual(bubble.objectName(), "assistantBubble")
            ss = bubble.styleSheet()
            self.assertIn("#1A1D21", ss)
            self.assertIn("border: none", ss)
            self.assertIn("border-radius: 0px", ss)

            # Widgets filhos internos sem borda, transparentes e selecao #58A6FF
            self.assertIsNotNone(bubble.label)
            label_ss = bubble.label.styleSheet()
            self.assertIn("border: none", label_ss)
            self.assertIn("background: transparent", label_ss)
            self.assertIn("selection-background-color: #58A6FF", label_ss)
        finally:
            bubble.deleteLater()

    def test_user_and_assistant_bubbles_are_distinct(self):
        """Estilos de user e assistente devem ser claramente distintos."""
        user_bubble = MessageBubbleWidget(text="Usuario", role="user")
        asst_bubble = MessageBubbleWidget(text="MARK", role="assistant")
        try:
            self.assertNotEqual(user_bubble.styleSheet(), asst_bubble.styleSheet())
        finally:
            user_bubble.deleteLater()
            asst_bubble.deleteLater()

    def test_chat_widget_and_append_bubble_in_window(self):
        """Window deve inserir bolhas com alinhamento correto (user a direita, assistente a esquerda)."""
        win = self._make_window()
        try:
            win._append_bubble("Pergunta do usuario", "user")
            win._append_bubble("Resposta do assistente", "assistant")

            user_b = win.findChild(QFrame, "userBubble")
            asst_b = win.findChild(QFrame, "assistantBubble")
            self.assertIsNotNone(user_b)
            self.assertIsNotNone(asst_b)

            # Labels de mensagem dentro dos baloes nao devem ter bordas duplas
            for b in (user_b, asst_b):
                body = b.findChild(QLabel, "messageBody")
                self.assertIsNotNone(body)
                self.assertIn("border: none", body.styleSheet())
                self.assertIn("background: transparent", body.styleSheet())
        finally:
            win.close()

    # -------------------------------------------------------------------------
    # Passo 2: Nomes Exatos dos Modelos na Selecao de IA e Deteccao Dinamica
    # -------------------------------------------------------------------------
    def test_provider_selector_displays_real_installed_ollama_models(self):
        """Seletor deve exibir dinamicamente os modelos reais retornados pelo Ollama."""
        selector = ProviderSelectorWidget()
        try:
            mock_installed = ["R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m", "llama3:8b"]
            with patch("mark.cognition.provider_adapter.OllamaProviderAdapter.get_installed_models", return_value=mock_installed):
                selector.populate_from_config(installed_models=mock_installed, available_providers={})

            item_texts = [selector.itemText(i) for i in range(selector.count())]
            self.assertTrue(any("Qwen 2.5 Coder 7B" in t for t in item_texts))
            self.assertTrue(any("Llama 3 (llama3:8b)" in t for t in item_texts))

            # Modelos ficticios nao baixados e nao configurados nao devem aparecer
            self.assertNotIn("Qwen 2.5 Coder 32B (Ollama Local)", item_texts)
            self.assertNotIn("Claude 3.5 Sonnet", item_texts)
            self.assertNotIn("GPT-4o", item_texts)
        finally:
            selector.deleteLater()

    def test_provider_selector_ollama_offline_or_empty(self):
        """Quando Ollama estiver sem modelos ou offline, oculta modelos ficticios."""
        selector = ProviderSelectorWidget()
        try:
            # Sem modelos locais e sem chaves de nuvem
            selector.populate_from_config(installed_models=[], available_providers={})
            self.assertEqual(selector.count(), 1)
            self.assertEqual(selector.itemText(0), "Nenhum provedor ativo - Configure as Chaves")
            self.assertFalse(selector.model().item(0).isEnabled())

            # Sem modelos locais, mas com provedor de nuvem configurado
            from mark.config.llm_config import ProviderProfile
            mock_cloud = {"openai": ProviderProfile("openai", "gpt-4o", api_key="sk-test")}
            selector.populate_from_config(installed_models=[], available_providers=mock_cloud)

            item_texts = [selector.itemText(i) for i in range(selector.count())]
            self.assertIn("Nenhum modelo Ollama instalado", item_texts)
            self.assertIn("GPT-4o", item_texts)
            # O item de nenhum modelo local deve estar desabilitado
            self.assertFalse(selector.model().item(0).isEnabled())
            # O item de nuvem deve estar habilitado
            self.assertTrue(selector.model().item(1).isEnabled())
        finally:
            selector.deleteLater()

    def test_provider_selector_hides_unconfigured_cloud_models(self):
        """Seletor deve ocultar modelos em nuvem cujas chaves de API nao estejam configuradas."""
        selector = ProviderSelectorWidget()
        try:
            from mark.config.llm_config import LLMConfig, ProviderProfile
            cfg = LLMConfig()
            # Apenas Gemini configurado
            cfg.set_provider_key("gemini", "test-gemini-key", save_to_vault=False)

            available = cfg.get_available_providers()
            selector.populate_from_config(installed_models=["qwen3-vl:4b"], available_providers=available)

            item_texts = [selector.itemText(i) for i in range(selector.count())]
            self.assertTrue(any("Gemini 2.0 Flash" in t for t in item_texts))

            # Nao configurados nao devem constar na lista
            self.assertNotIn("Claude 3.5 Sonnet", item_texts)
            self.assertNotIn("GPT-4o", item_texts)
            self.assertNotIn("DeepSeek V3", item_texts)
        finally:
            selector.deleteLater()

    def test_provider_selector_processing_type_and_badge(self):
        """Seletor deve indicar processamento (Nuvem / Local Ollama) no tooltip e no badge."""
        selector = ProviderSelectorWidget()
        badge = QLabel()
        try:
            from mark.config.llm_config import ProviderProfile
            mock_cloud = {"anthropic": ProviderProfile("anthropic", "claude-3-5-sonnet-20241022", api_key="sk-ant")}
            mock_local = ["R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m"]

            selector.populate_from_config(installed_models=mock_local, available_providers=mock_cloud)
            selector.set_badge_widget(badge)

            # Local Ollama
            selector.select_model_by_name("Qwen 2.5 Coder")
            self.assertEqual(selector.processing_type(), "Local Ollama")
            self.assertEqual(badge.text(), "Local Ollama")
            self.assertIn("Local Ollama", selector.toolTip())

            # Nuvem
            selector.select_model_by_name("Claude 3.5 Sonnet")
            self.assertEqual(selector.processing_type(), "Nuvem")
            self.assertEqual(badge.text(), "Nuvem")
            self.assertIn("Nuvem", selector.toolTip())
        finally:
            selector.deleteLater()
            badge.deleteLater()

    def test_window_has_provider_selector_and_badge(self):
        """MarkWindow deve ter ProviderSelectorWidget e badge de processamento ativos."""
        win = self._make_window()
        try:
            self.assertIsInstance(win.model_selector, ProviderSelectorWidget)
            self.assertTrue(hasattr(win, "processing_badge"))
            self.assertIn(win.processing_badge.text(), {"Local Ollama", "Nuvem", "Indisponível"})

            # Reasoning selector com nomes descritivos e sem rotulo generico "Medio"
            reasoning_texts = [win.reasoning_selector.itemText(i) for i in range(win.reasoning_selector.count())]
            self.assertNotIn("Médio", reasoning_texts)
            self.assertTrue(any("Balanceado" in t for t in reasoning_texts))
        finally:
            win.close()

    def test_provider_selector_async_discovery_worker(self):
        """ModelDiscoveryWorker deve emitir discovery_finished e atualizar o seletor sem travar."""
        from PyQt6.QtCore import QEventLoop
        from mark.ui.widgets import ModelDiscoveryWorker

        selector = ProviderSelectorWidget()
        try:
            loop = QEventLoop()
            finished_called = []

            def on_finished(installed, cloud):
                finished_called.append((installed, cloud))
                selector._on_discovery_finished(installed, cloud)
                loop.quit()

            worker = ModelDiscoveryWorker(parent=selector)
            worker.discovery_finished.connect(on_finished)
            worker.start()
            loop.exec()

            self.assertEqual(len(finished_called), 1)
            self.assertGreaterEqual(selector.count(), 1)
        finally:
            selector.deleteLater()

    # -------------------------------------------------------------------------
    # Passo 3: Menu Ferramentas Categorizado e Scrollbars
    # -------------------------------------------------------------------------
    def test_tools_menu_categorized_sections_and_actions(self):
        """Menu popup de ferramentas deve ser categorizado com as 4 secoes estilizadas."""
        win = self._make_window()
        try:
            menu = win.tools_menu
            actions = [a.text() for a in menu.actions()]

            # Verifica presenca dos titulos de secao estilizados como separadores
            self.assertIn("--- SEGURANÇA E AGENTES ---", actions)
            self.assertIn("--- ENGENHARIA E CÓDIGO ---", actions)
            self.assertIn("--- UTILITÁRIOS E MÍDIA ---", actions)
            self.assertIn("--- SISTEMA ---", actions)

            # Verifica itens de Seguranca e Agentes
            self.assertIn("Antivírus e ameaças · Sentinel", actions)
            self.assertIn("Dispositivos e segurança", actions)

            # Verifica itens de Engenharia e Codigo
            self.assertIn("Tarefas do Engineer", actions)
            self.assertIn("Engenharia Autônoma · Sandbox", actions)
            self.assertIn("Código · GitHub", actions)

            # Verifica itens de Utilitarios e Midia
            self.assertIn("Agenda e lembretes", actions)
            self.assertIn("E-mail · Tuta", actions)
            self.assertIn("Visão ao vivo / Câmera", actions)

            # Verifica itens de Sistema
            self.assertIn("Plugins...", actions)
            self.assertIn("Limpar conversa", actions)

            # Titulos de secao devem ser desabilitados para agir como separadores visuais
            for a in menu.actions():
                if a.text().startswith("---"):
                    self.assertFalse(a.isEnabled(), f"Separador {a.text()} deve estar desabilitado")
        finally:
            win.close()

    def test_stylesheet_scrollbar_and_capsule_styles(self):
        """QSS deve conter scrollbar suave (6px em #334155), fundo transparente e tipografia moderna."""
        self.assertIn("width: 6px", STYLESHEET)
        self.assertIn("background: #334155", STYLESHEET)
        self.assertIn("selection-background-color: #58A6FF", STYLESHEET)
        self.assertIn("Inter", STYLESHEET)

    def test_command_palette_modal_and_shortcuts(self):
        """Janela principal deve registrar CommandPaletteDialog, atalhos Ctrl+K e barra de telemetria."""
        win = self._make_window()
        try:
            self.assertTrue(hasattr(win, "command_palette"))
            self.assertTrue(win.sidebar_scroll.isHidden())
            self.assertTrue(win.tools_button.isHidden())
            self.assertIsNotNone(win.bwrap_status_label)
            self.assertIsNotNone(win.mtls_status_label)
            self.assertIn("BWRAP", win.bwrap_status_label.text())
            self.assertIn("mTLS", win.mtls_status_label.text())
        finally:
            win.close()


    # -------------------------------------------------------------------------
    # Passo 4: Modulo mark.ui e Execucao Local
    # -------------------------------------------------------------------------
    def test_mark_ui_module_exports_and_entrypoint(self):
        """Garante que mark.ui exporta os componentes e possui __main__.py funcional."""
        import mark.ui
        self.assertTrue(hasattr(mark.ui, "MessageBubbleWidget"))
        self.assertTrue(hasattr(mark.ui, "ChatWidget"))
        self.assertTrue(hasattr(mark.ui, "ProviderSelectorWidget"))

        entry_point = Path(__file__).resolve().parents[1] / "src" / "mark" / "ui" / "__main__.py"
        self.assertTrue(entry_point.exists())


if __name__ == "__main__":
    unittest.main()
