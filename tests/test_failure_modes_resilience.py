"""Suíte de Testes para Tratamento de Casos de Falha e Resiliência Extrema do MARK.

Cobre rigorosamente os 6 vetores de resiliência:
1. Respostas Vazias / Null Outputs (re-prompt automático, EmptyResponseError, fallback chain).
2. Conteúdo Malformado / Parsing Errors (reparo sintático de JSON, tolerant parsing, fences Markdown).
3. Timeouts e Provedor Indisponível (LLMTimeoutError, Task assíncrona QThread não bloqueante, ErrorCardWidget).
4. Estouro de Limite de Contexto (Janela deslizante com compressão preservando System Prompt e mensagem atual intactos).
5. Falhas no Processamento de Anexos (Encodings, PDFs corrompidos, DOCX inválidos, permissões negadas).
6. Recuperação e Ações Diretas na UI PyQt6 (botões de Retry e Alternar Modelo).
"""

from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from typing import Iterator
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

# Configura Qt offscreen para execução headless de testes
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from mark.cognition.brain_routing import (
    DynamicBrainRouter,
    FallbackChainExhaustedError,
    get_brain_router,
)
from mark.cognition.provider_adapter import (
    BaseLLMProvider,
    ContextWindowExceededError,
    EmptyResponseError,
    LLMError,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
    LLMTimeoutError,
    OpenAICompatibleProvider,
    ProviderAPIError,
)
from mark.cognition.providers import (
    ModelMetrics,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    OllamaProvider,
)
from mark.cognition.resilience import (
    compress_request_history,
    estimate_tokens,
    format_attachment_failure_notice,
    repair_malformed_json_string,
    tolerant_json_parse,
    tolerant_read_text_file,
)
from mark.desktop_chat import ChatMixin
from mark.desktop_helpers import _prepare_attachments
from mark.desktop_task import Task
from mark.ui.widgets import ErrorCardWidget, MessageBubbleWidget


# -----------------------------------------------------------------------------
# Mock Provider para Testes de Resiliência do ProviderAdapter
# -----------------------------------------------------------------------------

class MockResilientProvider(BaseLLMProvider):
    def __init__(
        self,
        responses: list[str | Exception] | None = None,
        provider_name: str = "mock_resilient",
        model: str = "mock-model",
        max_tokens: int = 2048,
        **kwargs,
    ):
        super().__init__(model=model, **kwargs)
        self._provider_name = provider_name
        self.responses = list(responses or [])
        self.call_history: list[LLMRequest] = []
        self._max_tokens = max_tokens

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def get_context_window(self, model: str | None = None) -> int:
        return self._max_tokens

    def completion(self, request: LLMRequest) -> LLMResponse:
        self.call_history.append(request)
        if not self.responses:
            return LLMResponse(
                content="Resposta padrão",
                model=request.model or self.model,
            )
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return LLMResponse(
            content=resp,
            model=request.model or self.model,
        )

    def stream(self, request: LLMRequest) -> Iterator[LLMStreamChunk]:
        raise NotImplementedError("Streaming não necessário neste teste.")


# =============================================================================
# 1. Testes de Respostas Vazias / Null Outputs
# =============================================================================

