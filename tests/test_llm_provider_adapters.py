"""Suíte de Testes para Provedores Agnósticos de LLM, Roteamento e Cofre de Chaves.

Valida:
1. Chamadas simuladas (mocked), parsing de respostas e streaming SSE para todos os adaptadores:
   - OpenAI / Azure OpenAI
   - Anthropic (Claude 3.5 Sonnet / Haiku)
   - Google Gemini (AI Studio / Vertex AI)
   - Groq / OpenRouter / DeepSeek / Mistral
   - Provedores Locais (Ollama / vLLM / LM Studio / Local)
2. Normalização de tokens, tratamento de erros HTTP e limites de janela de contexto.
3. Criptografia AES-256-GCM, integridade autenticada e permissões POSIX 0600 no cofre privado de chaves.
4. Carregamento de configuração via ambiente (.env), TOML e cofre privado, com higienização estrita de logs.
5. Roteamento dinâmico e execução da cadeia de fallback transparente por agente.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

from mark.cognition.brain_routing import (
    DynamicBrainRouter,
    FallbackChainExhaustedError,
    get_brain_router,
)
from mark.cognition.provider_adapter import (
    AnthropicProvider,
    AuthenticationError,
    AzureOpenAIProvider,
    BaseLLMProvider,
    ContextWindowExceededError,
    DeepSeekProvider,
    GeminiProvider,
    GroqProvider,
    LLMError,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
    LLMTimeoutError,
    LMStudioProvider,
    LocalProvider,
    MistralProvider,
    OllamaProviderAdapter,
    OpenAICompatibleProvider,
    OpenAIProvider,
    OpenRouterProvider,
    ProviderAPIError,
    RateLimitError,
    VLLMProvider,
    create_llm_provider,
    estimate_tokens,
    get_model_context_window,
)
from mark.cognition.providers import ModelMetrics, ModelRequest, ModelResponse
from mark.config.llm_config import (
    AgentProfileConfig,
    LLMConfig,
    LLMKeyVault,
    ProviderProfile,
)
from mark.logging_sanitizer import (
    clear_registered_secrets,
    register_secret_for_redaction,
    sanitize_log_text,
)
from mark.storage_security import StorageSecurityError, verify_private_path


class TestProviderAdapters(unittest.TestCase):
    """Testes unitários dos adaptadores específicos de provedores com chamadas simuladas."""

    def setUp(self):
        clear_registered_secrets()

    def tearDown(self):
        clear_registered_secrets()

    def test_context_window_and_token_estimation(self):
        """Valida estimativa de tokens e resolução de janelas de contexto."""
        self.assertGreater(estimate_tokens("Olá, como posso otimizar meu código Python hoje?"), 5)
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(get_model_context_window("gpt-4o"), 128_000)
        self.assertEqual(get_model_context_window("claude-3-5-sonnet"), 200_000)
        self.assertEqual(get_model_context_window("gemini-2.0-flash"), 1_048_576)
        self.assertEqual(get_model_context_window("qwen2.5-coder"), 32_768)

    @patch("urllib.request.urlopen")
    def test_openai_completion_and_parsing(self, mock_urlopen):
        """Valida requisição e resposta do adaptador OpenAI."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "chatcmpl-123",
            "model": "gpt-4o",
            "choices": [{"message": {"role": "assistant", "content": "Olá, sou o MARK!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        provider = OpenAIProvider(api_key="sk-test-openai-key", model="gpt-4o")
        req = LLMRequest(prompt="Quem és tu?", system="És o assistente MARK.")
        resp = provider.completion(req)

        self.assertEqual(resp.content, "Olá, sou o MARK!")
        self.assertEqual(resp.model, "gpt-4o")
        self.assertEqual(resp.prompt_tokens, 12)
        self.assertEqual(resp.completion_tokens, 8)
        self.assertEqual(resp.total_tokens, 20)

        # Verifica chamada HTTP
        sent_req = mock_urlopen.call_args[0][0]
        self.assertEqual(sent_req.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(sent_req.headers["Authorization"], "Bearer sk-test-openai-key")

    @patch("urllib.request.urlopen")
    def test_openai_streaming_sse(self, mock_urlopen):
        """Valida parsing de chunks em streaming SSE do OpenAI."""
        sse_lines = [
            b'data: {"choices": [{"delta": {"content": "Ol\xc3\xa1 "}}]}\n',
            b'data: {"choices": [{"delta": {"content": "Mundo!"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}\n',
            b"data: [DONE]\n",
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(sse_lines)
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        chunks = list(provider.stream(LLMRequest(prompt="Diga olá")))

        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].delta_text, "Olá ")
        self.assertFalse(chunks[0].is_final)
        self.assertEqual(chunks[1].delta_text, "Mundo!")
        self.assertEqual(chunks[2].finish_reason, "stop")
        self.assertTrue(chunks[2].is_final)
        self.assertEqual(chunks[2].total_tokens, 7)

    @patch("urllib.request.urlopen")
    def test_azure_openai_endpoint_and_headers(self, mock_urlopen):
        """Valida formatação de URL e autenticação via header api-key no Azure OpenAI."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Azure resposta"}}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        provider = AzureOpenAIProvider(
            model="gpt-4o",
            api_key="azure-secret-key",
            azure_endpoint="https://mycorp.openai.azure.com",
            deployment_name="mark-gpt4o",
            api_version="2024-08-01-preview",
        )
        provider.completion(LLMRequest(prompt="Olá Azure"))

        sent_req = mock_urlopen.call_args[0][0]
        self.assertTrue(sent_req.full_url.startswith("https://mycorp.openai.azure.com/openai/deployments/mark-gpt4o/chat/completions"))
        self.assertIn("api-version=2024-08-01-preview", sent_req.full_url)
        self.assertEqual(sent_req.headers["Api-key"], "azure-secret-key")

    @patch("urllib.request.urlopen")
    def test_anthropic_completion_and_schema_translation(self, mock_urlopen):
        """Valida tradução de schema de mensagens (system top-level) e resposta na Anthropic."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "Resposta do Claude 3.5 Sonnet"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 15, "output_tokens": 10},
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        provider = AnthropicProvider(api_key="sk-ant-test12345", model="claude-3-5-sonnet-20241022")
        req = LLMRequest(
            prompt="Escreva uma função Fibonacci",
            system="Apenas responda com código Python.",
            temperature=0.2,
        )
        resp = provider.completion(req)

        self.assertEqual(resp.content, "Resposta do Claude 3.5 Sonnet")
        self.assertEqual(resp.prompt_tokens, 15)
        self.assertEqual(resp.completion_tokens, 10)

        sent_req = mock_urlopen.call_args[0][0]
        self.assertEqual(sent_req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(sent_req.headers["X-api-key"], "sk-ant-test12345")
        self.assertEqual(sent_req.headers["Anthropic-version"], "2023-06-01")

        body = json.loads(sent_req.data.decode("utf-8"))
        self.assertEqual(body["system"], "Apenas responda com código Python.")
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"], "Escreva uma função Fibonacci")

    @patch("urllib.request.urlopen")
    def test_anthropic_streaming_sse(self, mock_urlopen):
        """Valida streaming de chunks com formato Anthropic content_block_delta."""
        sse_lines = [
            b'data: {"type": "message_start", "message": {"usage": {"input_tokens": 8}}}\n',
            b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "def fib(n):"}}\n',
            b'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 6}}\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(sse_lines)
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        provider = AnthropicProvider(api_key="sk-ant-key", model="claude-3-5-sonnet-20241022")
        chunks = list(provider.stream(LLMRequest(prompt="Gere fib")))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].delta_text, "def fib(n):")
        self.assertFalse(chunks[0].is_final)
        self.assertEqual(chunks[1].finish_reason, "end_turn")
        self.assertTrue(chunks[1].is_final)
        self.assertEqual(chunks[1].prompt_tokens, 8)
        self.assertEqual(chunks[1].completion_tokens, 6)

    @patch("urllib.request.urlopen")
    def test_gemini_completion_and_schema_translation(self, mock_urlopen):
        """Valida tradução de mensagens para contents e systemInstruction no Google Gemini."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{
                "content": {"parts": [{"text": "Resposta do Gemini 2.0 Flash"}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 15, "totalTokenCount": 35},
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        provider = GeminiProvider(api_key="gemini-secret-api-key", model="gemini-2.0-flash")
        req = LLMRequest(prompt="Resuma este texto", system="Instrução do sistema.")
        resp = provider.completion(req)

        self.assertEqual(resp.content, "Resposta do Gemini 2.0 Flash")
        self.assertEqual(resp.total_tokens, 35)

        sent_req = mock_urlopen.call_args[0][0]
        self.assertIn("models/gemini-2.0-flash:generateContent", sent_req.full_url)
        self.assertEqual(sent_req.headers["X-goog-api-key"], "gemini-secret-api-key")

        body = json.loads(sent_req.data.decode("utf-8"))
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "Instrução do sistema.")
        self.assertEqual(body["contents"][0]["role"], "user")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "Resuma este texto")

    def test_specialized_cloud_and_local_provider_defaults(self):
        """Valida que Groq, OpenRouter, DeepSeek, Mistral, Ollama e vLLM configuram corretamente seus endpoints."""
        groq = GroqProvider(api_key="gsk-test")
        self.assertEqual(groq.provider_name, "groq")
        self.assertEqual(groq.base_url, "https://api.groq.com/openai/v1")

        openrouter = OpenRouterProvider(api_key="sk-or-test")
        self.assertEqual(openrouter.provider_name, "openrouter")
        self.assertEqual(openrouter.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(openrouter.extra_headers["HTTP-Referer"], "https://mark-assistant.local")

        deepseek = DeepSeekProvider(api_key="sk-ds-test")
        self.assertEqual(deepseek.provider_name, "deepseek")
        self.assertEqual(deepseek.base_url, "https://api.deepseek.com")

        mistral = MistralProvider(api_key="sk-mis-test")
        self.assertEqual(mistral.provider_name, "mistral")
        self.assertEqual(mistral.base_url, "https://api.mistral.ai/v1")

        ollama = OllamaProviderAdapter()
        self.assertEqual(ollama.provider_name, "ollama")
        self.assertEqual(ollama.base_url, "http://127.0.0.1:11434/v1")

        vllm = VLLMProvider(model="meta-llama/Llama-3.1-8B-Instruct")
        self.assertEqual(vllm.provider_name, "vllm")
        self.assertEqual(vllm.base_url, "http://127.0.0.1:8000/v1")

        lmstudio = LMStudioProvider()
        self.assertEqual(lmstudio.provider_name, "lmstudio")
        self.assertEqual(lmstudio.base_url, "http://127.0.0.1:1234/v1")

    @patch("urllib.request.urlopen")
    def test_unified_error_handling(self, mock_urlopen):
        """Valida mapeamento unificado de erros HTTP 401, 429, 500 e timeout."""
        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")

        # 401 Unauthorized
        mock_urlopen.side_effect = urllib.error.HTTPError("url", 401, "Unauthorized", {}, io.BytesIO(b'{"error":{"message":"Invalid key"}}'))
        with self.assertRaises(AuthenticationError):
            provider.completion(LLMRequest(prompt="Hi"))

        # 429 Rate Limit
        mock_urlopen.side_effect = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO(b'{"error":{"message":"Rate limit exceeded"}}'))
        with self.assertRaises(RateLimitError):
            provider.completion(LLMRequest(prompt="Hi"))

        # 503 Service Unavailable
        mock_urlopen.side_effect = urllib.error.HTTPError("url", 503, "Service Unavailable", {}, io.BytesIO(b"Downstream down"))
        with self.assertRaises(ProviderAPIError):
            provider.completion(LLMRequest(prompt="Hi"))

        # Timeout
        mock_urlopen.side_effect = TimeoutError("Connection timed out")
        with self.assertRaises(LLMTimeoutError):
            provider.completion(LLMRequest(prompt="Hi"))

    def test_context_window_exceeded_guard(self):
        """Valida que prompts que excedem o limite da janela de contexto são bloqueados preventivamente."""
        provider = OpenAIProvider(api_key="sk-test", model="gpt-4")  # Janela de 8192
        huge_prompt = "palavra " * 15_000
        req = LLMRequest(prompt=huge_prompt)
        with self.assertRaises(ContextWindowExceededError):
            provider.completion(req)


class TestKeyVaultAndConfig(unittest.TestCase):
    """Testes de segurança do cofre privado de chaves e módulo LLMConfig."""

    def setUp(self):
        clear_registered_secrets()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_path = Path(self.tmp_dir.name) / "vault" / "test_keys.enc"
        self.master_key_path = Path(self.tmp_dir.name) / "vault" / ".master.key"

    def tearDown(self):
        clear_registered_secrets()
        self.tmp_dir.cleanup()

    def test_vault_encryption_decryption_and_posix_permissions(self):
        """Valida cifra AES-256-GCM, decifra e conformidade estrita com permissões 0600/0700."""
        vault = LLMKeyVault(vault_file=self.vault_path, master_key_file=self.master_key_path)

        # Grava chave
        vault.set_key("anthropic", "sk-ant-test-secret-value-12345")

        # Verifica permissões POSIX no arquivo do cofre (0600) e na pasta (0700)
        verify_private_path(self.vault_path, is_dir=False)
        verify_private_path(self.vault_path.parent, is_dir=True)

        mode_file = stat.S_IMODE(self.vault_path.stat().st_mode)
        mode_dir = stat.S_IMODE(self.vault_path.parent.stat().st_mode)
        self.assertEqual(mode_file, 0o600)
        self.assertEqual(mode_dir, 0o700)

        # Recupera chave decifrada
        retrieved = vault.get_key("anthropic")
        self.assertEqual(retrieved, "sk-ant-test-secret-value-12345")

        # Valida listagem
        self.assertEqual(vault.list_providers(), ["anthropic"])

        # Exclusão
        self.assertTrue(vault.delete_key("anthropic"))
        self.assertIsNone(vault.get_key("anthropic"))

    def test_vault_tampering_detection(self):
        """Valida que qualquer alteração de bit no texto cifrado do cofre é detectada e rejeitada."""
        vault = LLMKeyVault(vault_file=self.vault_path, master_key_file=self.master_key_path)
        vault.set_key("openai", "sk-secret-openai-valid")

        # Adulterar 1 byte do ciphertext
        raw = bytearray(self.vault_path.read_bytes())
        raw[-1] ^= 0x01
        self.vault_path.write_bytes(bytes(raw))

        with self.assertRaises(StorageSecurityError):
            vault.get_key("openai")

    def test_llm_config_loading_from_toml(self):
        """Valida carregamento de configurações estruturadas a partir de arquivo config.toml."""
        toml_file = Path(self.tmp_dir.name) / "config.toml"
        toml_content = """
        [llm]
        default_provider = "groq"
        default_model = "llama-3.3-70b-versatile"

        [llm.providers.groq]
        model = "llama-3.3-70b-versatile"
        api_key = "gsk-mocked-toml-key"

        [llm.agents.EngineerAgent]
        provider = "deepseek"
        model = "deepseek-coder"
        fallback = [["ollama", "qwen2.5-coder"]]
        """
        toml_file.write_text(toml_content, encoding="utf-8")

        vault = LLMKeyVault(vault_file=self.vault_path, master_key_file=self.master_key_path)
        cfg = LLMConfig(vault=vault, config_path=toml_file, autoload=True)

        self.assertEqual(cfg.default_provider, "groq")
        self.assertEqual(cfg.default_model, "llama-3.3-70b-versatile")

        engineer_prof = cfg.get_agent_profile("EngineerAgent")
        self.assertEqual(engineer_prof.primary_provider, "deepseek")
        self.assertEqual(engineer_prof.primary_model, "deepseek-coder")
        self.assertEqual(engineer_prof.fallback_chain, [("ollama", "qwen2.5-coder")])

    def test_leak_prevention_and_logging_sanitizer_integration(self):
        """Garante que chaves carregadas no LLMConfig ou no cofre são mascaradas e nunca vazam em logs."""
        vault = LLMKeyVault(vault_file=self.vault_path, master_key_file=self.master_key_path)
        cfg = LLMConfig(vault=vault, config_path=self.vault_path, autoload=False)

        secret_key = "sk-super-secret-unique-key-998877"
        cfg.set_provider_key("openai", secret_key, save_to_vault=True)

        # 1. String e repr nunca expõem a chave completa
        self.assertNotIn(secret_key, repr(cfg))
        self.assertNotIn(secret_key, repr(cfg.get_provider("openai")))

        # 2. Dicionário seguro substitui por [REDACTED]
        safe_dict = cfg.to_dict(safe=True)
        self.assertEqual(safe_dict["providers"]["openai"]["api_key"], "[REDACTED]")

        # 3. Integração com logging_sanitizer: a chave bruta no texto de log é automaticamente redigida
        log_message = f"Erro na chamada do modelo usando {secret_key} no endpoint"
        sanitized = sanitize_log_text(log_message)
        self.assertNotIn(secret_key, sanitized)
        self.assertIn("[REDACTED_API_KEY]", sanitized)


class TestDynamicBrainRouter(unittest.TestCase):
    """Testes do DynamicBrainRouter, perfis de agentes e cadeia de fallback transparente."""

    def setUp(self):
        clear_registered_secrets()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_path = Path(self.tmp_dir.name) / "vault" / "router_keys.enc"
        self.vault = LLMKeyVault(vault_file=self.vault_path, master_key_file=Path(self.tmp_dir.name) / ".key")
        self.cfg = LLMConfig(vault=self.vault, autoload=False)
        self.router = DynamicBrainRouter(self.cfg)

    def tearDown(self):
        clear_registered_secrets()
        self.tmp_dir.cleanup()

    def test_agent_profiles_definition(self):
        """Valida que cada agente possui seu perfil e modelo prioritário."""
        engineer = self.router.get_agent_profile("EngineerAgent")
        self.assertEqual(engineer.primary_provider, "anthropic")
        self.assertEqual(engineer.primary_model, "claude-3-5-sonnet-20241022")
        self.assertIn(("openai", "gpt-4o"), engineer.fallback_chain)

        sentinel = self.router.get_agent_profile("SentinelAgent")
        self.assertEqual(sentinel.primary_provider, "ollama")
        self.assertTrue("heretic" in sentinel.primary_model.lower() or "qwen" in sentinel.primary_model.lower())

    @patch.object(OpenAICompatibleProvider, "completion")
    def test_fallback_chain_triggers_on_rate_limit(self, mock_completion):
        """Valida que quando o provedor primário falha com 429, o roteador redireciona para o secundário."""
        # Configura agente de teste com cadeia primário -> secundário
        self.cfg.set_agent_profile(
            "TestAgent",
            primary_provider="openai",
            primary_model="gpt-4o",
            fallback_chain=[("ollama", "qwen2.5:3b")],
        )

        # 1ª chamada (OpenAI) falha com RateLimitError; 2ª chamada (Ollama) tem sucesso
        mock_completion.side_effect = [
            RateLimitError("Limite de cota atingido na OpenAI (429)", provider="openai", model="gpt-4o"),
            LLMResponse(content="Resposta gerada pelo Ollama local", model="qwen2.5:3b"),
        ]

        resp = self.router.execute_with_fallback("Refatore o algoritmo", agent_name="TestAgent")

        self.assertEqual(resp.content, "Resposta gerada pelo Ollama local")
        self.assertEqual(mock_completion.call_count, 2)

    @patch.object(BaseLLMProvider, "generate")
    def test_fallback_chain_annotates_model_metrics(self, mock_generate):
        """Valida anotação de fallback_from e fallback_reason em ModelResponse (compatibilidade com MARK)."""
        self.cfg.set_agent_profile(
            "EngineerAgent",
            primary_provider="anthropic",
            primary_model="claude-3-5-sonnet-20241022",
            fallback_chain=[("ollama", "R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m")],
        )

        mock_generate.side_effect = [
            LLMTimeoutError("Timeout conectando na Anthropic", provider="anthropic", model="claude-3-5-sonnet-20241022"),
            ModelResponse(
                text="def otimizar(): pass",
                metrics=ModelMetrics(
                    model="R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m",
                    latency_ms=120.0,
                    prompt_tokens=50,
                    output_tokens=20,
                    attempts=1,
                    success=True,
                ),
            ),
        ]

        model_req = ModelRequest(prompt="Escreva a função otimizar")
        result = self.router.execute_with_fallback(model_req, agent_name="EngineerAgent")

        self.assertTrue(isinstance(result, ModelResponse))
        self.assertEqual(result.text, "def otimizar(): pass")
        self.assertEqual(result.metrics.fallback_from, "anthropic:claude-3-5-sonnet-20241022")
        self.assertIn("Timeout", result.metrics.fallback_reason)
        self.assertEqual(result.metrics.attempts, 2)

    @patch.object(OpenAICompatibleProvider, "completion")
    def test_fallback_chain_exhausted_raises_error(self, mock_completion):
        """Valida que quando todos os provedores da cadeia falham, FallbackChainExhaustedError é disparada."""
        self.cfg.set_agent_profile(
            "FragileAgent",
            primary_provider="openai",
            primary_model="gpt-4o",
            fallback_chain=[("groq", "llama-3.3-70b-versatile")],
        )

        mock_completion.side_effect = [
            ProviderAPIError("Erro 500 OpenAI"),
            ProviderAPIError("Erro 500 Groq"),
        ]

        with self.assertRaises(FallbackChainExhaustedError):
            self.router.execute_with_fallback("Tarefa crítica", agent_name="FragileAgent")


if __name__ == "__main__":
    unittest.main()
