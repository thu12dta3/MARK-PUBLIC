"""Bateria de testes de Regressão Cognitiva do MARKBench.

Mede e valida:
1. Respostas a perguntas de referência com expectativas estritas de qualidade e citação.
2. Telemetria local isolada: Latência total, Time-To-First-Token (TTFT), estimativa de VRAM e taxa de respostas vazias.
3. Roteamento por capacidade com preservação estrita do Zero-Surprise Routing (Privacidade por Padrão).
4. Verificação de integridade de citações ancoradas [DocID:Trecho] em tarefas RAG.
"""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mark.cognition.brain_routing import (
    DynamicBrainRouter,
    PrivacyLevel,
    PrivacyPolicyViolationError,
    ProviderType,
    RoutingDecision,
    TaskComplexity,
    analyze_task_complexity,
    detect_sensitive_payload,
)
from mark.cognition.circuit_breaker import CircuitBreaker
from mark.cognition.provider_adapter import (
    BaseLLMProvider,
    LLMRequest,
    LLMResponse,
)
from mark.cognition.providers import ModelMetrics, ModelRequest, ModelResponse
from mark.cognition.telemetry import (
    CognitiveTelemetry,
    estimate_model_vram_mib,
    get_cognitive_telemetry,
)
from mark.config.llm_config import AgentProfileConfig, LLMConfig, ProviderProfile
from mark.knowledge.citation import CitationVerifier
from mark.knowledge.indexer import KnowledgeIndexer
from mark.knowledge.pipeline import RAGPipeline


