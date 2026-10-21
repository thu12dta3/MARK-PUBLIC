"""Pipeline de Preparação e Sanitização de Datasets do MARK.

Aplica validações rigorosas antes de qualquer potencial treinamento ou ajuste:
1. Verificação de Licença: Apenas licenças permissivas ou dados do próprio usuário (USER_OWNED, MIT, APACHE-2.0, CC0).
2. Filtro de Qualidade: Eliminação de ruído, repetições mecânicas e tamanho inadequado.
3. Deduplicação: Prevenção de overfit em amostras duplicadas via hashes exatos e similaridade léxica.
4. Descontaminação de Benchmarks: Bloqueia qualquer amostra que colida com conjuntos de teste/holdout do MARKBench.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Iterable

from ..cognition.storage import private_file

logger = logging.getLogger("mark.evolution.dataset_prep")

PERMISSIVE_LICENSES = {
    "USER_OWNED",
    "MIT",
    "APACHE-2.0",
    "APACHE_2.0",
    "CC0",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "PUBLIC_DOMAIN",
}


@dataclass
class SanitizationReport:
    total_input: int
    accepted_count: int
    discarded_count: int
    discarded_by_reason: dict[str, int] = field(default_factory=dict)
    discarded_items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatasetSanitizer:
    """Higienizador e validador de datasets para treinamento supervisionado."""

    def __init__(
        self,
        allowed_licenses: Iterable[str] | None = None,
        min_prompt_chars: int = 8,
        min_response_chars: int = 10,
        max_chars: int = 32_000,
        benchmark_holdouts: list[str] | None = None,
    ) -> None:
        self.allowed_licenses = set(allowed_licenses or PERMISSIVE_LICENSES)
        self.min_prompt_chars = min_prompt_chars
        self.min_response_chars = min_response_chars
        self.max_chars = max_chars
        self.holdouts: list[str] = list(benchmark_holdouts or [])
        self._seen_hashes: set[str] = set()

    def add_benchmark_holdout(self, prompt: str) -> None:
        """Registra uma questão de referência do benchmark para proteção contra contaminação."""
        clean = self._normalize_text(prompt)
        if clean and clean not in self.holdouts:
            self.holdouts.append(clean)

    def reset_deduplication(self) -> None:
        """Limpa o cache de hashes de deduplicação."""
        self._seen_hashes.clear()

    @staticmethod
    def _normalize_text(text: str) -> str:
        t = re.sub(r"\s+", " ", text.strip().lower())
        return re.sub(r"[^\w\s]", "", t)

    def check_sample(
        self,
        prompt: str,
        response: str,
        license_name: str = "USER_OWNED",
    ) -> tuple[bool, str | None]:
        """Avalia um par (prompt, response). Retorna (válido, motivo_descarte)."""
        # 1. Checagem de Licença
        norm_lic = license_name.strip().upper()
        if norm_lic not in self.allowed_licenses:
            return False, f"LICENSE_RESTRICTED: Licença '{license_name}' não é permitida para treinamento"

        # 2. Checagem de Qualidade e Tamanho
        clean_p = prompt.strip() if prompt else ""
        clean_r = response.strip() if response else ""

        if len(clean_p) < self.min_prompt_chars:
            return False, f"LOW_QUALITY: Prompt muito curto ({len(clean_p)} < {self.min_prompt_chars} caracteres)"

        if len(clean_r) < self.min_response_chars:
            return False, f"LOW_QUALITY: Resposta muito curta ({len(clean_r)} < {self.min_response_chars} caracteres)"

        if len(clean_p) + len(clean_r) > self.max_chars:
            return False, f"LOW_QUALITY: Excesso de caracteres ({len(clean_p) + len(clean_r)} > {self.max_chars})"

        # Detecção de repetição degenerativa (ex: mesma linha repetida 8+ vezes)
        lines = [l.strip() for l in clean_r.splitlines() if l.strip()]
        if len(lines) >= 8:
            unique_lines = set(lines)
            if len(unique_lines) / len(lines) < 0.35:
                return False, "LOW_QUALITY: Degeneração por repetição mecânica excessiva"

        # 3. Descontaminação contra Holdout do MARKBench
        norm_p = self._normalize_text(clean_p)
        tokens_p = set(norm_p.split())
        for holdout in self.holdouts:
            tokens_h = set(holdout.split())
            if tokens_p and tokens_h:
                overlap = len(tokens_p & tokens_h) / max(len(tokens_p), len(tokens_h))
                if overlap >= 0.85:
                    return False, "BENCHMARK_CONTAMINATION: Amostra colide com questão de teste holdout do benchmark"

        # 4. Deduplicação por Hash
        sample_hash = hashlib.sha256(f"{norm_p}|||{self._normalize_text(clean_r)}".encode()).hexdigest()
        if sample_hash in self._seen_hashes:
            return False, "DUPLICATE: Par prompt/resposta idêntico a amostra já processada"

        self._seen_hashes.add(sample_hash)
        return True, None

    def sanitize_records(
        self,
        records: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], SanitizationReport]:
        """Processa lista de dicionários contendo ao menos 'prompt' e 'response'."""
        accepted: list[dict[str, Any]] = []
        discarded_by_reason: dict[str, int] = {}
        discarded_items: list[dict[str, Any]] = []

        for idx, item in enumerate(records):
            p = str(item.get("prompt", ""))
            r = str(item.get("response", ""))
            lic = str(item.get("license", "USER_OWNED"))

            valid, reason = self.check_sample(p, r, license_name=lic)
            if valid:
                accepted.append(item)
            else:
                reason_code = reason.split(":")[0] if reason else "UNKNOWN"
                discarded_by_reason[reason_code] = discarded_by_reason.get(reason_code, 0) + 1
                discarded_items.append({
                    "index": idx,
                    "reason": reason,
                    "prompt_snippet": p[:80],
                })

        report = SanitizationReport(
            total_input=len(records),
            accepted_count=len(accepted),
            discarded_count=len(discarded_items),
            discarded_by_reason=discarded_by_reason,
            discarded_items=discarded_items,
        )
        return accepted, report

    def sanitize_jsonl_file(
        self,
        input_file: str | Path,
        output_file: str | Path,
        default_license: str = "USER_OWNED",
    ) -> SanitizationReport:
        """Lê arquivo JSONL, filtra amostras de acordo com as regras e grava JSONL sanitizado."""
        in_p = Path(input_file).resolve()
        out_p = private_file(Path(output_file).resolve())

        records: list[dict[str, Any]] = []
        with in_p.open("r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    try:
                        data = json.loads(line_str)
                        if "license" not in data:
                            data["license"] = default_license
                        records.append(data)
                    except json.JSONDecodeError:
                        pass

        accepted, report = self.sanitize_records(records)

        with out_p.open("w", encoding="utf-8") as f:
            for item in accepted:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        return report
