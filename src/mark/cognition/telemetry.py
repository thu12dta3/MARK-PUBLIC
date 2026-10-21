"""Telemetria Cognitiva Local e Isolada do MARK (Latência, TTFT, VRAM e Taxa de Degradação).

Armazena métricas exclusivamente na máquina do usuário sem vazamento de dados:
- Latência total e Time-to-First-Token (TTFT)
- Consumo estimado e medido de VRAM por modelo e tamanho de contexto
- Taxa de respostas vazias e degradação de geração
- Estatísticas de capacidade para tomada de decisão no roteador
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import math
import os
from pathlib import Path
import time
from typing import Any
import uuid

from .storage import private_file

logger = logging.getLogger("mark.cognition.telemetry")

DEFAULT_METRICS_LOG = Path.home() / ".local/state/mark/cognition/metrics.jsonl"
DEFAULT_TELEMETRY_DB = Path.home() / ".local/state/mark/cognition/telemetry.jsonl"
DEFAULT_LOG = DEFAULT_METRICS_LOG
ALLOWED = {"event_id", "recorded_at_ns", "model", "latency_ms", "prompt_tokens", "output_tokens", "attempts", "success"}

# Parâmetros e bit-depths estimados de modelos locais padrão
_MODEL_PARAMS_BILLION: dict[str, float] = {
    "qwen": 4.0,
    "qwen3-vl": 4.0,
    "qwen2.5:3b": 3.0,
    "qwen2.5:7b": 7.0,
    "llama3:8b": 8.0,
    "llama3.2:3b": 3.2,
    "mistral:7b": 7.0,
    "deepseek-r1:8b": 8.0,
    "deepseek-r1:1.5b": 1.5,
    "phi3:mini": 3.8,
    "gemma2:2b": 2.0,
    "gemma2:9b": 9.0,
}


@dataclass
class ModelExecutionMetrics:
    model: str
    prompt_tokens: int
    output_tokens: int
    latency_ms: float
    ttft_ms: float = 0.0
    estimated_vram_mib: float = 0.0
    is_empty: bool = False
    task_type: str = "general"
    success: bool = True
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    recorded_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_model_vram_mib(model_name: str, context_tokens: int = 2048, quantization_bits: float = 4.5) -> float:
    """Calcula matematicamente o consumo estimado de VRAM em MiB para pesos e KV-cache."""
    norm = model_name.lower()
    params = 4.0  # default 4B
    for key in sorted(_MODEL_PARAMS_BILLION.keys(), key=len, reverse=True):
        if key in norm:
            params = _MODEL_PARAMS_BILLION[key]
            break

    # 1. VRAM de Pesos (Params * (bits / 8) * overhead CUDA/PyTorch de 1.15)
    weights_bytes = (params * 1e9) * (quantization_bits / 8.0) * 1.15
    weights_mib = weights_bytes / (1024.0 * 1024.0)

    # 2. VRAM do KV-Cache (2 * layers * heads * dim * context_tokens * 2 bytes)
    # Heurística média: ~0.15 MiB por 1k tokens por bilhão de parâmetros
    kv_cache_mib = (context_tokens / 1024.0) * params * 42.0

    # 3. Contexto de ativações temporárias (~150 MiB)
    activation_overhead_mib = 180.0

    total_mib = weights_mib + kv_cache_mib + activation_overhead_mib
    return round(total_mib, 2)


class CognitiveTelemetry:
    """Coletor isolado de telemetria cognitiva e monitor de regressão local."""

    def __init__(self, log_path: Path | None = None) -> None:
        self.log_path = log_path or DEFAULT_TELEMETRY_DB
        self._in_memory_history: list[ModelExecutionMetrics] = []

    def record_inference(
        self,
        model: str,
        prompt_tokens: int,
        output_tokens: int,
        latency_ms: float,
        ttft_ms: float | None = None,
        is_empty: bool = False,
        estimated_vram_mib: float | None = None,
        task_type: str = "general",
        success: bool = True,
    ) -> ModelExecutionMetrics:
        """Registra métricas de uma inferência de modelo."""
        vram = estimated_vram_mib
        if vram is None:
            vram = estimate_model_vram_mib(model, context_tokens=prompt_tokens + output_tokens)

        ttft = ttft_ms if ttft_ms is not None else (latency_ms * 0.35)

        event = ModelExecutionMetrics(
            model=model,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency_ms, 2),
            ttft_ms=round(ttft, 2),
            estimated_vram_mib=vram,
            is_empty=is_empty,
            task_type=task_type,
            success=success and not is_empty,
        )

        self._in_memory_history.append(event)
        self._persist_event(event)
        return event

    def _persist_event(self, event: ModelExecutionMetrics) -> None:
        """Persiste metricamente em arquivo protegido local sem risco de estouro de disco."""
        try:
            path = private_file(self.log_path)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
                if os.fstat(stream.fileno()).st_size + len(line.encode()) > 5_000_000:
                    return  # Mantém limite de 5MB
                stream.write(line)
        except Exception:
            pass

    def get_model_profile(self, model: str | None = None) -> dict[str, Any]:
        """Calcula perfil estatístico agregado de performance e confiabilidade."""
        events = [
            e for e in self._in_memory_history
            if model is None or e.model.lower() == model.lower()
        ]

        if not events:
            # Retorna estimativa heurística padrão para modelo frio
            model_name = model or "qwen"
            est_vram = estimate_model_vram_mib(model_name)
            return {
                "model": model_name,
                "total_inferences": 0,
                "avg_latency_ms": 1200.0,
                "avg_ttft_ms": 420.0,
                "empty_response_rate": 0.0,
                "estimated_vram_mib": est_vram,
                "success_rate": 1.0,
            }

        total = len(events)
        empties = sum(1 for e in events if e.is_empty)
        successes = sum(1 for e in events if e.success)
        avg_lat = sum(e.latency_ms for e in events) / total
        avg_ttft = sum(e.ttft_ms for e in events) / total
        avg_vram = sum(e.estimated_vram_mib for e in events) / total

        return {
            "model": model or "all",
            "total_inferences": total,
            "avg_latency_ms": round(avg_lat, 2),
            "avg_ttft_ms": round(avg_ttft, 2),
            "empty_response_rate": round(empties / max(1, total), 4),
            "estimated_vram_mib": round(avg_vram, 2),
            "success_rate": round(successes / max(1, total), 4),
        }


_GLOBAL_TELEMETRY: CognitiveTelemetry | None = None


def get_cognitive_telemetry() -> CognitiveTelemetry:
    """Retorna instância singleton do coletor de telemetria cognitiva."""
    global _GLOBAL_TELEMETRY
    if _GLOBAL_TELEMETRY is None:
        _GLOBAL_TELEMETRY = CognitiveTelemetry()
    return _GLOBAL_TELEMETRY


def record_metrics(metrics: dict[str, Any], cfg: dict[str, Any]) -> None:
    """Função de compatibilidade com a interface legada de telemetria."""
    if not cfg.get("cognition", {}).get("telemetry", False):
        return
    data = {key: value for key, value in metrics.items() if key in ALLOWED and key not in {"event_id", "recorded_at_ns"}}
    data.update(event_id=uuid.uuid4().hex, recorded_at_ns=time.time_ns())
    path = private_file(DEFAULT_LOG)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        line = json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n"
        if os.fstat(stream.fileno()).st_size + len(line.encode()) > 1_000_000:
            return
        stream.write(line)

    try:
        telem = get_cognitive_telemetry()
        telem.record_inference(
            model=str(metrics.get("model", "unknown")),
            prompt_tokens=int(metrics.get("prompt_tokens", 0)),
            output_tokens=int(metrics.get("output_tokens", 0)),
            latency_ms=float(metrics.get("latency_ms", 0.0)),
            ttft_ms=metrics.get("ttft_ms"),
            is_empty=bool(metrics.get("is_empty", False)),
            success=bool(metrics.get("success", True)),
        )
    except Exception:
        pass
