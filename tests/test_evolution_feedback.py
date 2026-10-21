"""Suíte de testes para Feedback do Usuário, Preparação de Datasets e Decisão Baseada em Evidências."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from mark.evolution.dataset_prep import DatasetSanitizer, SanitizationReport
from mark.evolution.evidence_decision import (
    DecisionAction,
    EvidenceDecisionEngine,
    EvidenceReport,
)
from mark.evolution.feedback import (
    FailureCategory,
    FeedbackCollector,
    FeedbackRating,
)


class TestEvolutionFeedbackAndEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_feedback.sqlite3"
        self.collector = FeedbackCollector(db_path=self.db_path)

    def tearDown(self):
        self.collector.close()
        self.tmp_dir.cleanup()

    # -------------------------------------------------------------------------
    # Teste 1: Coleta e categorização de feedback em banco isolado
    # -------------------------------------------------------------------------
    def test_feedback_collection_and_failure_patterns(self):
        # 1. Feedback positivo
        e1 = self.collector.record_feedback(
            prompt="Como compilar Rust?",
            response="Execute cargo build.",
            rating=FeedbackRating.POSITIVE,
            model="qwen2.5:3b",
        )
        self.assertEqual(e1.rating, 1)

        # 2. Feedbacks negativos com falhas categorizadas
        self.collector.record_feedback(
            prompt="Quantos habitantes tem Marte?",
            response="Marte tem 2 milhões de habitantes segundo o censo de 2024.",
            rating=FeedbackRating.NEGATIVE,
            category=FailureCategory.HALLUCINATION,
            correction_text="Marte não possui habitantes humanos conhecidos.",
            model="qwen2.5:3b",
        )
        self.collector.record_feedback(
            prompt="Qual a velocidade da luz?",
            response="Aproximadamente 500 km/h.",
            rating=FeedbackRating.NEGATIVE,
            category=FailureCategory.HALLUCINATION,
            correction_text="A velocidade da luz no vácuo é aproximadamente 300.000 km/s.",
            model="qwen2.5:3b",
        )
        self.collector.record_feedback(
            prompt="Resuma o documento [doc1]",
            response="O documento fala sobre economia [doc1:trecho inexistente].",
            rating=FeedbackRating.NEGATIVE,
            category=FailureCategory.UNANCHORED_CITATION,
            model="qwen2.5:3b",
        )

        # Consulta com filtro
        hallucinations = self.collector.list_feedback(category=FailureCategory.HALLUCINATION)
        self.assertEqual(len(hallucinations), 2)
        self.assertEqual(hallucinations[0].category, "HALLUCINATION")

        # Padrões de falha agregados
        patterns = self.collector.get_failure_patterns()
        self.assertEqual(patterns["total_entries"], 4)
        self.assertEqual(patterns["failure_counts"]["HALLUCINATION"], 2)
        self.assertEqual(patterns["failure_counts"]["UNANCHORED_CITATION"], 1)
        self.assertEqual(patterns["most_frequent_failure"], "HALLUCINATION")

    # -------------------------------------------------------------------------
    # Teste 2: Validação e Higienização de Datasets (Licença, Qualidade, Duplicação, Descontaminação)
    # -------------------------------------------------------------------------
    def test_dataset_sanitization_rules(self):
        sanitizer = DatasetSanitizer(
            allowed_licenses={"USER_OWNED", "MIT", "APACHE-2.0"},
            min_prompt_chars=10,
            min_response_chars=10,
        )

        # Registra questão do MARKBench para checar descontaminação
        sanitizer.add_benchmark_holdout(
            "Implemente um algoritmo de refatoração para resolver concorrência e deadlock em Rust"
        )

        records = [
            # 1. Amostra Válida
            {
                "prompt": "Explique o funcionamento do pattern matching em Python 3.10.",
                "response": "O pattern matching usa a sintaxe match/case para desestruturar objetos.",
                "license": "USER_OWNED",
            },
            # 2. Licença restrita (GPL ou proprietária) -> Rejeitada
            {
                "prompt": "Como usar o driver proprietário no Linux?",
                "response": "Instale os cabeçalhos do kernel e o pacote binário.",
                "license": "GPL-3.0",
            },
            # 3. Baixa qualidade: muito curto -> Rejeitada
            {
                "prompt": "Oi",
                "response": "Olá!",
                "license": "MIT",
            },
            # 4. Baixa qualidade: repetição degenerativa -> Rejeitada
            {
                "prompt": "Descreva o processo de compilação em detalhes.",
                "response": "Linha repetida\n" * 12,
                "license": "APACHE-2.0",
            },
            # 5. Contaminação de Benchmark: questão que vaza o teste holdout -> Rejeitada
            {
                "prompt": "Implemente um algoritmo de refatoração para resolver concorrência e deadlock em Rust",
                "response": "Aqui está a solução do teste para memorização.",
                "license": "USER_OWNED",
            },
            # 6. Duplicata da amostra 1 -> Rejeitada
            {
                "prompt": "Explique o funcionamento do pattern matching em Python 3.10.",
                "response": "O pattern matching usa a sintaxe match/case para desestruturar objetos.",
                "license": "USER_OWNED",
            },
        ]

        accepted, report = sanitizer.sanitize_records(records)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(report.total_input, 6)
        self.assertEqual(report.accepted_count, 1)
        self.assertEqual(report.discarded_count, 5)

        reasons = report.discarded_by_reason
        self.assertIn("LICENSE_RESTRICTED", reasons)
        self.assertIn("LOW_QUALITY", reasons)
        self.assertIn("BENCHMARK_CONTAMINATION", reasons)
        self.assertIn("DUPLICATE", reasons)

    # -------------------------------------------------------------------------
    # Teste 3: Higienização de arquivo JSONL
    # -------------------------------------------------------------------------
    def test_sanitize_jsonl_file_roundtrip(self):
        sanitizer = DatasetSanitizer()
        input_file = Path(self.tmp_dir.name) / "raw.jsonl"
        output_file = Path(self.tmp_dir.name) / "sanitized.jsonl"

        with input_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"prompt": "Explique o protocolo HTTP/3 com QUIC.", "response": "HTTP/3 usa UDP com protocolo QUIC para reduzir RTT.", "license": "MIT"}) + "\n")
            f.write(json.dumps({"prompt": "Apenas teste", "response": "Curto", "license": "MIT"}) + "\n")

        report = sanitizer.sanitize_jsonl_file(input_file, output_file)
        self.assertEqual(report.accepted_count, 1)
        self.assertEqual(report.discarded_count, 1)
        self.assertTrue(output_file.exists())

        with output_file.open("r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("HTTP/3", lines[0]["prompt"])

    # -------------------------------------------------------------------------
    # Teste 4: Decisão Baseada em Evidências para Fine-Tuning
    # -------------------------------------------------------------------------
    def test_evidence_decision_engine_strict_rules(self):
        engine = EvidenceDecisionEngine(
            min_prompt_variations=3,
            min_rag_attempts=2,
            benchmark_pass_threshold=0.70,
            min_recurrent_failures=5,
        )

        # Caso 1: Desempenho alto no benchmark (>= 85%) -> Fine-tuning desnecessário
        r1 = engine.evaluate_for_fine_tuning(
            task_name="sql_queries",
            benchmark_pass_rate=0.92,
            prompt_variations_tested=1,
            rag_attempts=1,
            recurrent_failures_count=0,
        )
        self.assertEqual(r1.action, DecisionAction.PERFORMANCE_SATISFACTORY)
        self.assertFalse(r1.fine_tuning_recommended)

        # Caso 2: Falha no benchmark mas pouca variação de prompt (< 3) -> Recomenda Prompt Engineering
        r2 = engine.evaluate_for_fine_tuning(
            task_name="rust_architecture",
            benchmark_pass_rate=0.55,
            prompt_variations_tested=1,
            rag_attempts=2,
            recurrent_failures_count=6,
        )
        self.assertEqual(r2.action, DecisionAction.EXHAUST_PROMPT_ENGINEERING)
        self.assertFalse(r2.fine_tuning_recommended)

        # Caso 3: Prompt engineering esgotado (3), mas RAG não tentado (< 2) -> Recomenda RAG
        r3 = engine.evaluate_for_fine_tuning(
            task_name="rust_architecture",
            benchmark_pass_rate=0.55,
            prompt_variations_tested=3,
            rag_attempts=1,
            recurrent_failures_count=6,
        )
        self.assertEqual(r3.action, DecisionAction.ENHANCE_RAG_PIPELINE)
        self.assertFalse(r3.fine_tuning_recommended)

        # Caso 4: Prompt (3) e RAG (2) esgotados, mas sem falhas recorrentes catalogadas (< 5)
        r4 = engine.evaluate_for_fine_tuning(
            task_name="rust_architecture",
            benchmark_pass_rate=0.55,
            prompt_variations_tested=3,
            rag_attempts=2,
            recurrent_failures_count=2,
        )
        self.assertEqual(r4.action, DecisionAction.EXHAUST_PROMPT_ENGINEERING)
        self.assertFalse(r4.fine_tuning_recommended)

        # Caso 5: TODAS as evidências comprovam falha consistente: Prompt (>= 3), RAG (>= 2), Falhas (>= 5), Pass Rate (< 70%)
        r5 = engine.evaluate_for_fine_tuning(
            task_name="rust_architecture",
            benchmark_pass_rate=0.50,
            prompt_variations_tested=4,
            rag_attempts=3,
            recurrent_failures_count=8,
        )
        self.assertEqual(r5.action, DecisionAction.FINE_TUNING_JUSTIFIED)
        self.assertTrue(r5.fine_tuning_recommended)
        self.assertIn("EVIDÊNCIA MÉTRICA CONCLUSA", r5.justification)


if __name__ == "__main__":
    unittest.main()
