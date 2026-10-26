"""Testes unitarios e de integracao para os componentes de UI (PyQt6/QSS) do MARK."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QSettings, Qt
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
        """Card do usuario deve ter fundo carvão #20252C, border: none, border-radius: 8px e selecao ciano #00D8F6."""
        bubble = MessageBubbleWidget(text="Ola MARK", role="user")
        try:
            self.assertEqual(bubble.objectName(), "userBubble")
            ss = bubble.styleSheet()
            self.assertIn("#20252C", ss)
            self.assertIn("border: none", ss)
            self.assertIn("border-radius: 8px", ss)

            # Widgets filhos internos nao devem ter bordas nem fundos
            self.assertIsNotNone(bubble.label)
            label_ss = bubble.label.styleSheet()
            self.assertIn("border: none", label_ss)
            self.assertIn("background: transparent", label_ss)
            self.assertIn("selection-background-color: #00D8F6", label_ss)
        finally:
            bubble.deleteLater()

    def test_assistant_bubble_styling_and_clean_children(self):
        """Mensagem do assistente deve ser texto direto sobre fundo carvão #121417, sem balão, border: none, border-radius: 0px."""
        bubble = MessageBubbleWidget(text="Resposta executiva do MARK", role="assistant")
        try:
            self.assertEqual(bubble.objectName(), "assistantBubble")
            ss = bubble.styleSheet()
            self.assertIn("#121417", ss)
            self.assertIn("border: none", ss)
            self.assertIn("border-radius: 0px", ss)

            # Widgets filhos internos sem borda, transparentes e selecao #00D8F6
            self.assertIsNotNone(bubble.label)
            label_ss = bubble.label.styleSheet()
            self.assertIn("border: none", label_ss)
            self.assertIn("background: transparent", label_ss)
            self.assertIn("selection-background-color: #00D8F6", label_ss)
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

    def test_provider_selector_restores_selected_model_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = str(Path(directory) / "mark.ini")
            first_settings = QSettings(settings_path, QSettings.Format.IniFormat)
            first = ProviderSelectorWidget(settings=first_settings)
            first.populate_from_config(
                installed_models=["qwen3-vl:4b", "llama3:8b"],
                available_providers={},
            )
            first.setCurrentIndex(1)
            self.assertEqual(first.currentData(), "ollama:llama3:8b")
            first.deleteLater()
            self.app.processEvents()

            reopened_settings = QSettings(settings_path, QSettings.Format.IniFormat)
            reopened = ProviderSelectorWidget(settings=reopened_settings)
            try:
                reopened.populate_from_config(
                    installed_models=["qwen3-vl:4b", "llama3:8b"],
                    available_providers={},
                )
                self.assertEqual(reopened.currentData(), "ollama:llama3:8b")
            finally:
                reopened.deleteLater()

    def test_provider_selector_persists_first_local_model_by_id(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = str(Path(directory) / "mark.ini")
            first_settings = QSettings(settings_path, QSettings.Format.IniFormat)
            first = ProviderSelectorWidget(settings=first_settings)
            first.populate_from_config(
                installed_models=["qwen3-vl:4b", "llama3:8b"],
                available_providers={},
            )
            self.assertEqual(first.current_model_id(), "qwen3-vl:4b")
            first.setCurrentIndex(0)
            first.deleteLater()
            self.app.processEvents()

            reopened_settings = QSettings(settings_path, QSettings.Format.IniFormat)
            reopened = ProviderSelectorWidget(settings=reopened_settings)
            try:
                reopened.populate_from_config(
                    installed_models=["llama3:8b", "qwen3-vl:4b"],
                    available_providers={},
                )
                self.assertEqual(reopened.current_model_id(), "qwen3-vl:4b")
            finally:
                reopened.deleteLater()

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
            self.assertIn("gemini-3.8-flash", item_texts)

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
        """QSS deve conter scrollbar suave em tom carvão (#252A32), acentos ciano (#00D8F6) e tipografia Inter estrita."""
        self.assertIn("width: 6px", STYLESHEET)
        self.assertIn("background: #252A32", STYLESHEET)
        self.assertIn("selection-background-color: #00D8F6", STYLESHEET)
        self.assertIn("Inter", STYLESHEET)

    def test_strict_typography_weights_inter_400_and_500(self):
        """Tipografia deve usar fonte Inter com pesos estritos 400 (Regular) e 500 (Medium)."""
        import re
        # Verifica no QSS que os pesos utilizados são estritamente 400 e 500
        weights = set(re.findall(r"font-weight:\s*(\d+)", STYLESHEET))
        self.assertTrue(weights.issubset({"400", "500"}), f"Pesos de fonte permitidos sao apenas 400 e 500, obtido: {weights}")

    def test_chat_column_centralized_760px(self):
        """Área de conversa deve ser uma coluna centralizada responsiva de até 920px de largura máxima em tela cheia."""
        win = self._make_window()
        try:
            container = getattr(win, "chat_column_container", None) or getattr(win, "chat_scroll", None)
            self.assertIsNotNone(container)
            max_w = container.maximumWidth()
            self.assertEqual(max_w, 920, f"Largura máxima da coluna deve ser 920px para conforto em tela cheia, obtido: {max_w}")
        finally:
            win.close()

    def test_pill_input_container_with_integrated_send_button(self):
        """Entrada (Input) deve ter formato de pílula arredondada com botão de enviar integrado internamente."""
        win = self._make_window()
        try:
            self.assertIsNotNone(win.input)
            self.assertIsNotNone(win.send_button)
            # O input container / composer_box deve ser em formato de pílula
            composer_box = getattr(win, "composer_box", None)
            self.assertIsNotNone(composer_box)
            ss = composer_box.styleSheet()
            self.assertIn("border-radius:", ss)
            # Botão de enviar com acento ciano
            send_ss = win.send_button.styleSheet()
            self.assertTrue("#00D8F6" in send_ss or "#00D8F6" in STYLESHEET)
        finally:
            win.close()

    def test_model_selector_no_truncation_and_real_id_propagation(self):
        """Seletor de modelos deve ser cinza minimalista, exibir texto completo sem cortes e propagar o ID real."""
        from PyQt6.QtWidgets import QComboBox
        selector = ProviderSelectorWidget()
        try:
            mock_installed = ["R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m", "llama3:8b"]
            selector.populate_from_config(installed_models=mock_installed, available_providers={})

            # Política de ajuste ao conteúdo ativada para impedir truncamento
            self.assertEqual(selector.sizeAdjustPolicy(), QComboBox.SizeAdjustPolicy.AdjustToContents)
            # Visualização não deve elidir texto
            self.assertEqual(selector.view().textElideMode(), Qt.TextElideMode.ElideNone)

            # current_model_id deve retornar o ID real exato do modelo (sem 'ollama:' ou 'mark' hardcoded)
            selector.setCurrentIndex(0)
            self.assertEqual(selector.current_model_id(), "R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m")
            selector.setCurrentIndex(1)
            self.assertEqual(selector.current_model_id(), "llama3:8b")
        finally:
            selector.deleteLater()

    def test_model_selector_and_pill_width_fits_full_model_name(self):
        """Para cada nome de modelo, compara a largura do texto (QFontMetrics.horizontalAdvance)
        com a largura disponível do widget. Se o texto for maior ou truncado, o teste falha."""
        from PyQt6.QtGui import QFontInfo, QFontMetrics
        from mark.ui.main_window import MainWindow

        win = MainWindow()
        try:
            win.resize(1024, 768)
            win.show()
            self.app.processEvents()

            # Valida que a coluna mede até 920px de largura máxima
            self.assertEqual(win.chat_column_container.maximumWidth(), 920)

            test_models = [
                "R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m",
            ]

            for model_id in test_models:
                win.model_selector.populate_from_config(installed_models=[model_id], available_providers={})
                win._update_routing_target_badge()
                self.app.processEvents()

                # 1. Validação do Seletor de Modelos
                ms = win.model_selector
                ms_font = ms.font()
                ms_family = QFontInfo(ms_font).family()
                ms_text = ms.currentText()
                ms_fm = QFontMetrics(ms_font)
                ms_text_w = ms_fm.horizontalAdvance(ms_text)
                ms_widget_w = ms.width()

                print(f"\n[TEST METRICS] ModelSelector - Fonte: {ms_family}, Texto: '{ms_text}' (largura={ms_text_w}px), Widget width={ms_widget_w}px")

                self.assertIn(model_id, ms_text, f"Nome completo do modelo {model_id} deve estar presente no seletor, obtido: {ms_text}")
                self.assertNotIn("...", ms_text, "Nome do modelo no seletor não pode conter reticências")
                self.assertGreaterEqual(
                    ms_widget_w,
                    ms_text_w,
                    f"Largura disponível do seletor ({ms_widget_w}px) deve comportar a largura do texto ({ms_text_w}px) na fonte {ms_family}",
                )

                # 2. Validação da Pílula Inferior (routingTargetBadge)
                pill = win.routing_target_badge
                pill_font = pill.font()
                pill_family = QFontInfo(pill_font).family()
                pill_text = pill.text()
                pill_fm = QFontMetrics(pill_font)
                pill_text_w = pill_fm.horizontalAdvance(pill_text)
                pill_widget_w = pill.width()

                print(f"[TEST METRICS] RoutingPill - Fonte: {pill_family}, Texto: '{pill_text}' (largura={pill_text_w}px), Widget width={pill_widget_w}px")

                self.assertIn(model_id, pill_text, f"Nome completo do modelo {model_id} deve estar presente na pílula inferior, obtido: {pill_text}")
                self.assertNotIn("...", pill_text, "Texto da pílula não pode conter reticências")
                self.assertGreaterEqual(
                    pill_widget_w,
                    pill_text_w,
                    f"Largura disponível da pílula ({pill_widget_w}px) deve comportar a largura do texto ({pill_text_w}px) na fonte {pill_family}",
                )
        finally:
            win.close()


    def test_chat_request_payload_fixes_error_400_and_omits_cloud_authorized(self):
        """Garante que o payload enviado à API omite 'cloud_authorized' de mark_context e inclui modelo válido."""
        win = self._make_window()
        try:
            captured_requests = []

            def fake_request(path, payload=None, timeout=240):
                if path == "/v1/chat/completions" and payload:
                    captured_requests.append(payload)
                return 200, b'{"choices":[{"message":{"content":"ok"}}]}'

            with patch("mark.desktop_chat._request", side_effect=fake_request), \
                 patch("mark.desktop._request", side_effect=fake_request):
                win.input.setPlainText("Teste de conformidade de payload")
                win.send_message()
                self.app.processEvents()

            self.assertEqual(len(captured_requests), 1)
            p = captured_requests[0]
            # O modelo deve ser uma string não vazia
            self.assertIsInstance(p.get("model"), str)
            self.assertTrue(bool(p.get("model")))

            # mark_context NÃO PODE conter 'cloud_authorized' (que gerava Erro 400)
            mark_ctx = p.get("mark_context", {})
            self.assertNotIn("cloud_authorized", mark_ctx)
            # Chaves permitidas pelo backend são estritamente o subconjunto de {"session_id", "project_id", "task_id", "specialization"}
            self.assertTrue(set(mark_ctx).issubset({"session_id", "project_id", "task_id", "specialization"}))
        finally:
            win.close()

    def test_technical_footer_discrete_and_details_on_click(self):
        """Rodapé (Status BWRAP, mTLS) deve ser discreto no repouso e detalhar apenas no clique."""
        win = self._make_window()
        try:
            footer = getattr(win, "telemetry_box", None) or getattr(win, "footer_widget", None)
            self.assertIsNotNone(footer)
            # Etiquetas de status devem existir e ter manipulador de clique para detalhes
            self.assertIsNotNone(win.bwrap_status_label)
            self.assertIsNotNone(win.mtls_status_label)
            self.assertTrue(hasattr(win, "show_technical_details") or hasattr(win, "_on_bwrap_clicked"))
        finally:
            win.close()

    def test_code_block_rendering_with_header_and_copy_button(self):
        """Blocos de código devem ter fundo próprio, cabeçalho indicando a linguagem e botão de copiar."""
        from mark.desktop_theme import message_html
        raw_md = "```python\ndef hello():\n    return 'world'\n```"
        html = message_html(raw_md)
        self.assertIn("python", html.lower())
        self.assertIn("copiar", html.lower())
        self.assertIn("<pre", html)

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

    def test_main_window_redesign_parity(self):
        """MainWindow (versão estendida) deve ter a coluna de 920px, input em pílula e rodapé técnico."""
        from mark.ui.main_window import MainWindow
        win = MainWindow()
        try:
            self.assertEqual(win.chat_column_container.maximumWidth(), 920)
            self.assertIn("border-radius:", win.composer_box.styleSheet())
            self.assertIn("#00D8F6", win.send_button.styleSheet())
            self.assertTrue(hasattr(win, "show_technical_details"))
            self.assertIsNotNone(win.bwrap_status_label)
            self.assertIsNotNone(win.mtls_status_label)
        finally:
            win.close()

    def test_sidebar_fixed_engineer_conversations_and_context_actions(self):
        """Valida barra lateral fixa, criação de conversa exclusiva para Engineer, ausência de emojis e ações de renomear/excluir."""
        from mark.ui.main_window import MainWindow
        with patch.object(MainWindow, "refresh_status"):
            win = MainWindow()
        win.status_timer.stop()
        win.history_timer.stop()
        try:
            win.show()
            self.app.processEvents()

            # 1. Barra lateral fixa: visível e sem botões visíveis de esconder/alternar
            self.assertTrue(win.sidebar.isVisible())
            self.assertFalse(hasattr(win, "header_sidebar_toggle"))
            self.assertTrue(win.sidebar_toggle_button.isHidden())

            # 2. No modo Geral, botão de Nova Conversa fica oculto; no modo Engineer, fica visível
            win.open_specialized_chat("general")
            self.app.processEvents()
            self.assertTrue(win.sidebar_new_chat_btn.isHidden())
            self.assertTrue(win.new_conversation_button.isHidden())

            win.open_specialized_chat("engineer")
            self.app.processEvents()
            self.assertTrue(win.sidebar_new_chat_btn.isVisible())
            self.assertTrue(win.new_conversation_button.isVisible())

            # 3. Criar nova conversa cria exclusivamente para 'engineer' e sem emojis nos itens
            win._create_new_conversation_from_sidebar()
            self.app.processEvents()
            new_conv_id = win.current_conversation_id
            self.assertEqual(win.current_specialization, "engineer")
            self.assertNotEqual(new_conv_id, "general-main")

            for i in range(win.sidebar_conv_list.count()):
                item_text = win.sidebar_conv_list.item(i).text()
                for emoji in ("💬", "⚙️", "🎓", "🟢"):
                    self.assertNotIn(emoji, item_text)

            # 4. Renomear e excluir conversa funcionam e atualizam a lista
            self.assertTrue(win._rename_conversation(new_conv_id, "Refatoração UI 16:9"))
            texts_after_rename = [win.sidebar_conv_list.item(i).text() for i in range(win.sidebar_conv_list.count())]
            self.assertIn("Refatoração UI 16:9", texts_after_rename)

            self.assertTrue(win._delete_conversation(new_conv_id, "engineer"))
            ids_after_delete = [
                win.sidebar_conv_list.item(i).data(Qt.ItemDataRole.UserRole)[0]
                for i in range(win.sidebar_conv_list.count())
            ]
            self.assertNotIn(new_conv_id, ids_after_delete)
        finally:
            win.close()

    def test_fullscreen_16_9_visual_tokens_and_symmetry(self):
        """Valida simetria horizontal do eixo central de 920px em 1920x1080 (16:9), fonte Inter e pesos 400/500."""
        import re
        from PyQt6.QtGui import QFontInfo
        from mark.ui.main_window import MainWindow
        from mark.ui.theme_manager import ThemeManager

        for cls in (MarkWindow, MainWindow):
            with patch.object(cls, "_load_history"), patch.object(cls, "refresh_status"):
                win = cls()
            win.status_timer.stop()
            win.history_timer.stop()
            try:
                win.resize(1920, 1080)
                win.show()
                self.app.processEvents()

                # 1. Simetria horizontal do eixo central na área de conteúdo em 1920x1080
                col = win.chat_column_container
                content_area = getattr(win, "chat_page", col.parentWidget())
                self.assertEqual(col.maximumWidth(), 920)
                self.assertEqual(col.width(), 920)

                col_top_left_in_content = col.mapTo(content_area, col.rect().topLeft())
                left_dist = col_top_left_in_content.x()
                right_dist = content_area.width() - (col_top_left_in_content.x() + col.width())
                self.assertEqual(
                    left_dist,
                    right_dist,
                    f"[{cls.__name__}] Assimetria em 1920x1080: esquerda={left_dist}px, direita={right_dist}px",
                )

                # Simetria interna de scroll, composer e telemetry dentro de chat_page
                for child_name, child_w in (
                    ("scroll", win.scroll),
                    ("composer_box", win.composer_box),
                    ("telemetry_box", win.telemetry_box),
                ):
                    c_pos = child_w.mapTo(content_area, child_w.rect().topLeft())
                    c_left = c_pos.x()
                    c_right = content_area.width() - (c_pos.x() + child_w.width())
                    self.assertEqual(
                        c_left,
                        c_right,
                        f"[{cls.__name__}:{child_name}] Assimetria interna em 1920x1080: esquerda={c_left}px, direita={c_right}px",
                    )

                # 2. Validação da fonte resolvida para Inter e pesos estritos 400/500
                resolved_family = QFontInfo(win.font()).family()
                self.assertIn("Inter", resolved_family)

                target_roots = [content_area]
                for attr in ("sidebar", "sidebar_scroll", "voice_page"):
                    root_w = getattr(win, attr, None)
                    if root_w is not None:
                        target_roots.append(root_w)

                checked_widgets = [win]
                for r in target_roots:
                    checked_widgets.append(r)
                    checked_widgets.extend(r.findChildren(QWidget))

                for w in checked_widgets:
                    ss = w.styleSheet()
                    if ss:
                        weights = set(re.findall(r"font-weight:\s*(\w+)", ss))
                        self.assertTrue(
                            weights.issubset({"400", "500"}),
                            f"[{cls.__name__}:{w.objectName() or type(w).__name__}] Pesos fora de 400/500: {weights}",
                        )
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