class TestCognitiveRegression(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.config = LLMConfig(autoload=False)
        self.config.providers = {
            "ollama": ProviderProfile("ollama", "qwen2.5:3b", base_url="http://127.0.0.1:11434"),
            "secondary_local": ProviderProfile("ollama", "qwen2.5:7b", base_url="http://127.0.0.1:11434"),
            "groq": ProviderProfile("groq", "llama-3.3-70b-versatile", base_url="https://api.groq.com/openai/v1", api_key="gsk-test"),
            "openai": ProviderProfile("openai", "gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-test"),
        }
        self.config.agents = {
            "ChatAgent": AgentProfileConfig(
                agent_name="ChatAgent",
                primary_provider="ollama",
                primary_model="qwen2.5:3b",
                fallback_chain=[
                    ("ollama", "qwen2.5:7b"),  # Modelo secundário mais robusto
                    ("groq", "llama-3.3-70b-versatile"),  # Cloud
                ],
            ),
        }
        self.circuit_breaker = CircuitBreaker(name="bench_cb", failure_threshold=2, recovery_timeout=30.0)

    def tearDown(self):
        self.tmp_dir.cleanup()

    # -------------------------------------------------------------------------
    # Teste 1: Medição isolada de telemetria cognitiva (TTFT, VRAM, taxa vazia)
    # -------------------------------------------------------------------------
    def test_cognitive_telemetry_metrics_and_vram_estimation(self):
        """Valida estimativa matemática de VRAM e coleta de telemetria sem vazamento."""
        telem = CognitiveTelemetry()

        # 1. Estimativa de VRAM
        vram_3b = estimate_model_vram_mib("qwen2.5:3b", context_tokens=2048)
        vram_7b = estimate_model_vram_mib("qwen2.5:7b", context_tokens=2048)
        vram_8b = estimate_model_vram_mib("llama3:8b", context_tokens=4096)

        self.assertGreater(vram_7b, vram_3b)
        self.assertGreater(vram_8b, vram_7b)
        self.assertGreater(vram_3b, 1000.0)  # Pelo menos ~1.8GB para 3B com KV-cache

        # 2. Registro de inferências e cálculo de degradação
        telem.record_inference(
            model="qwen2.5:3b",
            prompt_tokens=150,
            output_tokens=300,
            latency_ms=850.0,
            ttft_ms=180.0,
            is_empty=False,
            task_type="coding",
        )
        telem.record_inference(
            model="qwen2.5:3b",
            prompt_tokens=100,
            output_tokens=0,
            latency_ms=1200.0,
            ttft_ms=1200.0,
            is_empty=True,  # Resposta vazia/degradada
            task_type="coding",
        )

        profile = telem.get_model_profile("qwen2.5:3b")
        self.assertEqual(profile["total_inferences"], 2)
        self.assertEqual(profile["empty_response_rate"], 0.5)
        self.assertAlmostEqual(profile["avg_latency_ms"], 1025.0, delta=1.0)
        self.assertAlmostEqual(profile["avg_ttft_ms"], 690.0, delta=1.0)

    # -------------------------------------------------------------------------
    # Teste 2: Análise de complexidade e Roteamento por Capacidade
    # -------------------------------------------------------------------------
    def test_task_complexity_analysis_and_capability_routing(self):
        """Garante que tarefas complexas usem modelo mais capaz quando há histórico de degradação."""
        router = DynamicBrainRouter(
            config=self.config,
            circuit_breaker=self.circuit_breaker,
            allow_cloud_fallback=True,
        )

        # 1. Classificação de complexidade
        simple_prompt = "Bom dia MARK, que horas são?"
        self.assertEqual(analyze_task_complexity(simple_prompt), TaskComplexity.LOW)

        complex_prompt = (
            "Implemente um algoritmo de refatoração para resolver concorrência "
            "e deadlock em um pool transacional assíncrono em Rust:\n"
            "```rust\nfn resolve_deadlock() -> Result<(), MutexError> {}\n```"
        )
        self.assertEqual(analyze_task_complexity(complex_prompt), TaskComplexity.HIGH)

        # 2. Roteamento padrão sem degradação: usa primário local
        decision_normal = router.resolve_target(complex_prompt)
        self.assertEqual(decision_normal.provider_type, ProviderType.LOCAL_OLLAMA)
        self.assertEqual(decision_normal.model_name, "qwen2.5:3b")
        self.assertEqual(decision_normal.task_complexity, TaskComplexity.HIGH)
        self.assertGreater(decision_normal.estimated_vram_mib, 0.0)

        # 3. Simula degradação no modelo primário via telemetria global
        telem = get_cognitive_telemetry()
        telem.record_inference(model="qwen2.5:3b", prompt_tokens=50, output_tokens=0, latency_ms=500.0, is_empty=True)
        telem.record_inference(model="qwen2.5:3b", prompt_tokens=50, output_tokens=0, latency_ms=500.0, is_empty=True)

        decision_degraded = router.resolve_target(complex_prompt)
        # Deve ter escalado para o secundário local (7b) para lidar com a alta complexidade
        self.assertEqual(decision_degraded.provider_type, ProviderType.LOCAL_SECONDARY)
        self.assertEqual(decision_degraded.model_name, "qwen2.5:7b")
        self.assertEqual(decision_degraded.privacy_level, PrivacyLevel.STRICT_LOCAL)
        self.assertFalse(decision_degraded.requires_user_confirmation)

    # -------------------------------------------------------------------------
    # Teste 3: Preservação estrita do Zero-Surprise Routing em tarefas complexas
    # -------------------------------------------------------------------------
    def test_zero_surprise_routing_with_sensitive_data_in_complex_tasks(self):
        """Mesmo com alta complexidade e degradação local, dados confidenciais NUNCA saem para nuvem."""
        router = DynamicBrainRouter(
            config=self.config,
            circuit_breaker=self.circuit_breaker,
            allow_cloud_fallback=True,
        )

        sensitive_complex_prompt = (
            "[CONFIDENCIAL] Refatore a arquitetura de segurança do sistema bancário. "
            "A chave privada é sk-abcdef1234567890abcdef1234567890. "
            "Implemente algoritmo de mitigação contra vazamento."
        )

        self.assertTrue(detect_sensitive_payload(sensitive_complex_prompt))
        self.assertEqual(analyze_task_complexity(sensitive_complex_prompt), TaskComplexity.HIGH)

        decision = router.resolve_target(sensitive_complex_prompt)
        # Jamais pode ser CLOUD_API
        self.assertNotEqual(decision.provider_type, ProviderType.CLOUD_API)
        self.assertEqual(decision.privacy_level, PrivacyLevel.STRICT_LOCAL)
        self.assertTrue(decision.is_sensitive)

    # -------------------------------------------------------------------------
    # Teste 4: Regressão RAG e Aderência a Citações Ancoradas [DocID:Trecho]
    # -------------------------------------------------------------------------
    def test_rag_verifiable_citations_anchor_fidelity(self):
        """Avalia a adesão e verificação rigorosa de citações recuperadas na base de conhecimento."""
        indexer = KnowledgeIndexer(db_path=f"{self.tmp_dir.name}/rag_bench.sqlite3")
        doc_id = indexer.index_text(
            title="Manual de Segurança MARK",
            content=(
                "A diretriz fundamental do MARK é a privacidade total por padrão. "
                "Nenhum byte de telemetria ou dado sensível deve ser enviado à nuvem sem consentimento explícito. "
                "O cofre de chaves utiliza Argon2id e criptografia autenticada AES-256-GCM."
            ),
        )

        pipeline = RAGPipeline(indexer=indexer)
        rag_ctx = pipeline.retrieve_and_format("Qual a diretriz de privacidade do MARK?")
        self.assertIn("Manual de Segurança MARK", rag_ctx.formatted_context_text)

        # 1. Resposta fiel com citação verificável
        good_response = f"O sistema prioriza a privacidade total [{doc_id}:privacidade total por padrão]."
        report_good = pipeline.verify_response(good_response, rag_ctx)
        self.assertEqual(report_good.total_citations, 1)
        self.assertEqual(report_good.verified_count, 1)
        self.assertEqual(report_good.matches[0]["status"], "VERIFIED")
        self.assertEqual(report_good.matches[0]["doc_id"], doc_id)

        # 2. Resposta com citação desancorada (DocID real mas trecho não existe)
        unanchored_response = f"O MARK envia dados para o servidor externo [{doc_id}:envia dados para o servidor externo]."
        report_unanchored = pipeline.verify_response(unanchored_response, rag_ctx)
        self.assertEqual(report_unanchored.total_citations, 1)
        self.assertEqual(report_unanchored.unanchored_count, 1)
        self.assertFalse(report_unanchored.is_strictly_anchored)
        self.assertEqual(report_unanchored.matches[0]["status"], "UNANCHORED_EXCERPT")

        # 3. Resposta com DocID alucinado que não existe no contexto
        hallucinated_response = "Segundo relatório externo [doc_inexistente_99:dados privados sao enviados]."
        report_hallucinated = pipeline.verify_response(hallucinated_response, rag_ctx)
        self.assertEqual(report_hallucinated.total_citations, 1)
        self.assertEqual(report_hallucinated.hallucinated_count, 1)
        self.assertFalse(report_hallucinated.is_strictly_anchored)
        self.assertEqual(report_hallucinated.matches[0]["status"], "HALLUCINATED_DOC")


if __name__ == "__main__":
    unittest.main()