class TestEmptyResponseResilience(unittest.TestCase):
    """Valida o tratamento de saídas vazias ou nulas geradas por modelos."""

    def test_empty_response_triggers_automatic_reprompt_and_succeeds(self):
        """Se o LLM retornar string vazia ou espaços, dispara re-prompt e retorna a resposta válida."""
        provider = MockResilientProvider(responses=["   ", "Resposta substantiva após re-prompt."])
        req = ModelRequest(prompt="Explique o teorema de Gödel")

        resp = provider.generate(req)

        self.assertEqual(len(provider.call_history), 2)
        # O 2º request deve conter o pedido de reprompt
        self.assertIn("Forneça agora uma resposta para a solicitação anterior", provider.call_history[1].prompt)
        self.assertEqual(resp.text, "Resposta substantiva após re-prompt.")

    def test_empty_response_persistent_raises_empty_response_error(self):
        """Se o LLM persistir em retornar vazio após re-prompt, levanta EmptyResponseError."""
        provider = MockResilientProvider(responses=["", "   \n\t  "])
        req = ModelRequest(prompt="Gere um plano")

        with self.assertRaises(EmptyResponseError) as ctx:
            provider.generate(req)

        self.assertIn("Resposta vazia retornada pelo modelo", str(ctx.exception))
        self.assertEqual(len(provider.call_history), 2)

    def test_brain_routing_seamless_fallback_on_empty_response(self):
        """Se o provedor primário falhar com EmptyResponseError, o roteador ativa o próximo modelo."""
        router = DynamicBrainRouter(allow_cloud_fallback=True)

        mock_failing = MockResilientProvider(responses=["", ""], provider_name="primary_failing", model="fail-model")
        mock_backup = MockResilientProvider(responses=["Sucesso do modelo backup."], provider_name="backup_prov", model="backup-model")

        with patch.object(router, "resolve_chain", return_value=[("primary_failing", "fail-model"), ("backup_prov", "backup-model")]), \
             patch.object(router, "get_provider_adapter") as mock_get_adapter:

            def adapter_side_effect(p_name, m_name):
                if p_name == "primary_failing":
                    return mock_failing
                return mock_backup

            mock_get_adapter.side_effect = adapter_side_effect

            req = ModelRequest(prompt="Tarefa urgente")
            res = router.execute_with_fallback(req)

            self.assertEqual(res.text, "Sucesso do modelo backup.")

    def test_ollama_provider_handles_none_content_gracefully(self):
        """OllamaProviderAdapter trata retorno nulo sem levantar AttributeError e recupera via reprompt."""
        from mark.cognition.provider_adapter import OllamaProviderAdapter
        adapter = OllamaProviderAdapter(model="llama3:8b", base_url="http://127.0.0.1:11434")

        fake_payload_empty = {
            "choices": [{"message": {"role": "assistant", "content": None}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 0},
        }
        fake_payload_ok = {
            "choices": [{"message": {"role": "assistant", "content": "Resposta após retorno nulo"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        mock_resp_1 = MagicMock()
        mock_resp_1.read.return_value = json.dumps(fake_payload_empty).encode("utf-8")
        mock_resp_1.__enter__.return_value = mock_resp_1

        mock_resp_2 = MagicMock()
        mock_resp_2.read.return_value = json.dumps(fake_payload_ok).encode("utf-8")
        mock_resp_2.__enter__.return_value = mock_resp_2

        with patch("urllib.request.urlopen", side_effect=[mock_resp_1, mock_resp_2]):
            req = ModelRequest(prompt="Olá Ollama")
            resp = adapter.generate(req)
            self.assertEqual(resp.text, "Resposta após retorno nulo")




# =============================================================================
# 2. Testes de Conteúdo Malformado e Parsing Tolerante
# =============================================================================

class TestMalformedContentAndJsonResilience(unittest.TestCase):
    """Valida heurísticas de reparo e parsing tolerante de JSON/Markdown."""

    def test_repair_markdown_code_fences(self):
        raw = "Aqui está o resultado:\n```json\n{\"status\": \"ok\", \"code\": 200}\n```\nObrigado!"
        parsed = tolerant_json_parse(raw)
        self.assertEqual(parsed, {"status": "ok", "code": 200})

    def test_repair_unclosed_braces_and_brackets(self):
        truncated = '{"action": "deploy", "services": ["auth", "database"'
        repaired = repair_malformed_json_string(truncated)
        parsed = json.loads(repaired)
        self.assertEqual(parsed.get("action"), "deploy")
        self.assertIn("auth", parsed.get("services", []))

    def test_repair_trailing_commas(self):
        malformed = '{"title": "MARK", "tags": ["ia", "resiliencia",], "valid": true,}'
        parsed = tolerant_json_parse(malformed)
        self.assertEqual(parsed["title"], "MARK")
        self.assertEqual(parsed["valid"], True)
        self.assertEqual(len(parsed["tags"]), 2)

    def test_repair_python_literals_to_json(self):
        py_dict = '{"running": True, "failed": False, "context": None, "count": 42}'
        parsed = tolerant_json_parse(py_dict)
        self.assertIs(parsed["running"], True)
        self.assertIs(parsed["failed"], False)
        self.assertIsNone(parsed["context"])
        self.assertEqual(parsed["count"], 42)

    def test_repair_single_quotes(self):
        single_quoted = "{'user_id': 'u123', 'status': 'active'}"
        parsed = tolerant_json_parse(single_quoted)
        self.assertEqual(parsed["user_id"], "u123")
        self.assertEqual(parsed["status"], "active")

    def test_repair_unclosed_quotes(self):
        unclosed = '{"message": "Este texto foi cortado no meio'
        parsed = tolerant_json_parse(unclosed)
        self.assertIn("message", parsed)
        self.assertTrue(parsed["message"].startswith("Este texto foi cortado"))

    def test_tolerant_json_parse_fallback_to_regex_extraction(self):
        corrupted = 'Texto antes: [PROVEDOR_LOG] id: 9999, model: "deepseek-r1", score: 98, status: "healthy" fim do log.'
        parsed = tolerant_json_parse(corrupted, default={"fallback": True})
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed.get("id"), 9999)
        self.assertEqual(parsed.get("model"), "deepseek-r1")
        self.assertEqual(parsed.get("status"), "healthy")

    def test_provider_structured_json_output_uses_tolerant_parsing(self):
        """Quando output_format='json', o ProviderAdapter não quebra em JSON truncado."""
        truncated_json = '{"analysis": "Resumo executivo", "confidence": 0.95'
        provider = MockResilientProvider(responses=[truncated_json])
        req = ModelRequest(prompt="Retorne JSON", output_format="json")

        resp = provider.generate(req)
        # O text retornado foi higienizado via tolerant parsing e é um JSON válido
        parsed = json.loads(resp.text)
        self.assertEqual(parsed.get("analysis"), "Resumo executivo")
        self.assertEqual(parsed.get("confidence"), 0.95)


# =============================================================================
# 3. Testes de Timeouts e Trabalhador Assíncrono (QThread / UI)
# =============================================================================

class DummyDesktopChat(ChatMixin, QWidget):
    """Widget de teste simulando a interface gráfica para validar a captura de falhas."""

    def __init__(self):
        super().__init__()
        self.empty_state = QLabel("Empty State")
        self.chat_layout = QVBoxLayout()
        self.chat_layout.addWidget(self.empty_state)
        self.chat_layout.addStretch(1)
        self.setLayout(self.chat_layout)

        self.input = QPlainTextEdit()
        self.connection_label = QLabel("Conectado")
        self.model_selector = QComboBox()
        self.model_selector.addItems(["llama3:8b", "deepseek-r1:14b", "claude-3-5-sonnet"])
        self.current_conversation_id = "test-conv"
        self._last_user_message = "Mensagem que causou timeout"
        self._last_user_paths = []
        self.busy = False

    def _schedule_bottom_scroll(self):
        pass

    def refresh_status(self):
        pass


class TestTimeoutAndAsyncWorkerResilience(unittest.TestCase):
    """Valida o tratamento de timeouts de inferência e a integridade da thread de UI."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_provider_adapter_translates_timeout_errors(self):
        """TimeoutError e urllib socket timeouts são mapeados estritamente para LLMTimeoutError."""
        provider = OpenAICompatibleProvider(model="gpt-4o", api_key="sk-test")

        # Testa TimeoutError nativo via _translate_error
        err_native = TimeoutError("Connection timed out")
        translated = provider._translate_error(err_native)
        self.assertIsInstance(translated, LLMTimeoutError)

        # Testa urllib URLError com string "timed out"
        err_urllib = urllib.error.URLError("Connection timed out")
        translated_urllib = provider._translate_error(err_urllib)
        self.assertIsInstance(translated_urllib, LLMTimeoutError)

    def test_desktop_task_captures_exceptions_without_crashing_or_blocking(self):
        """Task(QThread) deve capturar qualquer exceção e emitir o sinal failed de forma não-bloqueante."""
        failed_messages = []

        def failing_operation():
            raise LLMTimeoutError("Ollama timeout após 60s", provider="ollama", model="llama3:8b")

        task = Task(failing_operation)
        task.failed.connect(lambda msg: failed_messages.append(msg))

        loop = QEventLoop()
        task.finished.connect(loop.quit)

        task.start()
        loop.exec()

        self.assertEqual(len(failed_messages), 1)
        self.assertIn("Ollama timeout após 60s", failed_messages[0])

    def test_ui_error_card_generated_with_recovery_buttons(self):
        """Simula falha na UI e verifica criação de ErrorCardWidget com retry e switch model."""
        win = DummyDesktopChat()

        # Simula o fluxo de simplificação de erro
        title, body = win._simplify_error_details("LLMTimeoutError: Ollama timeout após 60s")
        self.assertEqual(title, "Tempo Limite de Resposta Excedido")
        self.assertIn("atingiu o tempo limite", body)

        retry_fired = []
        switch_fired = []
        win._append_error_card(
            error_message=body,
            title=title,
            retry_callback=lambda: retry_fired.append(True),
            switch_model_callback=lambda: switch_fired.append(True),
        )

        # Localiza o ErrorCardWidget inserido no layout
        cards = [
            win.chat_layout.itemAt(i).widget().findChild(ErrorCardWidget)
            for i in range(win.chat_layout.count() - 1)
            if win.chat_layout.itemAt(i).widget() and win.chat_layout.itemAt(i).widget().findChild(ErrorCardWidget)
        ]
        self.assertEqual(len(cards), 1)
        card = cards[0]

        # Verifica botões de recuperação
        self.assertEqual(card.title_label.text(), "Tempo Limite de Resposta Excedido")
        self.assertFalse(card.retry_button.isHidden())
        self.assertTrue(card.retry_button.isEnabled())
        self.assertFalse(card.switch_model_button.isHidden())
        self.assertTrue(card.switch_model_button.isEnabled())

        # Testa cliques nos botões emitindo os sinais e callbacks
        card.switch_model_button.click()
        self.assertTrue(switch_fired)

        card.retry_button.click()
        self.assertTrue(retry_fired)

        # Testa ação real de alternar modelo do ChatMixin
        initial_model = win.model_selector.currentText()
        win._switch_model_action()
        self.assertNotEqual(win.model_selector.currentText(), initial_model)
        self.assertEqual(win.model_selector.currentText(), "deepseek-r1:14b")

        # Testa método _retry_last_message delegando para send_message
        win._last_user_message = "consulta anterior"
        with patch.object(win, "send_message") as mock_send:
            win._retry_last_message()
            mock_send.assert_called_once_with("consulta anterior")



# =============================================================================
# 4. Testes de Context Window Overflow e Compressão por Janela Deslizante
# =============================================================================

class TestContextWindowOverflowAndSlidingWindow(unittest.TestCase):
    """Valida a estimativa prévia de tokens e compressão inteligente de histórico."""

    def test_estimate_tokens_accuracy(self):
        text = "O MARK é um assistente executivo altamente resiliente e seguro."
        count = estimate_tokens(text)
        self.assertGreater(count, 5)
        self.assertLess(count, 30)
        self.assertEqual(estimate_tokens(""), 0)

    def test_sliding_window_compresses_history_preserving_system_and_current_prompt(self):
        """A compressão de histórico descarta os turnos mais antigos mantendo System Prompt e Mensagem Atual intactos."""
        system_prompt = "Você é o MARK, assistente executivo em Rust/Python."
        current_prompt = "Esta é a solicitação mais recente do usuário."

        # Cria um histórico longo com múltiplos turnos com texto suficiente para ultrapassar o limite
        long_history = []
        for i in range(10):
            long_history.append({"role": "user", "content": f"Turno {i}: " + ("palavra " * 50)})
            long_history.append({"role": "assistant", "content": f"Resposta {i}: " + ("palavra " * 50)})

        max_context = 600  # Janela apertada para forçar compressão
        compressed_msgs, was_compressed = compress_request_history(
            messages=long_history,
            system_prompt=system_prompt,
            current_prompt=current_prompt,
            max_context=max_context,
            reserve_tokens=100,
        )

        self.assertTrue(was_compressed)
        self.assertLess(len(compressed_msgs), len(long_history))
        # O primeiro item deve ser o aviso de compressão
        self.assertIn("Histórico anterior comprimido", compressed_msgs[0]["content"])

        # O último turno preservado deve ser um dos turnos mais recentes
        self.assertIn("Turno", compressed_msgs[-2]["content"] if len(compressed_msgs) > 1 else compressed_msgs[-1]["content"])

    def test_provider_adapter_check_context_window_auto_compresses(self):
        """Ao chamar check_context_window, comprime automaticamente o histórico antes de levantar erro."""
        provider = MockResilientProvider(max_tokens=200)

        history = [
            LLMMessage(role="user", content="Turno antigo " + ("palavra " * 80)),
            LLMMessage(role="assistant", content="Resposta antiga " + ("palavra " * 80)),
            LLMMessage(role="user", content="Turno recente: " + ("palavra " * 80)),
        ]

        req = LLMRequest(
            prompt="Minha pergunta atual curta",
            system="Você é o MARK",
            messages=history,
        )

        # Não deve levantar ContextWindowExceededError porque o histórico foi comprimido
        provider.check_context_window(req)
        # O número de mensagens no request foi reduzido
        self.assertLess(len(req.messages), len(history))

    def test_context_window_exceeded_on_irreducible_prompt(self):
        """Se o System Prompt + Mensagem Atual sozinhos excederem a capacidade do modelo, lança erro com aviso claro."""
        provider = MockResilientProvider(max_tokens=100)

        huge_prompt = "palavra " * 500  # Muito acima de 100 tokens
        req = LLMRequest(prompt=huge_prompt, system="System Prompt")

        with self.assertRaises(ContextWindowExceededError) as ctx:
            provider.check_context_window(req)

        self.assertIn("limite máximo", str(ctx.exception))


# =============================================================================
# 5. Testes de Falhas no Processamento de Anexos
# =============================================================================

class TestAttachmentProcessingResilience(unittest.TestCase):
    """Valida a leitura e processamento seguro e gracioso de arquivos anexos."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tolerant_read_text_file_with_non_utf8_encodings(self):
        """Lê arquivo com encoding Latin-1 sem estourar UnicodeDecodeError."""
        file_path = self.base_path / "latin1.txt"
        file_path.write_bytes("Relatório de Ações: Ação e Configuração".encode("latin-1"))

        content, err = tolerant_read_text_file(file_path)
        self.assertIsNone(err)
        self.assertIn("Relatório de Ações", content)

    def test_prepare_attachments_handles_inaccessible_file(self):
        """Arquivo com permissão negada (000) retorna aviso gracioso sem crashar."""
        file_path = self.base_path / "locked.txt"
        file_path.write_text("Confidencial", encoding="utf-8")
        try:
            file_path.chmod(0o000)
            if not os.access(file_path, os.R_OK):
                res = _prepare_attachments([file_path])
                self.assertEqual(len(res), 1)
                self.assertTrue(res[0].get("error"))
                self.assertIn("permissão negada", res[0]["text"].lower())
        finally:
            file_path.chmod(0o600)

    def test_prepare_attachments_handles_corrupt_pdf(self):
        """PDF corrompido com bytes arbitrários retorna aviso formatado sem interromper o fluxo."""
        corrupt_pdf = self.base_path / "corrupt.pdf"
        corrupt_pdf.write_bytes(b"%PDF-1.4\nCorrompido\x00\xff\xfe\x00Invalido")

        # Mock de falha no pdftotext para garantir determinismo em qualquer ambiente
        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.stderr = "Syntax Error: Couldn't read xref table"
            mock_run.return_value = mock_proc

            res = _prepare_attachments([corrupt_pdf])
            self.assertEqual(len(res), 1)
            self.assertTrue(res[0].get("error"))
            self.assertIn("PDF corrompido ou protegido", res[0]["text"])

    def test_prepare_attachments_handles_corrupt_docx(self):
        """Arquivo DOCX com zip corrompido retorna aviso formatado sem exceção fatal."""
        corrupt_docx = self.base_path / "document.docx"
        corrupt_docx.write_bytes(b"PK\x03\x04EsteNaoEUmZipValido")

        res = _prepare_attachments([corrupt_docx])
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0].get("error"))
        self.assertIn("DOCX corrompido ou inválido", res[0]["text"])

    def test_prepare_attachments_handles_unsupported_extensions(self):
        """Extensões não suportadas (ex: .exe, .bin) retornam aviso gracioso."""
        binary_file = self.base_path / "app.exe"
        binary_file.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00")

        res = _prepare_attachments([binary_file])
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0].get("error"))
        self.assertIn("formato não suportado", res[0]["text"])

    def test_prepare_attachments_partial_failure_does_not_abort_pipeline(self):
        """Em múltiplos anexos (1 válido, 1 corrompido), o anexo válido é lido e o corrompido recebe aviso."""
        valid_file = self.base_path / "notes.txt"
        valid_file.write_text("Anotações da reunião de segurança", encoding="utf-8")

        corrupt_docx = self.base_path / "damaged.docx"
        corrupt_docx.write_bytes(b"LixoBinario")

        res = _prepare_attachments([valid_file, corrupt_docx])
        self.assertEqual(len(res), 2)

        # Arquivo válido
        self.assertEqual(res[0]["name"], "notes.txt")
        self.assertIn("Anotações da reunião", res[0]["text"])
        self.assertFalse(res[0].get("error", False))

        # Arquivo corrompido
        self.assertEqual(res[1]["name"], "damaged.docx")
        self.assertTrue(res[1].get("error"))
        self.assertIn("DOCX corrompido", res[1]["text"])


if __name__ == "__main__":
    unittest.main()
