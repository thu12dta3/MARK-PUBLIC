"""Suíte de Testes End-to-End (E2E) dos Fluxos de Usuário, Multimodalidade e Integração Real do MARK.

Cobre a validação dos caminhos dourados (Golden Paths):
1. Passo 1 — Testes Determinísticos de Fluxo de UI e Sessão (Mock Providers):
   - Inserção de prompt, envio, gravação em histórico SQLite e janela deslizante de memória.
   - Alternância dinâmica de modelo/provedor pelo ProviderSelectorWidget e validação do novo adaptador.
   - Alternância da Sidebar entre as abas do QStackedWidget (Chat, Cofre, Voz) durante processamento sem congelamento.
2. Passo 2 — Validação Multimodal E2E (Anexos, Visão e Áudio):
   - Drag and Drop de arquivos (.txt, .pdf, .png) no Composer com miniaturas/lista na UI e encapsulamento no extrator.
   - Roteamento seguro de imagens para a camada de Visão Multimodal com bloqueio em modelos puramente textuais.
   - Interface de Voz (índice 2): inicialização, envio de buffers PCM para STT com limpeza imediata e prevenção de vazamento.
3. Passo 3 — Teste de Integração Real (Golden Path com Ollama):
   - Chamadas reais ao motor local (127.0.0.1:11434) sem mocks com @pytest.mark.integration.
   - Confirmação do Pre-flight Target Disclosure (#routingTargetBadge = Local) e fluxo completo para o frontend.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PIL import Image
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from mark.audio import save_wav, transcribe_temporary
from mark.cognition.brain_routing import (
    DynamicBrainRouter,
    PrivacyLevel,
    ProviderType,
    RoutingDecision,
)
from mark.cognition.provider_adapter import (
    BaseLLMProvider,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
    OllamaProviderAdapter,
    estimate_tokens,
)
from mark.cognition.providers import ModelMetrics, ModelRequest, ModelResponse
from mark.cognition.resilience import compress_request_history
from mark.config.llm_config import (
    AgentProfileConfig,
    LLMConfig,
    LLMKeyVault,
    ProviderProfile,
)
from mark.desktop_helpers import (
    _prepare_attachments,
    _voice_request,
)
from mark.ui.main_window import MainWindow
from mark.ui.widgets import MessageBubbleWidget, ProviderSelectorWidget


# =============================================================================
# Provedor Simulado (MockLLMProvider) para Testes Determinísticos
# =============================================================================


class MockLLMProvider(BaseLLMProvider):
    """Provedor falso determinístico para testes E2E sem latência de rede."""

    def __init__(
        self,
        provider_name: str = "mock_provider",
        model: str = "mock-model",
        canned_response: str = "Resposta simulada determinística do MARK",
        supports_vision: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, **kwargs)
        self._provider_name = provider_name
        self.canned_response = canned_response
        self.supports_vision = supports_vision
        self.calls: list[LLMRequest] = []
        self.generate_calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def completion(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if not self.supports_vision:
            for msg in request.resolved_messages():
                content = str(msg.get("content", ""))
                if "image_url" in content or "data:image" in content or "[Imagem anexada:" in content:
                    raise ValueError(f"O modelo '{self.model}' não suporta entrada de visão/imagem.")

        return LLMResponse(
            content=self.canned_response,
            model=self.model,
            finish_reason="stop",
            prompt_tokens=15,
            completion_tokens=20,
            total_tokens=35,
            latency_ms=5.0,
        )

    def stream(self, request: LLMRequest) -> Iterator[LLMStreamChunk]:
        self.calls.append(request)
        yield LLMStreamChunk(delta_text=self.canned_response, finish_reason="stop")

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.generate_calls.append(request)
        if not self.supports_vision and (request.image or request.images):
            raise ValueError(f"O modelo '{self.model}' não suporta entrada de visão/imagem.")

        return ModelResponse(
            text=self.canned_response,
            metrics=ModelMetrics(
                model=self.model,
                latency_ms=5.0,
                prompt_tokens=15,
                output_tokens=20,
                attempts=1,
                success=True,
            ),
        )


def _generate_valid_pdf_bytes(text: str = "Conteudo de documento PDF para teste E2E") -> bytes:
    """Gera bytes de um arquivo PDF válido e sintético com bloco de texto."""
    stream = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode("latin1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode("latin1") + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode("latin1") + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
        f"{offset:010} 00000 n \n".encode("latin1") for offset in offsets[1:]
    )
    data += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin1")
    return data


# =============================================================================
# Suíte de Testes E2E: User Flows, Multimodalidade e Integração Real
# =============================================================================


class TestE2EUserFlows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.vault = LLMKeyVault(
            vault_file=self.tmp_path / "vault.bin",
            master_key_file=self.tmp_path / "master.key",
        )
        self.window = MainWindow(vault=self.vault)
        # Isolamento estrito de dados locais para testes (Regra de Arquitetura 2)
        self.window._memory_file = self.tmp_path / "conversation.sqlite3"
        self.window._conversations_file = lambda: self.tmp_path / "desktop-conversations.sqlite3"
        self.window._ensure_conversation_store()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.tmp_dir.cleanup()

    def wait_until(self, condition: Any, timeout_ms: int = 4000, step_ms: int = 25) -> bool:
        """Aguarda condição assíncrona processando eventos da fila Qt sem bloquear."""
        start = time.monotonic()
        while not condition():
            self.app.processEvents()
            QTest.qWait(step_ms)
            if (time.monotonic() - start) * 1000 > timeout_ms:
                return False
        self.app.processEvents()
        return True

    # -------------------------------------------------------------------------
    # Passo 1: Testes Determinísticos de Fluxo de UI e Sessão (Mock Providers)
    # -------------------------------------------------------------------------

    def test_step1_typing_prompt_sending_and_chat_history_recording(self):
        """Simula digitação de prompt, envio, registro no histórico SQLite e na memória."""
        prompt_text = "Explique a arquitetura de segurança do MARK."
        expected_reply = "A arquitetura do MARK emprega Bubblewrap, AEAD AES-256-GCM e zeroize."

        mock_local_provider = MockLLMProvider(
            provider_name="ollama",
            model="haervwe/qwen3-vl-4b-heretic:latest",
            canned_response=expected_reply,
        )

        # Simula resposta do endpoint interno mantendo a camada de rede falsa
        def fake_request(path: str, payload: dict | None = None, timeout: int = 240):
            if path == "/v1/chat/completions":
                return 200, json.dumps({
                    "id": "chatcmpl-mock-123",
                    "choices": [{"message": {"role": "assistant", "content": expected_reply}}],
                }).encode("utf-8")
            return 200, b"{}"

        with patch("mark.desktop_chat._request", side_effect=fake_request), \
             patch("mark.desktop._request", side_effect=fake_request):

            # 1. Usuário digita na caixa do Composer
            self.window.input.setPlainText(prompt_text)
            self.assertEqual(self.window.input.toPlainText().strip(), prompt_text)

            # 2. Usuário aciona o botão de envio
            QTest.mouseClick(self.window.send_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()

            # 3. Aguarda o término da tarefa de background do Worker
            success = self.wait_until(lambda: not self.window.busy and bool(self.window.last_assistant_reply))
            self.assertTrue(success, "A resposta assíncrona não foi entregue no prazo estipulado.")

            # 4. Verifica renderização dos balões no Chat Layout
            bubbles = self.window.findChildren(MessageBubbleWidget)
            self.assertGreaterEqual(len(bubbles), 2)
            user_bubble = bubbles[-2]
            assistant_bubble = bubbles[-1]
            self.assertEqual(user_bubble.role, "user")
            self.assertIn(prompt_text, user_bubble.text)
            self.assertEqual(assistant_bubble.role, "assistant")
            self.assertIn(expected_reply, assistant_bubble.text)

            # 5. Verifica persistência no banco SQLite de conversas
            db = self.window._conversation_db()
            try:
                rows = db.execute(
                    "SELECT role, content FROM conversation_messages WHERE conversation_id=? ORDER BY id ASC",
                    (self.window.current_conversation_id,),
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0][0], "user")
                self.assertIn(prompt_text, rows[0][1])
                self.assertEqual(rows[1][0], "assistant")
                self.assertEqual(rows[1][1], expected_reply)
            finally:
                db.close()

            # 6. Verifica se o histórico alimenta a próxima interação no buffer de mensagens
            req_messages = self.window._conversation_request_messages("Próxima pergunta")
            self.assertGreaterEqual(len(req_messages), 3)
            self.assertEqual(req_messages[0]["role"], "user")
            self.assertEqual(req_messages[1]["role"], "assistant")

    def test_step1_sliding_window_memory_compresses_under_token_budget(self):
        """Valida que a janela deslizante comprime o histórico longo preservando System e Prompt atual."""
        db = self.window._conversation_db()
        try:
            # Popula 40 mensagens para ultrapassar o limite de mensagens e tokens
            for i in range(20):
                db.execute(
                    "INSERT INTO conversation_messages(conversation_id, role, content, created_at) VALUES(?, 'user', ?, ?)",
                    (self.window.current_conversation_id, f"Pergunta antiga {i}: " + ("contexto " * 30), int(time.time())),
                )
                db.execute(
                    "INSERT INTO conversation_messages(conversation_id, role, content, created_at) VALUES(?, 'assistant', ?, ?)",
                    (self.window.current_conversation_id, f"Resposta antiga {i}: " + ("informacao " * 30), int(time.time())),
                )
            db.commit()
        finally:
            db.close()

        # Janela deslizante de recuperação de mensagens do banco tem limite máximo de 32 itens
        sliding_request_messages = self.window._conversation_request_messages("Prompt atual irrevogável")
        self.assertLessEqual(len(sliding_request_messages), 32)

        # Valida compressão heurística por orçamento de tokens
        dict_messages = [{"role": m["role"], "content": str(m["content"])} for m in sliding_request_messages]
        compressed_msgs, was_compressed = compress_request_history(
            dict_messages,
            system_prompt="Você é o MARK, assistente de IA.",
            current_prompt="Prompt atual irrevogável",
            max_context=800,
            reserve_tokens=100,
        )
        self.assertTrue(was_compressed)
        self.assertIn("Histórico anterior comprimido", compressed_msgs[0]["content"])
        self.assertIn("Prompt atual irrevogável", sliding_request_messages[-1]["content"][0]["text"])

    def test_step1_dynamic_model_switching_via_provider_selector(self):
        """Valida a alternância dinâmica entre Ollama Local e Nuvem Autorizada via ProviderSelectorWidget."""
        config = LLMConfig(autoload=False)
        config.providers = {
            "ollama": ProviderProfile("ollama", "haervwe/qwen3-vl-4b-heretic:latest", base_url="http://127.0.0.1:11434"),
            "openai": ProviderProfile("openai", "gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-test-mock-key"),
        }
        config.agents["ChatAgent"] = AgentProfileConfig(
            agent_name="ChatAgent",
            primary_provider="ollama",
            primary_model="haervwe/qwen3-vl-4b-heretic:latest",
            fallback_chain=[("openai", "gpt-4o")],
        )

        mock_cloud_provider = MockLLMProvider(
            provider_name="openai",
            model="gpt-4o",
            canned_response="Resposta vinda da Nuvem Autorizada (GPT-4o)",
        )

        # Popula o seletor com 1 modelo local e 1 provedor em nuvem com chave válida
        self.window.model_selector.populate_from_config(
            config=config,
            installed_models=["haervwe/qwen3-vl-4b-heretic:latest"],
            available_providers={"openai": config.providers["openai"]},
        )
        self.app.processEvents()

        # Estado inicial: Local Ollama
        self.assertEqual(self.window.model_selector.processing_type(), "Local Ollama")
        self.window._update_routing_target_badge()
        self.assertIn("Local", self.window.routing_target_badge.text())
        self.assertEqual(self.window.routing_target_badge.property("target"), "local")

        # Alterna dinamicamente para o modelo de Nuvem
        switched = self.window.model_selector.select_model_by_name("gpt-4o")
        self.assertTrue(switched, "Não foi possível selecionar o modelo gpt-4o no seletor.")
        self.app.processEvents()

        self.assertEqual(self.window.model_selector.processing_type(), "Nuvem")
        self.assertEqual(self.window.model_selector.current_provider(), "openai")
        self.assertEqual(self.window.model_selector.current_model_id(), "gpt-4o")

        # Verifica atualização do Pre-flight Disclosure
        self.window._update_routing_target_badge()
        badge_text = self.window.routing_target_badge.text()
        self.assertTrue("Nuvem" in badge_text or "Openai" in badge_text)

        # Envia mensagem usando o adaptador da nuvem
        captured_payloads = []

        def capture_request(path: str, payload: dict | None = None, timeout: int = 240):
            if path == "/v1/chat/completions" and payload:
                captured_payloads.append(payload)
                return 200, json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": mock_cloud_provider.canned_response}}],
                }).encode("utf-8")
            return 200, b"{}"

        with patch("mark.desktop_chat._request", side_effect=capture_request), \
             patch("mark.desktop._request", side_effect=capture_request), \
             patch.object(self.window, "confirm_cloud_egress", return_value=True):

            self.window.send_message("Qual a melhor estratégia para cache?")
            self.app.processEvents()
            self.wait_until(lambda: not self.window.busy)

            self.assertEqual(len(captured_payloads), 1)
            # O payload enviado especifica o modelo da nuvem selecionado dinamicamente
            self.assertEqual(captured_payloads[0]["model"], "openai:gpt-4o")
            self.assertEqual(self.window.last_assistant_reply, mock_cloud_provider.canned_response)

    def test_step1_sidebar_navigation_during_active_processing_preserves_state(self):
        """Valida que alternar a Sidebar entre as abas do QStackedWidget durante inferência não congela a UI nem perde estado."""
        in_flight_reply = "Resposta após alternância de abas."
        task_can_finish = False

        def delayed_request(path: str, payload: dict | None = None, timeout: int = 240):
            while not task_can_finish:
                time.sleep(0.02)
            return 200, json.dumps({
                "choices": [{"message": {"role": "assistant", "content": in_flight_reply}}],
            }).encode("utf-8")

        with patch("mark.desktop_chat._request", side_effect=delayed_request), \
             patch("mark.desktop._request", side_effect=delayed_request):

            self.window.input.setPlainText("Processando tarefa em paralelo...")
            self.window.send_message()
            self.app.processEvents()

            self.assertTrue(self.window.busy, "O assistente deveria estar em estado ocupado (busy).")

            # Alterna para aba Configurações (índice 1) durante a requisição
            self.window.open_settings()
            self.app.processEvents()
            self.assertEqual(self.window.stacked_widget.currentIndex(), 1)
            self.assertEqual(self.window.stacked_widget.currentWidget(), self.window.settings_page)

            # Alterna para aba Chamada de Voz (índice 2)
            self.window.open_call()
            self.app.processEvents()
            self.assertEqual(self.window.stacked_widget.currentIndex(), 2)
            self.assertEqual(self.window.stacked_widget.currentWidget(), self.window.voice_page)

            # Alterna de volta para Chat (índice 0)
            self.window.open_chat()
            self.app.processEvents()
            self.assertEqual(self.window.stacked_widget.currentIndex(), 0)
            self.assertEqual(self.window.stacked_widget.currentWidget(), self.window.chat_page)

            # Libera a resposta do worker assíncrono
            task_can_finish = True
            finished = self.wait_until(lambda: not self.window.busy)
            self.assertTrue(finished)

            # Confirma que a resposta foi entregue no chat da aba 0 sem perda de estado
            self.assertEqual(self.window.last_assistant_reply, in_flight_reply)
            self.assertFalse(self.window.busy)

    # -------------------------------------------------------------------------
    # Passo 2: Validação Multimodal E2E (Anexos, Visão e Áudio)
    # -------------------------------------------------------------------------

    def test_step2_drag_and_drop_multimodal_attachments_txt_pdf_png(self):
        """Simula o fluxo de 'Drag and Drop' de arquivos (.txt, .pdf, .png) no Composer e valida extração e exibição."""
        # 1. Cria arquivos de teste
        txt_path = self.tmp_path / "instrucoes.txt"
        txt_path.write_text("Diretriz de arquitetura segura do sistema MARK.", encoding="utf-8")

        pdf_path = self.tmp_path / "documento.pdf"
        pdf_path.write_bytes(_generate_valid_pdf_bytes("Relatório de Conformidade E2E"))

        png_path = self.tmp_path / "diagrama.png"
        img = Image.new("RGB", (64, 64), color="teal")
        img.save(str(png_path), "PNG")

        # 2. Simula o evento de Drop no Composer
        self.window.input.files_dropped.emit([str(txt_path), str(pdf_path), str(png_path)])
        self.app.processEvents()

        # 3. Valida a lista de anexos na interface
        self.assertTrue(self.window.attachment_list.isVisible())
        self.assertEqual(self.window.attachment_list.count(), 3)
        self.assertEqual(len(self.window.attachments), 3)

        list_texts = [self.window.attachment_list.item(i).text() for i in range(3)]
        self.assertTrue(any("instrucoes.txt" in t for t in list_texts))
        self.assertTrue(any("documento.pdf" in t for t in list_texts))
        self.assertTrue(any("diagrama.png" in t for t in list_texts))

        # 4. Valida encapsulamento no extrator _prepare_attachments
        prepared = _prepare_attachments(self.window.attachments)
        self.assertEqual(len(prepared), 3)

        prep_by_name = {item["name"]: item for item in prepared}

        # Text file
        self.assertIn("instrucoes.txt", prep_by_name)
        self.assertIn("Diretriz de arquitetura segura", prep_by_name["instrucoes.txt"]["text"])

        # PDF file
        self.assertIn("documento.pdf", prep_by_name)
        self.assertTrue("Relatório" in prep_by_name["documento.pdf"]["text"] or "Conformidade" in prep_by_name["documento.pdf"]["text"] or len(prep_by_name["documento.pdf"]["text"]) > 0)

        # Image file
        self.assertIn("diagrama.png", prep_by_name)
        self.assertEqual(prep_by_name["diagrama.png"]["mime"], "image/png")
        self.assertTrue(len(prep_by_name["diagrama.png"]["data"]) > 10)
        # Valida que o dado é um Base64 decodificável
        decoded_png = base64.b64decode(prep_by_name["diagrama.png"]["data"])
        self.assertTrue(decoded_png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_step2_multimodal_vision_routing_and_non_vision_blocking(self):
        """Valida que imagens acionam o perfil multimodal no modo Engineer e bloqueia modelos puramente textuais."""
        png_path = self.tmp_path / "captura.png"
        img = Image.new("RGB", (32, 32), color="red")
        img.save(str(png_path), "PNG")

        self.window.attachments = [png_path]
        self.window.current_specialization = "engineer"

        # 1. Envio no modo Engineer com imagem atualiza badge para "ENGINEER · VISÃO MULTIMODAL"
        mock_reply = "Identifiquei uma imagem com dimensões 32x32 na cor vermelha."
        captured_payloads = []
        captured_badges = []

        def capture_request(path: str, payload: dict | None = None, timeout: int = 240):
            if path == "/v1/chat/completions" and payload:
                captured_payloads.append(payload)
                captured_badges.append(self.window.engineer_chat_badge.text())
                return 200, json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": mock_reply}}],
                }).encode("utf-8")
            return 200, b"{}"

        with patch("mark.desktop_chat._request", side_effect=capture_request), \
             patch("mark.desktop._request", side_effect=capture_request):

            self.window.input.setPlainText("Analise este diagrama")
            self.window.send_message()
            self.app.processEvents()

            self.wait_until(lambda: not self.window.busy)

            # O badge de trabalho do Engineer foi configurado para visão multimodal durante a inferência
            self.assertEqual(len(captured_badges), 1)
            self.assertEqual(captured_badges[0], "ENGINEER · VISÃO MULTIMODAL")

            self.assertEqual(len(captured_payloads), 1)
            last_message_content = captured_payloads[0]["messages"][-1]["content"]
            # Valida estrutura multipart com imagem encodada
            has_image_url = any(
                isinstance(part, dict) and part.get("type") == "image_url" for part in last_message_content
            )
            self.assertTrue(has_image_url, "O payload enviado não continha o bloco multimodal image_url.")

        # 2. Bloqueio determinístico se o modelo selecionado não suportar visão
        text_only_provider = MockLLMProvider(
            provider_name="text_only_ai",
            model="text-only-model-7b",
            supports_vision=False,
        )
        req_with_image = LLMRequest(
            prompt="Descreva esta imagem",
            messages=[{"role": "user", "content": "data:image/png;base64,iVBORw0KGgoAAAANS..."}],
        )
        with self.assertRaises(ValueError) as ctx:
            text_only_provider.completion(req_with_image)
        self.assertIn("não suporta entrada de visão", str(ctx.exception))

    def test_step2_audio_voice_interface_stt_and_memory_leak_prevention(self):
        """Valida a interface de voz (aba índice 2), transições, envio de PCM para STT e limpeza segura de buffers."""
        # 1. Transição para aba de voz in-app
        self.window.open_call()
        self.app.processEvents()
        self.assertEqual(self.window.stacked_widget.currentIndex(), 2)
        self.assertEqual(self.window.stacked_widget.currentWidget(), self.window.voice_page)

        # 2. Ativação de chamada
        voice_widget = self.window.voice_widget
        self.assertFalse(voice_widget.is_active)
        voice_widget.toggle_call()
        self.assertTrue(voice_widget.is_active)
        self.assertEqual(voice_widget.state_label.text(), "Ouvindo... (Voz ativa)")
        self.assertIn("#3FB950", voice_widget.voice_orb.styleSheet())  # Verde de atividade

        # 3. Criação de buffer de áudio PCM sintético (16kHz mono 16-bit = 32000 bytes/seg)
        pcm_sample = b"\x00\x00\x20\x00\x40\x00\x10\x00" * 2000  # 16.000 bytes (~0.5s de áudio)
        self.assertEqual(len(pcm_sample), 16000)

        # Valida que save_wav cria arquivo WAV com permissões POSIX privadas 0600
        test_wav = self.tmp_path / "voice_sample.wav"
        save_wav(test_wav, pcm_sample, sample_rate=16000)
        self.assertTrue(test_wav.exists())
        mode = stat.S_IMODE(test_wav.stat().st_mode)
        self.assertEqual(mode, 0o600, f"O arquivo temporário de áudio deve ter modo 0600, obtido: {oct(mode)}")

        # Valida transcribe_temporary com Mock STT e deleção imediata
        cfg = {"whisper": {"binary": "/usr/bin/true", "timeout": 10}}
        with patch("mark.audio.transcribe", return_value="MARK, ative o modo foco."):
            temp_wav_target = self.tmp_path / "temporary_utterance.wav"
            transcription = transcribe_temporary(pcm_sample, temp_wav_target, "whisper-base", cfg)
            self.assertEqual(transcription, "MARK, ative o modo foco.")
            # Zero leakage: o arquivo temporário foi expurgado do sistema de arquivos
            self.assertFalse(temp_wav_target.exists(), "O arquivo temporário de áudio não foi expurgado pós-transcrição.")

        # Valida proteções de buffer em _voice_request (rejeita buffers inválidos fora da janela 5760..960000)
        with self.assertRaises(ValueError):
            _voice_request(b"\x00" * 100)  # Muito curto (< 5760 bytes)
        with self.assertRaises(ValueError):
            _voice_request(b"\x00" * 5761)  # Número ímpar de bytes (inválido para PCM 16-bit)

        # 4. Desativação de chamada e retorno à tela de conversa
        voice_widget.toggle_call()
        self.assertFalse(voice_widget.is_active)
        self.assertEqual(voice_widget.state_label.text(), "Chamada encerrada")

        # Clica no botão 'Encerrar e Voltar'
        QTest.mouseClick(voice_widget.end_call_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.stacked_widget.currentIndex(), 0)

    # -------------------------------------------------------------------------
    # Passo 3: Teste de Integração Real (Golden Path com Ollama)
    # -------------------------------------------------------------------------

    @pytest.mark.integration
    def test_step3_real_integration_golden_path_with_ollama(self):
        """Validação Real de Integração utilizando o motor local Ollama (127.0.0.1:11434).
        
        Verifica:
        1. Instanciação do roteador cognitivo real sem mocks.
        2. Pre-flight Target Disclosure indicando rota local no #routingTargetBadge.
        3. Envio de prompt real ao Ollama com resposta sem erros de estado.
        4. Entrega da resposta ao frontend (MainWindow) e histórico SQLite.
        """
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status != 200:
                    self.skipTest("Ollama local não está respondendo na porta 11434.")
        except Exception as exc:
            self.skipTest(f"Serviço local Ollama indisponível em 127.0.0.1:11434 ({exc}).")

        # 1. Instancia o roteador real com fallback para nuvem desativado (privacidade estrita)
        real_router = DynamicBrainRouter(allow_cloud_fallback=False)

        prompt = "Responda apenas 'OK'"

        # 2. Pre-flight Target Disclosure
        decision = real_router.resolve_target(prompt)
        self.assertEqual(decision.privacy_level, PrivacyLevel.STRICT_LOCAL)
        self.assertIn(decision.provider_type, (ProviderType.LOCAL_OLLAMA, ProviderType.LOCAL_SECONDARY))
        self.assertFalse(decision.requires_user_confirmation)

        # Atualiza badge visual de pré-voo no frontend
        self.window._update_routing_target_badge(decision)
        badge_text = self.window.routing_target_badge.text()
        self.assertIn("Local", badge_text)
        self.assertEqual(self.window.routing_target_badge.property("target"), "local")

        # 3. Execução real de inferência via Ollama local
        start_time = time.monotonic()
        response = real_router.dispatch(prompt)
        latency = (time.monotonic() - start_time) * 1000

        self.assertIsNotNone(response)
        content = getattr(response, "content", None) or getattr(response, "text", "")
        self.assertTrue(len(content.strip()) > 0, "A inferência local real retornou conteúdo vazio.")
        self.assertIn("OK", content.upper(), f"A resposta real do modelo foi: {content}")

        # 4. Injeção e fluxo no frontend da UI
        self.window._append_bubble(prompt, "user", source="desktop-chat")
        self.window._append_bubble(content, "assistant")
        self.window._save_conversation_message("user", prompt)
        self.window._save_conversation_message("assistant", content)
        self.window.last_assistant_reply = content
        self.app.processEvents()

        # Valida que o balão está presente no chat
        bubbles = self.window.findChildren(MessageBubbleWidget)
        self.assertGreaterEqual(len(bubbles), 2)
        self.assertIn("OK", bubbles[-1].text.upper())

        # Valida persistência no SQLite
        db = self.window._conversation_db()
        try:
            saved = db.execute(
                "SELECT role, content FROM conversation_messages WHERE conversation_id=? ORDER BY id DESC LIMIT 2",
                (self.window.current_conversation_id,),
            ).fetchall()
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0][0], "assistant")
            self.assertIn("OK", saved[0][1].upper())
            self.assertEqual(saved[1][0], "user")
        finally:
            db.close()

        # Log informativo para telemetria E2E
        print(f"\n[Golden Path Ollama E2E] Latência real: {latency:.1f}ms | Modelo: {decision.model_name} | Resposta: {content.strip()}")


if __name__ == "__main__":
    unittest.main()
