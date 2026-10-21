"""Suíte de testes para Roteamento Previsível, Privado por Padrão e Pre-flight Disclosure.

Valida:
1. Resolução determinística em duas fases: resolve_target() e dispatch().
2. Hierarquia estrita de inferência: Local Primário (Ollama) -> Local Secundário -> Nuvem sob autorização.
3. Trava estrita de nuvem: nenhuma API externa é invocada se allow_cloud_fallback=False, levantando LocalInferenceUnavailableError.
4. Pre-flight Target Disclosure: precisão absoluta entre a rota prevista por resolve_target() e a rota executada em dispatch().
5. Detecção proativa de payload sensível e bloqueio total de egress para a nuvem.
6. Exigência de consentimento prévio (requires_user_confirmation) para desvios em nuvem.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from mark.cognition.brain_routing import (
    DynamicBrainRouter,
    FallbackChainExhaustedError,
    LocalInferenceUnavailableError,
    PrivacyLevel,
    PrivacyPolicyViolationError,
    ProviderType,
    RoutingDecision,
    detect_sensitive_payload,
)
from mark.cognition.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from mark.cognition.provider_adapter import (
    BaseLLMProvider,
    LLMRequest,
    LLMResponse,
    OpenAICompatibleProvider,
)
from mark.cognition.providers import ModelMetrics, ModelRequest, ModelResponse
from mark.config.llm_config import AgentProfileConfig, LLMConfig, ProviderProfile


class TestPredictableRouting(unittest.TestCase):
    def setUp(self):
        self.config = LLMConfig(autoload=False)
        self.config.providers = {
            "ollama": ProviderProfile("ollama", "haervwe/qwen3-vl-4b-heretic:latest", base_url="http://127.0.0.1:11434"),
            "groq": ProviderProfile("groq", "llama-3.3-70b-versatile", base_url="https://api.groq.com/openai/v1", api_key="gsk-test"),
            "openai": ProviderProfile("openai", "gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-test"),
            "deepseek": ProviderProfile("deepseek", "deepseek-chat", base_url="https://api.deepseek.com", api_key="sk-dtest"),
            "anthropic": ProviderProfile("anthropic", "claude-3-5-sonnet-20241022", base_url="https://api.anthropic.com/v1", api_key="sk-ant"),
        }
        self.config.agents = {
            "ChatAgent": AgentProfileConfig(
                agent_name="ChatAgent",
                primary_provider="ollama",
                primary_model="haervwe/qwen3-vl-4b-heretic:latest",
                fallback_chain=[
                    ("ollama", "qwen2.5:3b"),  # Secundário local
                    ("deepseek", "deepseek-chat"),  # Cloud
                    ("groq", "llama-3.3-70b-versatile"),  # Cloud
                ],
            ),
            "StrictAgent": AgentProfileConfig(
                agent_name="StrictAgent",
                primary_provider="ollama",
                primary_model="haervwe/qwen3-vl-4b-heretic:latest",
                fallback_chain=[
                    ("openai", "gpt-4o"),
                ],
            ),
        }
        self.circuit_breaker = CircuitBreaker(name="test_cb", failure_threshold=2, recovery_timeout=60.0)

    # -------------------------------------------------------------------------
    # Teste 1: Queda do Ollama com nuvem desativada JAMAIS invoca APIs externas
    # -------------------------------------------------------------------------
    def test_step1_ollama_outage_with_cloud_disabled_invokes_no_external_apis(self):
        """Com allow_cloud_fallback=False e Ollama inoperante, nenhuma API externa é invocada."""
        router = DynamicBrainRouter(
            config=self.config,
            circuit_breaker=self.circuit_breaker,
            allow_cloud_fallback=False,  # Trava global de privacidade ativa
        )

        # Força abertura do Circuit Breaker simulando indisponibilidade do Ollama
        self.circuit_breaker.trip("Simulação de serviço Ollama offline")
        self.assertTrue(self.circuit_breaker.is_open)

        # Mocks para todos os provedores em nuvem
        with patch.object(OpenAICompatibleProvider, "completion") as mock_cloud_completion, \
             patch.object(OpenAICompatibleProvider, "generate") as mock_cloud_generate:

            # 1. Via dispatch direto
            with self.assertRaises(LocalInferenceUnavailableError) as ctx_disp:
                router.dispatch("Como criar um servidor HTTP em Rust?", agent_name="StrictAgent")
            self.assertIn("Inferência local", str(ctx_disp.exception))
            self.assertIn("indisponível", str(ctx_disp.exception))

            # 2. Via execute_with_fallback
            with self.assertRaises(LocalInferenceUnavailableError) as ctx_fall:
                router.execute_with_fallback("Explique decorators em Python", agent_name="StrictAgent")
            self.assertIn("Inferência local", str(ctx_fall.exception))

            # Prova absoluta: NENHUM byte foi enviado para a nuvem
            mock_cloud_completion.assert_not_called()
            mock_cloud_generate.assert_not_called()

    # -------------------------------------------------------------------------
    # Teste 2: Pre-flight Disclosure prevê com 100% de precisão o dispatch real
    # -------------------------------------------------------------------------
    def test_step2_resolve_target_predicts_dispatch_with_100_percent_accuracy(self):
        """resolve_target() prevê com exatidão matemática o destino e parâmetros do dispatch()."""
        router = DynamicBrainRouter(
            config=self.config,
            circuit_breaker=self.circuit_breaker,
            allow_cloud_fallback=True,
        )

        # Caso A: Estado Normal -> Rota Local Primária
        decision_local = router.resolve_target("Qual é a capital da França?", agent_name="ChatAgent")
        self.assertEqual(decision_local.provider_type, ProviderType.LOCAL_OLLAMA)
        self.assertEqual(decision_local.provider_name, "ollama")
        self.assertEqual(decision_local.model_name, "haervwe/qwen3-vl-4b-heretic:latest")
        self.assertEqual(decision_local.privacy_level, PrivacyLevel.STRICT_LOCAL)
        self.assertFalse(decision_local.requires_user_confirmation)

        with patch.object(OpenAICompatibleProvider, "completion") as mock_completion:
            mock_completion.return_value = LLMResponse(content="Paris", model="haervwe/qwen3-vl-4b-heretic:latest")
            resp = router.dispatch("Qual é a capital da França?", resolved_target=decision_local, agent_name="ChatAgent")
            self.assertEqual(resp.content, "Paris")
            # Verifica que o modelo e provedor despachados conferem 100% com a pre-flight decision
            self.assertEqual(mock_completion.call_args[0][0].model, decision_local.model_name)

        # Caso B: Seleção Explícita de Nuvem Autorizada na Sessão
        cloud_ctx = {
            "selected_provider": "deepseek",
            "selected_model": "deepseek-chat",
            "cloud_authorized": True,
        }
        decision_cloud = router.resolve_target("Analise esta arquitetura", context=cloud_ctx, agent_name="ChatAgent")
        self.assertEqual(decision_cloud.provider_type, ProviderType.CLOUD_API)
        self.assertEqual(decision_cloud.provider_name, "deepseek")
        self.assertEqual(decision_cloud.model_name, "deepseek-chat")
        self.assertEqual(decision_cloud.privacy_level, PrivacyLevel.CLOUD_AUTHORIZED)
        self.assertFalse(decision_cloud.requires_user_confirmation)
        self.assertTrue(decision_cloud.session_authorized)

        with patch.object(OpenAICompatibleProvider, "completion") as mock_cloud:
            mock_cloud.return_value = LLMResponse(content="Arquitetura robusta.", model="deepseek-chat")
            resp_cloud = router.dispatch(
                "Analise esta arquitetura",
                resolved_target=decision_cloud,
                context=cloud_ctx,
                agent_name="ChatAgent",
            )
            self.assertEqual(resp_cloud.content, "Arquitetura robusta.")
            self.assertEqual(mock_cloud.call_args[0][0].model, decision_cloud.model_name)

    # -------------------------------------------------------------------------
    # Teste 3: Detecção proativa de payload sensível e bloqueio total de egress
    # -------------------------------------------------------------------------
    def test_step3_sensitive_payload_detection_and_egress_lockdown(self):
        """Payloads sensíveis são travados estritamente no local, impedindo saída para nuvem."""
        router = DynamicBrainRouter(
            config=self.config,
            circuit_breaker=self.circuit_breaker,
            allow_cloud_fallback=True,  # Mesmo com nuvem permitida
        )

        sensitive_prompts = [
            "[CONFIDENCIAL] Relatório de fusão e aquisição com valuation confidencial",
            "[SENSÍVEL] Segredo de negócio e código proprietário interno",
            "A chave de acesso da AWS é sk-proj-1234567890abcdef1234567890",
            "Senha de produção do banco: password=SuperSecretRoot123!",
            ("-----" + "BEGIN RSA PRIVATE KEY" + "-----\nMIIEowIBAAKCAQEA0...\n-----" + "END RSA PRIVATE KEY" + "-----"),
            "Documento interno contendo CPF do usuário: 123.456.789-00",
        ]

        for prompt in sensitive_prompts:
            self.assertTrue(detect_sensitive_payload(prompt), f"Falha ao detectar payload sensível: {prompt}")

            # 1. Resolução prévia com tentativa de forçar nuvem
            decision = router.resolve_target(
                prompt,
                context={"selected_provider": "openai", "selected_model": "gpt-4o", "cloud_authorized": True},
                agent_name="ChatAgent",
            )

            # Rota para nuvem deve ter sido terminantemente rejeitada
            self.assertTrue(decision.is_sensitive)
            self.assertEqual(decision.provider_type, ProviderType.LOCAL_OLLAMA)
            self.assertEqual(decision.privacy_level, PrivacyLevel.STRICT_LOCAL)
            self.assertIn("sensíveis", decision.reason.lower())

            # 2. Se o Ollama estiver fora do ar e o payload for sensível, dispatch JAMAIS recorre à nuvem
            self.circuit_breaker.trip("Simulação de queda")
            with patch.object(OpenAICompatibleProvider, "completion") as mock_cloud:
                with self.assertRaises(LocalInferenceUnavailableError):
                    router.dispatch(prompt, resolved_target=decision)
                mock_cloud.assert_not_called()
            self.circuit_breaker.reset()

    # -------------------------------------------------------------------------
    # Teste 4: Hierarquia estrita de fallback local (Primário -> Secundário)
    # -------------------------------------------------------------------------
    def test_step4_local_fallback_hierarchy_primary_then_secondary(self):
        """Verifica a transição de rota local primária para rota local secundária."""
        router = DynamicBrainRouter(
            config=self.config,
            circuit_breaker=self.circuit_breaker,
            allow_cloud_fallback=False,
        )

        chain = router.resolve_chain("ChatAgent")
        self.assertEqual(chain[0], ("ollama", "haervwe/qwen3-vl-4b-heretic:latest"))
        self.assertEqual(chain[1], ("ollama", "qwen2.5:3b"))

        # Caso normal: resolve rota primária
        decision_primary = router.resolve_target("Mensagem comum", agent_name="ChatAgent")
        self.assertEqual(decision_primary.provider_type, ProviderType.LOCAL_OLLAMA)
        self.assertEqual(decision_primary.model_name, "haervwe/qwen3-vl-4b-heretic:latest")

    # -------------------------------------------------------------------------
    # Teste 5: Trava de autorização (requires_user_confirmation) para nuvem
    # -------------------------------------------------------------------------
    def test_step5_cloud_transition_requires_explicit_user_authorization(self):
        """Transição para nuvem sem token de sessão exige confirmação e bloqueia sem consentimento."""
        router = DynamicBrainRouter(
            config=self.config,
            circuit_breaker=self.circuit_breaker,
            allow_cloud_fallback=True,
        )
        self.circuit_breaker.trip("Ollama offline")

        # Sem autorização na sessão:
        decision_unauth = router.resolve_target("Pesquise novidades", context={}, agent_name="ChatAgent")
        self.assertEqual(decision_unauth.provider_type, ProviderType.CLOUD_API)
        self.assertTrue(decision_unauth.requires_user_confirmation)
        self.assertFalse(decision_unauth.session_authorized)

        # Tentativa de dispatch sem consentimento levanta PrivacyPolicyViolationError
        with self.assertRaises(PrivacyPolicyViolationError):
            router.dispatch("Pesquise novidades", resolved_target=decision_unauth, context={})

        # Com autorização concedida:
        auth_ctx = {"cloud_authorized": True}
        decision_auth = router.resolve_target("Pesquise novidades", context=auth_ctx, agent_name="ChatAgent")
        self.assertEqual(decision_auth.provider_type, ProviderType.CLOUD_API)
        self.assertFalse(decision_auth.requires_user_confirmation)
        self.assertTrue(decision_auth.session_authorized)

        with patch.object(OpenAICompatibleProvider, "completion") as mock_completion:
            mock_completion.return_value = LLMResponse(content="Resultado", model=decision_auth.model_name)
            resp = router.dispatch("Pesquise novidades", resolved_target=decision_auth, context=auth_ctx, agent_name="ChatAgent")
            self.assertEqual(resp.content, "Resultado")

    # -------------------------------------------------------------------------
    # Teste 6: Badges visuais de Pre-flight Target Disclosure
    # -------------------------------------------------------------------------
    def test_step6_badge_text_formatting(self):
        """Garante a formatação esperada dos rótulos e emojis no badge de status."""
        d_local = RoutingDecision(
            provider_type=ProviderType.LOCAL_OLLAMA,
            provider_name="ollama",
            model_name="qwen2.5-coder:7b",
            privacy_level=PrivacyLevel.STRICT_LOCAL,
            requires_user_confirmation=False,
        )
        self.assertEqual(d_local.badge_text, "🟢 Local (Ollama: qwen2.5-coder:7b)")

        d_cloud_auth = RoutingDecision(
            provider_type=ProviderType.CLOUD_API,
            provider_name="deepseek",
            model_name="deepseek-chat",
            privacy_level=PrivacyLevel.CLOUD_AUTHORIZED,
            requires_user_confirmation=False,
            session_authorized=True,
        )
        self.assertEqual(d_cloud_auth.badge_text, "🟡 Nuvem Autorizada (Deepseek API)")

        d_sensitive = RoutingDecision(
            provider_type=ProviderType.CLOUD_API,
            provider_name="openai",
            model_name="gpt-4o",
            privacy_level=PrivacyLevel.STRICT_LOCAL,
            requires_user_confirmation=False,
            is_sensitive=True,
        )
        self.assertEqual(d_sensitive.badge_text, "🔒 Dados Sensíveis (Bloqueio de Nuvem)")


class TestPredictableRoutingUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_step7_window_has_routing_target_badge_and_reactive_updates(self):
        """MainWindow deve possuir routing_target_badge que reage a modelos e payloads sensíveis."""
        from pathlib import Path
        import tempfile
        from mark.config.llm_config import LLMKeyVault
        from mark.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as tmp_dir:
            v_file = Path(tmp_dir) / "vault.enc"
            m_file = Path(tmp_dir) / "master.key"
            vault = LLMKeyVault(vault_file=v_file, master_key_file=m_file)
            win = MainWindow(vault=vault)
            try:
                self.assertTrue(hasattr(win, "routing_target_badge"))
                self.assertEqual(win.routing_target_badge.objectName(), "routingTargetBadge")
                # Estado inicial: Local Ollama
                badge_text = win.routing_target_badge.text()
                self.assertTrue(badge_text.startswith("🟢 Local") or "Local" in badge_text)

                # Simula digitação de payload sensível
                win.input.setPlainText("[CONFIDENCIAL] Segredo de estado e chave de produção")
                # Caso o seletor fosse Nuvem, mudaria para bloqueio
                win.model_selector._models_meta.append({
                    "id": "claude-3-5-sonnet",
                    "name": "Claude 3.5 Sonnet",
                    "provider": "anthropic",
                    "type": "Nuvem",
                    "data": "anthropic:claude-3-5-sonnet",
                    "enabled": True,
                })
                win.model_selector.addItem("Claude 3.5 Sonnet", "anthropic:claude-3-5-sonnet")
                win.model_selector.setCurrentIndex(win.model_selector.count() - 1)

                # Dispara atualização
                win._update_routing_target_badge()
                self.assertIn("Dados Sensíveis", win.routing_target_badge.text())

                # Verificação da trava de confirmação
                decision_sensitive = RoutingDecision(
                    provider_type=ProviderType.CLOUD_API,
                    provider_name="anthropic",
                    model_name="claude-3-5-sonnet",
                    privacy_level=PrivacyLevel.STRICT_LOCAL,
                    requires_user_confirmation=False,
                    is_sensitive=True,
                )
                with patch("PyQt6.QtWidgets.QMessageBox.critical") as mock_crit:
                    result = win.confirm_cloud_egress(decision_sensitive)
                    self.assertFalse(result)
                    mock_crit.assert_called_once()
            finally:
                win.close()


if __name__ == "__main__":
    unittest.main()
