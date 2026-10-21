"""Motor de Decisão Baseada em Evidências para Fine-Tuning e Adaptação do MARK.

Diretriz Fundamental:
Nenhum processo de fine-tuning ou coleta extensiva para treino supervisionado deve
ser iniciado sem comprovação métrica prévia de que técnicas leves e não destrutivas
(Prompt Engineering, System Instructions e RAG Híbrido) falharam de forma consistente
na suíte de regressão cognitiva MARKBench.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import logging
from typing import Any

logger = logging.getLogger("mark.evolution.evidence_decision")


class DecisionAction(str, Enum):
    PERFORMANCE_SATISFACTORY = "PERFORMANCE_SATISFACTORY"
    EXHAUST_PROMPT_ENGINEERING = "EXHAUST_PROMPT_ENGINEERING"
    ENHANCE_RAG_PIPELINE = "ENHANCE_RAG_PIPELINE"
    FINE_TUNING_JUSTIFIED = "FINE_TUNING_JUSTIFIED"


@dataclass(frozen=True)
class EvidenceReport:
    action: DecisionAction
    justification: str
    fine_tuning_recommended: bool
    benchmark_pass_rate: float
    prompt_variations_tested: int
    rag_attempts: int
    recurrent_failures_count: int
    task_name: str = "general"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d


class EvidenceDecisionEngine:
    """Avaliador estrito de evidências para transição entre RAG e Fine-Tuning."""

    def __init__(
        self,
        min_prompt_variations: int = 3,
        min_rag_attempts: int = 2,
        benchmark_pass_threshold: float = 0.70,
        min_recurrent_failures: int = 5,
    ) -> None:
        self.min_prompt_variations = min_prompt_variations
        self.min_rag_attempts = min_rag_attempts
        self.benchmark_pass_threshold = benchmark_pass_threshold
        self.min_recurrent_failures = min_recurrent_failures

    def evaluate_for_fine_tuning(
        self,
        task_name: str,
        benchmark_pass_rate: float,
        prompt_variations_tested: int,
        rag_attempts: int,
        recurrent_failures_count: int = 0,
    ) -> EvidenceReport:
        """Determina se o fine-tuning é tecnicamente justificável com base em provas numéricas."""
        # 1. Se o benchmark já apresenta alto acerto, fine-tuning é totalmente desnecessário
        if benchmark_pass_rate >= 0.85:
            return EvidenceReport(
                action=DecisionAction.PERFORMANCE_SATISFACTORY,
                justification=(
                    f"Desempenho satisfatório ({benchmark_pass_rate*100:.1f}% no MARKBench). "
                    "O modelo base atual atende plenamente aos requisitos operacionais sem necessidade de ajuste de pesos."
                ),
                fine_tuning_recommended=False,
                benchmark_pass_rate=benchmark_pass_rate,
                prompt_variations_tested=prompt_variations_tested,
                rag_attempts=rag_attempts,
                recurrent_failures_count=recurrent_failures_count,
                task_name=task_name,
            )

        # 2. Se não esgotou variações de engenharia de prompt (few-shot, chain-of-thought, etc.)
        if prompt_variations_tested < self.min_prompt_variations:
            return EvidenceReport(
                action=DecisionAction.EXHAUST_PROMPT_ENGINEERING,
                justification=(
                    f"Engenharia de prompt ainda não foi esgotada ({prompt_variations_tested}/{self.min_prompt_variations} variações testadas). "
                    "Teste técnicas de decomposição de instruções, few-shot contextual e reformulação antes de considerar treinamento."
                ),
                fine_tuning_recommended=False,
                benchmark_pass_rate=benchmark_pass_rate,
                prompt_variations_tested=prompt_variations_tested,
                rag_attempts=rag_attempts,
                recurrent_failures_count=recurrent_failures_count,
                task_name=task_name,
            )

        # 3. Se não explorou RAG híbrido ou reranking de contexto para ancorar fatos
        if rag_attempts < self.min_rag_attempts:
            return EvidenceReport(
                action=DecisionAction.ENHANCE_RAG_PIPELINE,
                justification=(
                    f"Pipeline RAG não foi suficientemente explorado ({rag_attempts}/{self.min_rag_attempts} iterações). "
                    "Aplique recuperação híbrida (FTS5 + embeddings) e reranker de contexto com citações verificáveis antes de alterar pesos."
                ),
                fine_tuning_recommended=False,
                benchmark_pass_rate=benchmark_pass_rate,
                prompt_variations_tested=prompt_variations_tested,
                rag_attempts=rag_attempts,
                recurrent_failures_count=recurrent_failures_count,
                task_name=task_name,
            )

        # 4. Se não há evidência empírica de falhas recorrentes registradas no feedback
        if recurrent_failures_count < self.min_recurrent_failures:
            return EvidenceReport(
                action=DecisionAction.EXHAUST_PROMPT_ENGINEERING,
                justification=(
                    f"Volume de falhas recorrentes insuficiente ({recurrent_failures_count}/{self.min_recurrent_failures} falhas catalogadas). "
                    "É necessário consolidar mais registros de erros do usuário para isolar a deficiência cognitiva."
                ),
                fine_tuning_recommended=False,
                benchmark_pass_rate=benchmark_pass_rate,
                prompt_variations_tested=prompt_variations_tested,
                rag_attempts=rag_attempts,
                recurrent_failures_count=recurrent_failures_count,
                task_name=task_name,
            )

        # 5. Condição estrita atendida: Prompt Engineering e RAG falharam comprovadamente
        if benchmark_pass_rate < self.benchmark_pass_threshold:
            return EvidenceReport(
                action=DecisionAction.FINE_TUNING_JUSTIFIED,
                justification=(
                    f"EVIDÊNCIA MÉTRICA CONCLUSA: Prompt engineering ({prompt_variations_tested} variações) e RAG ({rag_attempts} iterações) "
                    f"falharam repetidamente na tarefa '{task_name}', mantendo acerto de {benchmark_pass_rate*100:.1f}% "
                    f"(abaixo do limiar de {self.benchmark_pass_threshold*100:.1f}%), com {recurrent_failures_count} falhas recorrentes. "
                    "Ajuste fino (fine-tuning) aprovado por evidência empírica."
                ),
                fine_tuning_recommended=True,
                benchmark_pass_rate=benchmark_pass_rate,
                prompt_variations_tested=prompt_variations_tested,
                rag_attempts=rag_attempts,
                recurrent_failures_count=recurrent_failures_count,
                task_name=task_name,
            )

        # Caso intermediário
        return EvidenceReport(
            action=DecisionAction.PERFORMANCE_SATISFACTORY,
            justification=f"Acerto de {benchmark_pass_rate*100:.1f}% acima do limiar mínimo de fine-tuning.",
            fine_tuning_recommended=False,
            benchmark_pass_rate=benchmark_pass_rate,
            prompt_variations_tested=prompt_variations_tested,
            rag_attempts=rag_attempts,
            recurrent_failures_count=recurrent_failures_count,
            task_name=task_name,
        )
