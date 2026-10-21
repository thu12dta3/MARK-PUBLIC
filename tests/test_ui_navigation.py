"""Protocolo Antigravity de Testes Visuais e de Navegação da UI do MARK.

Verifica programaticamente via PyQt6.QtTest.QTest:
- Transição para o índice 1 ao clicar em Configurações.
- Retorno ao índice 0 ao clicar no botão "← Voltar para Conversa".
- Transição para o índice 2 ao clicar em Chamada, sem abertura de pop-up (QDialog) solto.
- Injeção e salvamento de chave customizada (X_API_KEY) no LLMKeyVault com exibição mascarada.
- Funcionamento do botão inferior esquerdo apenas expandindo/recolhendo a Sidebar sem invocar modais.
- Comportamento visual do ThinkingIndicatorWidget.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton

from mark.config.llm_config import LLMKeyVault
from mark.ui.main_window import MainWindow
from mark.ui.settings import mask_api_key
from mark.ui.widgets import ThinkingIndicatorWidget


class TestUINavigationProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_file = Path(self.tmp_dir.name) / "vault.bin"
        self.master_key_file = Path(self.tmp_dir.name) / "master.key"
        self.vault = LLMKeyVault(
            vault_file=self.vault_file,
            master_key_file=self.master_key_file,
        )
        self.window = MainWindow(vault=self.vault)
        self.window.show()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.tmp_dir.cleanup()

    def test_step1_click_settings_switches_stacked_widget_to_index_1(self):
        """Teste de clique no menu de configurações -> Verifica se o índice do StackedWidget muda para 1."""
        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)

        # Clica no botão de configurações da sidebar via QTest
        QTest.mouseClick(self.window.settings_nav_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(self.window.stacked_widget.currentIndex(), 1)
        self.assertEqual(self.window.stacked_widget.currentWidget(), self.window.settings_page)

    def test_step2_click_back_button_returns_stacked_widget_to_index_0(self):
        """Teste de clique no botão '← Voltar' -> Verifica se o índice retorna para 0."""
        # Coloca a janela em Configurações (índice 1)
        self.window.open_settings()
        self.app.processEvents()
        self.assertEqual(self.window.stacked_widget.currentIndex(), 1)

        # Localiza o botão '← Voltar para Conversa'
        back_btn = self.window.settings_widget.back_button
        self.assertIsNotNone(back_btn)
        self.assertIn("← Voltar", back_btn.text())

        # Executa clique programático via QTest
        QTest.mouseClick(back_btn, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)
        self.assertEqual(self.window.stacked_widget.currentWidget(), self.window.chat_page)

    def test_step3_click_call_switches_to_index_2_without_loose_popup(self):
        """Teste de clique no botão 'Chamada' -> Muda para 2 e não gera pop-up solto (QDialog)."""
        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)

        # Clica no botão 'Chamada' no cabeçalho do chat
        QTest.mouseClick(self.window.call_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        # Deve estar no índice 2
        self.assertEqual(self.window.stacked_widget.currentIndex(), 2)
        self.assertEqual(self.window.stacked_widget.currentWidget(), self.window.voice_page)

        # Verifica que NENHUM QDialog modal ou pop-up separado foi aberto
        self.assertIsNone(QApplication.activeModalWidget())
        top_level_dialogs = [
            w for w in QApplication.topLevelWidgets()
            if isinstance(w, QDialog) and w.isVisible()
        ]
        self.assertEqual(len(top_level_dialogs), 0)

        # Testa o botão 'Encerrar e Voltar' na tela de voz
        end_call_btn = self.window.voice_widget.end_call_button
        self.assertIsNotNone(end_call_btn)
        self.assertEqual(end_call_btn.text(), "Encerrar e Voltar")

        QTest.mouseClick(end_call_btn, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)

    def test_step4_custom_api_key_injection_saves_to_llm_key_vault(self):
        """Teste de injeção de chave customizada (X_API_KEY) -> Salva corretamente no LLMKeyVault."""
        # Navega para Configurações
        self.window.open_settings()
        self.app.processEvents()

        settings_widget = self.window.settings_widget

        # Injeta chave customizada qualquer (sem provedores hardcoded)
        custom_provider = "X_API_KEY"
        custom_key_val = "sk-custom-live-token-test-12345"

        settings_widget.provider_combo.setEditText(custom_provider)
        settings_widget.key_input.setText(custom_key_val)

        # Clica no botão 'Salvar Chave' via QTest
        QTest.mouseClick(settings_widget.save_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        # Valida que o LLMKeyVault gravou e decifra com sucesso
        saved_key = self.vault.get_key("X_API_KEY")
        self.assertEqual(saved_key, custom_key_val)

        # Valida que get_key case-insensitive também funciona
        self.assertEqual(self.vault.get_key("x_api_key"), custom_key_val)

        # Valida que a tabela exibe a chave devidamente ocultada (sk-...****)
        table = settings_widget.keys_table
        self.assertGreaterEqual(table.rowCount(), 1)

        found_in_table = False
        for row in range(table.rowCount()):
            provider_item = table.item(row, 0)
            key_item = table.item(row, 1)
            if provider_item and provider_item.text() == "X_API_KEY":
                self.assertIsNotNone(key_item)
                self.assertEqual(key_item.text(), "sk-...****")
                found_in_table = True
                break

        self.assertTrue(found_in_table, "Chave customizada X_API_KEY não foi encontrada na tabela da UI.")

        # Valida remoção universal da chave
        QTest.mouseClick(settings_widget.remove_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertIsNone(self.vault.get_key("X_API_KEY"))

    def test_step5_sidebar_toggle_button_expands_and_collapses_without_modals(self):
        """Certifique-se de que o botão inferior esquerdo apenas expanda/recolha a Sidebar sem modais."""
        initial_visibility = self.window.sidebar.isVisible()

        # Clica no botão inferior esquerdo
        QTest.mouseClick(self.window.sidebar_toggle_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        # Visibilidade deve ter sido alternada
        self.assertNotEqual(self.window.sidebar.isVisible(), initial_visibility)

        # Nenhum modal aberto
        self.assertIsNone(QApplication.activeModalWidget())

        # Clica novamente para restaurar
        QTest.mouseClick(self.window.sidebar_toggle_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.sidebar.isVisible(), initial_visibility)

    def test_step6_thinking_indicator_widget_behavior(self):
        """Valida que o ThinkingIndicatorWidget exibe 'MARK está pensando...' e pulsação."""
        indicator = self.window.thinking_indicator
        self.assertFalse(indicator.is_thinking())

        indicator.start()
        self.assertTrue(indicator.is_thinking())
        self.assertTrue(indicator.isVisible())
        self.assertIn("MARK está pensando", indicator.text_label.text())

        indicator.stop()
        self.assertFalse(indicator.is_thinking())
        self.assertTrue(indicator.isHidden())

    def test_step7_restored_advanced_composer_components(self):
        """Valida restauração do Composer avançado: attachButton, Composer multilinha, seletores de IA e sendButton."""
        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)

        # Botão de anexo
        attach_btn = self.window.attach_button
        self.assertIsNotNone(attach_btn)
        self.assertEqual(attach_btn.objectName(), "attachButton")

        # Campo de entrada Composer multilinha
        from mark.desktop_composer import Composer
        self.assertIsInstance(self.window.input, Composer)
        self.assertEqual(self.window.input.objectName(), "messageInput")

        # Seletores dinâmicos de IA
        from mark.ui.widgets import ProviderSelectorWidget
        self.assertIsInstance(self.window.model_selector, ProviderSelectorWidget)
        self.assertEqual(self.window.model_selector.objectName(), "modelSelector")
        self.assertEqual(self.window.processing_badge.objectName(), "processingBadge")

        # Seletor de raciocínio com níveis descritivos
        self.assertEqual(self.window.reasoning_selector.objectName(), "reasoningSelector")
        reasoning_items = [self.window.reasoning_selector.itemText(i) for i in range(self.window.reasoning_selector.count())]
        self.assertTrue(any("Balanceado" in t for t in reasoning_items))

        # Botão de envio
        self.assertEqual(self.window.send_button.objectName(), "sendButton")

    def test_step8_restored_rich_chat_and_tools_menu(self):
        """Valida visualizador rico de mensagens e menu de ferramentas categorizado."""
        # Renderização de balões de mensagens
        self.window._append_bubble("Pergunta visual do usuário", "user")
        self.window._append_bubble("Resposta com markdown do assistente", "assistant")
        self.app.processEvents()

        from PyQt6.QtWidgets import QFrame
        user_b = self.window.findChild(QFrame, "userBubble")
        asst_b = self.window.findChild(QFrame, "assistantBubble")
        self.assertIsNotNone(user_b)
        self.assertIsNotNone(asst_b)

        # Menu de ferramentas categorizado
        menu = self.window.tools_menu
        actions = [a.text() for a in menu.actions()]
        self.assertIn("--- SEGURANÇA E AGENTES ---", actions)
        self.assertIn("--- ENGENHARIA E CÓDIGO ---", actions)
        self.assertIn("--- UTILITÁRIOS E MÍDIA ---", actions)
        self.assertIn("--- SISTEMA ---", actions)

    def test_step9_sidebar_agent_context_switching_in_index_0(self):
        """Valida que 'Geral', 'Engenheiro' e 'Professor' alteram o contexto/agente dentro do índice 0."""
        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)

        # Clica no botão Engenheiro na sidebar
        QTest.mouseClick(self.window.engineer_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        # Deve permanecer no índice 0 (Chat) mas com contexto alterado para engineer
        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)
        self.assertEqual(self.window.current_specialization, "engineer")
        self.assertTrue(self.window.engineer_chat_badge.isVisible())

        # Clica no botão Professor na sidebar
        QTest.mouseClick(self.window.professor_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)
        self.assertEqual(self.window.current_specialization, "professor")
        self.assertFalse(self.window.engineer_chat_badge.isVisible())

        # Clica no botão Geral na sidebar
        QTest.mouseClick(self.window.chat_nav_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)
        self.assertEqual(self.window.current_specialization, "general")


if __name__ == "__main__":
    unittest.main()

