"""Suíte de testes para o Circuit Breaker e Resiliência de Roteamento Cognitivo.

Valida:
1. Máquina de estados do Circuit Breaker (FECHADO -> ABERTO -> MEIO-ABERTO -> FECHADO).
2. Fail-fast quando o circuito do Ollama está ABERTO, bypassando esperas longas de timeout.
3. Fallback automático e transparente para provedores em nuvem registrados no cofre.
4. Respeito estrito aos toggles de privacidade do usuário (bloqueio de nuvem quando configurado).
5. Resiliência a limites de taxa (HTTP 429) e quedas de conexão de rede.
6. Health-check síncrono e assíncrono do serviço Ollama.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from mark.cognition.brain_routing import (
    DynamicBrainRouter,
    FallbackChainExhaustedError,
    PrivacyPolicyViolationError,
)
from mark.cognition.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from mark.cognition.provider_adapter import (
    BaseLLMProvider,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    OpenAICompatibleProvider,
    RateLimitError,
)
from mark.cognition.providers import ModelMetrics, ModelRequest, ModelResponse
from mark.config.llm_config import LLMConfig, LLMKeyVault


class TestCircuitBreakerCore(unittest.TestCase):
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(name="test_ollama", failure_threshold=3, recovery_timeout=0.2)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())
        self.assertTrue(cb.is_closed)
        self.assertFalse(cb.is_open)

    def test_trips_to_open_after_failure_threshold(self):
        cb = CircuitBreaker(name="test_ollama", failure_threshold=3, recovery_timeout=0.2)
        cb.record_failure(ConnectionRefusedError("Connection refused"))
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())

        cb.record_failure(ConnectionRefusedError("Connection refused"))
        self.assertEqual(cb.state, CircuitState.CLOSED)

        # 3ª falha atinge o limiar
        cb.record_failure(TimeoutError("Ollama timeout"))
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertTrue(cb.is_open)
        self.assertFalse(cb.can_execute())

    def test_recovery_timeout_transitions_to_half_open(self):
        cb = CircuitBreaker(name="test_ollama", failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure(RuntimeError("fail 1"))
        cb.record_failure(RuntimeError("fail 2"))
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_execute())

        # Aguarda esgotar o tempo de recuperação
        time.sleep(0.06)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        self.assertTrue(cb.can_execute())

        # Teste com sucesso transiciona para FECHADO
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.is_closed)

    def test_failure_in_half_open_reopens_immediately(self):
        cb = CircuitBreaker(name="test_ollama", failure_threshold=2, recovery_timeout=0.05)
        cb.trip("Simulação de queda")
        self.assertEqual(cb.state, CircuitState.OPEN)

        time.sleep(0.06)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        # Falha durante teste reabre o circuito imediatamente
        cb.record_failure(RuntimeError("Ollama ainda fora do ar"))
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_execute())

    def test_health_check_synchronous_success_and_failure(self):
        cb = CircuitBreaker(name="test_hc", failure_threshold=1, health_check_timeout=0.5)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            ok = cb.check_health("http://127.0.0.1:11434")
            self.assertTrue(ok)
            self.assertEqual(cb.state, CircuitState.CLOSED)

        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("Offline")):
            ok = cb.check_health("http://127.0.0.1:11434")
            self.assertFalse(ok)
            self.assertEqual(cb.state, CircuitState.OPEN)

    def test_async_health_check_non_blocking(self):
        cb = CircuitBreaker(name="test_async", failure_threshold=2, health_check_timeout=0.5)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            future = cb.async_health_check("http://127.0.0.1:11434")
            result = future.result(timeout=1.0)
            self.assertTrue(result)
            self.assertEqual(cb.state, CircuitState.CLOSED)


class TestRouterCircuitBreakerAndFallback(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_path = Path(self.tmp_dir.name) / "vault" / "router_keys.enc"
        self.vault = LLMKeyVault(vault_file=self.vault_path, master_key_file=Path(self.tmp_dir.name) / ".key")
        self.cfg = LLMConfig(vault=self.vault, autoload=False)
        self.cb = CircuitBreaker(name="ollama", failure_threshold=2, recovery_timeout=0.05)
        self.router = DynamicBrainRouter(self.cfg, circuit_breaker=self.cb, allow_cloud_fallback=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    @patch.object(OpenAICompatibleProvider, "completion")
    def test_ollama_failure_trips_circuit_and_falls_back_to_cloud(self, mock_comp):
        """Valida que quedas no Ollama acionam o Circuit Breaker e redirecionam para a nuvem cadastrada."""
        self.cfg.set_agent_profile(
            "ChatAgent",
            primary_provider="ollama",
            primary_model="haervwe/qwen3-vl-4b-heretic:latest",
            fallback_chain=[("groq", "llama-3.3-70b-versatile")],
        )

        # 1ª requisição: Ollama falha (1 falha), mas ainda tenta e faz fallback para Groq
        mock_comp.side_effect = [
            ConnectionRefusedError("Ollama 127.0.0.1:11434 offline"),
            LLMResponse(content="Resposta gerada via Groq Cloud Fallback", model="llama-3.3-70b-versatile"),
        ]

        resp1 = self.router.execute_with_fallback("Olá MARK", agent_name="ChatAgent")
        self.assertEqual(resp1.content, "Resposta gerada via Groq Cloud Fallback")
        self.assertEqual(self.cb.state, CircuitState.CLOSED)  # 1 de 2 falhas

        # 2ª requisição: Ollama falha de novo -> Circuit Breaker trip para ABERTO
        mock_comp.side_effect = [
            ConnectionRefusedError("Ollama 127.0.0.1:11434 offline"),
            LLMResponse(content="Segunda resposta via Groq", model="llama-3.3-70b-versatile"),
        ]
        resp2 = self.router.execute_with_fallback("Como vai?", agent_name="ChatAgent")
        self.assertEqual(resp2.content, "Segunda resposta via Groq")
        self.assertEqual(self.cb.state, CircuitState.OPEN)  # 2 de 2 falhas -> ABERTO!

        # 3ª requisição com circuito ABERTO: Ollama NÃO deve sequer ser chamado (fail-fast imediato)
        mock_comp.side_effect = [
            LLMResponse(content="Resposta instantânea sem timeout do Ollama", model="llama-3.3-70b-versatile"),
        ]
        resp3 = self.router.execute_with_fallback("Terceira pergunta", agent_name="ChatAgent")
        self.assertEqual(resp3.content, "Resposta instantânea sem timeout do Ollama")
        self.assertEqual(mock_comp.call_count, 5)  # 2 + 2 + 1 (apenas Groq na 3ª chamada!)

    @patch.object(OpenAICompatibleProvider, "completion")
    def test_privacy_mode_blocks_cloud_fallback_when_ollama_is_down(self, mock_comp):
        """Valida que quando allow_cloud_fallback=False, requisições não vazam para nuvem."""
        self.router.allow_cloud_fallback = False
        self.cfg.set_agent_profile(
            "PrivateAgent",
            primary_provider="ollama",
            primary_model="local-model",
            fallback_chain=[("openai", "gpt-4o")],
        )

        # Ollama offline
        mock_comp.side_effect = ConnectionRefusedError("Ollama offline")

        with self.assertRaises(FallbackChainExhaustedError) as ctx:
            self.router.execute_with_fallback("Dados ultra sensíveis", agent_name="PrivateAgent")

        # Verifica que o erro relata a rejeição pela política de privacidade
        self.assertIn("privacidade", str(ctx.exception).lower())
        # O mock_comp só foi chamado 1 vez (Ollama local); a chamada à OpenAI foi barrada antes de ocorrer!
        self.assertEqual(mock_comp.call_count, 1)

    @patch.object(OpenAICompatibleProvider, "completion")
    def test_http_429_rate_limit_resilience_in_cloud_fallback(self, mock_comp):
        """Valida resiliência a HTTP 429: chaveia suavemente entre provedores em nuvem."""
        self.cfg.set_agent_profile(
            "MultiCloudAgent",
            primary_provider="groq",
            primary_model="llama-3.3-70b-versatile",
            fallback_chain=[("deepseek", "deepseek-chat"), ("ollama", "local-model")],
        )

        mock_comp.side_effect = [
            RateLimitError("Limite de requisições excedido na Groq (HTTP 429)", provider="groq", model="llama"),
            LLMResponse(content="Sucesso via DeepSeek Chat", model="deepseek-chat"),
        ]

        resp = self.router.execute_with_fallback("Tarefa de engenharia", agent_name="MultiCloudAgent")
        self.assertEqual(resp.content, "Sucesso via DeepSeek Chat")
        self.assertEqual(mock_comp.call_count, 2)


if __name__ == "__main__":
    unittest.main()
