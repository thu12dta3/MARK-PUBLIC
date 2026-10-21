from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from enum import Enum
import logging
import os
import re
import time
from typing import Any, Iterator, Sequence
import urllib.error

try:
    import mark_core
except ImportError:
    mark_core = None  # type: ignore[assignment]

from .circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from .telemetry import estimate_model_vram_mib, get_cognitive_telemetry

from ..config.llm_config import AgentProfileConfig, LLMConfig, get_llm_config
from ..logging_sanitizer import sanitize_log_text
from ..mcp_gods_eye import GodsEyeMCPBridge
logger = logging.getLogger("mark.cognition.brain_routing")

_CLOUD_KEYWORDS = (
    "pesquise",
    "analise o código",
    "analise o codigo",
    "analise os dados",
    "resuma o documento",
    "complexo",
)


def _analyze_routing(prompt: str) -> str:
    if detect_sensitive_payload(prompt):
        return "LOCAL"
    if mark_core is not None:
        return mark_core.analyze_routing(prompt)
    lower = prompt.lower()
    return "CLOUD" if any(kw in lower for kw in _CLOUD_KEYWORDS) else "LOCAL"


def route_request(prompt: str, gods_eye_bridge: GodsEyeMCPBridge | None = None) -> str:
    """Usa o motor Rust para decidir o roteamento e orquestra a chamada (incluindo ponte MCP God's Eye)."""
    if gods_eye_bridge is not None:
        dispatched = gods_eye_bridge.dispatch_from_prompt(prompt, open_browser=False)
        if dispatched is not None and dispatched.get("matched"):
            logger.info("Roteamento: Acionando ferramenta MCP do God's Eye (%s).", dispatched.get("tool"))
            return "GODS_EYE_MCP_TRIGGERED"

    decision = _analyze_routing(prompt)

    if decision == "CLOUD":
        logger.info("Roteamento: Escalonando para Cloud API (Poder Extra).")
        return "CLOUD_FALLBACK_TRIGGERED"
    else:
        logger.info("Roteamento: Mantendo processamento Local (Ollama).")
        return "LOCAL_EXECUTION"


def route_with_gods_eye_mcp(prompt: str, bridge: GodsEyeMCPBridge | None = None) -> dict[str, Any]:
    """Avalia e executa solicitações OSINT/espaciais na ponte MCP do God's Eye ou retorna a decisão híbrida."""
    active_bridge = bridge or GodsEyeMCPBridge()
    dispatched = active_bridge.dispatch_from_prompt(prompt, open_browser=False)
    if dispatched is not None and dispatched.get("matched"):
        return {
            "route": "GODS_EYE_MCP_TRIGGERED",
            "tool": dispatched["tool"],
            "mode": dispatched["mode"],
            "payload": dispatched.get("result") or {"reply": dispatched.get("reply")},
        }
    return {"route": route_request(prompt), "tool": None, "mode": "standard", "payload": None}


# ============================================================================
# Roteador Dinâmico e Estratégia de Fallback Determinística
# ============================================================================

from .provider_adapter import (
    AuthenticationError,
    BaseLLMProvider,
    ContextWindowExceededError,
    EmptyResponseError,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
    LLMTimeoutError,
    ProviderAPIError,
    RateLimitError,
    create_llm_provider,
)
from .providers import ModelMetrics, ModelProvider, ModelRequest, ModelResponse


class ProviderType(str, Enum):
    LOCAL_OLLAMA = "LOCAL_OLLAMA"
    LOCAL_SECONDARY = "LOCAL_SECONDARY"
    CLOUD_API = "CLOUD_API"


class PrivacyLevel(str, Enum):
    STRICT_LOCAL = "STRICT_LOCAL"
    CLOUD_AUTHORIZED = "CLOUD_AUTHORIZED"


class TaskComplexity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class RoutingDecision:
    """Decisão determinística e imutável de pré-voo para roteamento cognitivo."""

    provider_type: ProviderType | str
    provider_name: str
    model_name: str
    privacy_level: PrivacyLevel | str
    requires_user_confirmation: bool
    reason: str = ""
    is_sensitive: bool = False
    session_authorized: bool = False
    task_complexity: TaskComplexity | str = TaskComplexity.LOW
    estimated_vram_mib: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        pt = self.provider_type.value if isinstance(self.provider_type, ProviderType) else str(self.provider_type)
        pl = self.privacy_level.value if isinstance(self.privacy_level, PrivacyLevel) else str(self.privacy_level)
        tc = self.task_complexity.value if isinstance(self.task_complexity, TaskComplexity) else str(self.task_complexity)
        return {
            "provider_type": pt,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "privacy_level": pl,
            "requires_user_confirmation": self.requires_user_confirmation,
            "reason": self.reason,
            "is_sensitive": self.is_sensitive,
            "session_authorized": self.session_authorized,
            "task_complexity": tc,
            "estimated_vram_mib": self.estimated_vram_mib,
        }

    @property
    def badge_text(self) -> str:
        pt = self.provider_type.value if isinstance(self.provider_type, ProviderType) else str(self.provider_type)
        if self.is_sensitive:
            return "🔒 Dados Sensíveis (Bloqueio de Nuvem)"
        if pt == "LOCAL_OLLAMA":
            clean_model = self.model_name.split("/")[-1]
            return f"🟢 Local (Ollama: {clean_model})"
        elif pt == "LOCAL_SECONDARY":
            clean_model = self.model_name.split("/")[-1]
            return f"🟢 Local Secundário ({clean_model})"
        elif pt == "CLOUD_API":
            prov_cap = self.provider_name.capitalize()
            if self.session_authorized or not self.requires_user_confirmation:
                return f"🟡 Nuvem Autorizada ({prov_cap} API)"
            else:
                return f"⚠️ Nuvem Requer Confirmação ({prov_cap})"
        return f"⚪ {self.provider_name}: {self.model_name}"


class FallbackChainExhaustedError(Exception):
    """Lançada quando todos os provedores na cadeia de suporte falham."""


class LocalInferenceUnavailableError(FallbackChainExhaustedError):
    """Lançada quando o motor local (Ollama) está indisponível e a nuvem não está habilitada ou autorizada."""

    def __init__(
        self,
        message: str = (
            "Inferência local (Ollama) indisponível e saída para nuvem não autorizada ou desativada. "
            "Inicie o serviço local com 'systemctl start ollama' ou 'ollama serve'."
        ),
        recovery_hint: str = "Execute 'systemctl start ollama' ou 'ollama serve' no terminal.",
    ) -> None:
        super().__init__(message)
        self.recovery_hint = recovery_hint


class PrivacyPolicyViolationError(Exception):
    """Lançada quando o fallback para nuvem é solicitado mas bloqueado por política de privacidade do usuário."""


_SENSITIVE_TAGS = (
    "[confidencial]",
    "[sensivel]",
    "[sensível]",
    "[privado]",
    "[internal]",
    "[restrito]",
    "[restricted]",
    "confidential",
    "secret_key",
    "private_key",
    "api_key",
    "bearer ",
    "password=",
    "passwd=",
)

_SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk-[a-zA-Z0-9_-]{20,}|ghp_[a-zA-Z0-9]{20,}|AIzaSy[a-zA-Z0-9_-]{33})\b"),
    re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
)


_HIGH_COMPLEXITY_KEYWORDS = (
    "arquitetura", "refatore", "refatoração", "benchmark", "algoritmo",
    "prova matemática", "otimize", "concorrência", "deadlock", "segfault",
    "complexo", "implemente", "análise profunda", "debug", "vulnerabilidade",
)


def analyze_task_complexity(prompt: str, context: dict[str, Any] | None = None) -> TaskComplexity:
    """Classifica a complexidade cognitiva da tarefa baseando-se em volume, código e termos."""
    ctx = context or {}
    if ctx.get("task_complexity"):
        val = str(ctx["task_complexity"]).upper()
        if val in TaskComplexity._value2member_map_:
            return TaskComplexity(val)

    lower = prompt.lower()
    word_count = len(prompt.split())

    has_code_block = "```" in prompt or "def " in prompt or "fn " in prompt or "class " in prompt
    has_high_keywords = any(kw in lower for kw in _HIGH_COMPLEXITY_KEYWORDS)
    has_heavy_context = len(ctx.get("context_docs", [])) >= 3 or int(ctx.get("context_tokens", 0)) > 2000

    if (has_high_keywords and (has_code_block or word_count >= 15)) or (has_code_block and word_count >= 20) or has_heavy_context or word_count >= 100:
        return TaskComplexity.HIGH

    if word_count >= 25 or has_code_block or any(kw in lower for kw in _CLOUD_KEYWORDS) or has_high_keywords:
        return TaskComplexity.MEDIUM

    return TaskComplexity.LOW


def detect_sensitive_payload(text: str, context: dict[str, Any] | None = None) -> bool:
    """Verifica proativamente se o prompt ou contexto carrega tags ou padrões de dados sensíveis."""
    if context:
        if context.get("sensitive") is True or context.get("is_confidential") is True:
            return True
        if str(context.get("privacy_level", "")).upper() == "STRICT_LOCAL":
            return True

    if not text:
        return False

    lower = text.lower()
    if any(tag in lower for tag in _SENSITIVE_TAGS):
        return True

    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            return True

    return False


class DynamicBrainRouter:
    """Roteador cognitivo dinâmico com orquestração de provedores, Circuit Breaker e cadeia de suporte."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        allow_cloud_fallback: bool = True,
    ) -> None:
        self.config = config or get_llm_config()
        self._provider_cache: dict[tuple[str, str], BaseLLMProvider] = {}
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            name="ollama",
            failure_threshold=int(os.environ.get("MARK_CB_FAILURE_THRESHOLD", "3")),
            recovery_timeout=float(os.environ.get("MARK_CB_RECOVERY_TIMEOUT", "15.0")),
            health_check_timeout=float(os.environ.get("MARK_CB_HEALTH_TIMEOUT", "2.0")),
        )
        self.allow_cloud_fallback = allow_cloud_fallback

    def get_provider_adapter(self, provider_name: str, model_name: str | None = None) -> BaseLLMProvider:
        """Instancia ou recupera o adaptador de provedor com as credenciais configuradas."""
        norm_provider = provider_name.strip().lower()
        profile = self.config.get_provider(norm_provider)
        target_model = model_name or profile.model

        cache_key = (norm_provider, target_model)
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]

        adapter = create_llm_provider(
            norm_provider,
            model=target_model,
            api_key=profile.api_key,
            base_url=profile.base_url,
            timeout=profile.timeout,
            **profile.extra_params,
        )
        self._provider_cache[cache_key] = adapter
        return adapter

    def get_agent_profile(self, agent_name: str) -> AgentProfileConfig:
        """Recupera o perfil cognitivo do agente configurado."""
        return self.config.get_agent_profile(agent_name)

    def resolve_chain(self, agent_name: str) -> list[tuple[str, str]]:
        """Resolve a lista ordenada e sem duplicatas de (provedor, modelo) para o agente."""
        profile = self.get_agent_profile(agent_name)
        chain: list[tuple[str, str]] = [(profile.primary_provider, profile.primary_model)]
        for candidate in profile.fallback_chain:
            if candidate not in chain:
                chain.append(candidate)
        return chain

    def resolve_target(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
        agent_name: str = "ChatAgent",
    ) -> RoutingDecision:
        """Fase 1 do Roteamento Determinístico: Pre-flight Target Disclosure com balanceamento por capacidade."""
        ctx = context or {}
        is_sensitive = detect_sensitive_payload(prompt, ctx)
        complexity = analyze_task_complexity(prompt, ctx)
        profile = self.get_agent_profile(agent_name)

        selected_provider = ctx.get("selected_provider") or ctx.get("provider")
        selected_model = ctx.get("selected_model") or ctx.get("model")

        if selected_provider:
            norm_prov = str(selected_provider).strip().lower()
            target_model = str(selected_model or self.config.get_provider(norm_prov).model)
            est_vram = estimate_model_vram_mib(target_model)

            if norm_prov not in ("ollama", "local"):
                if is_sensitive:
                    logger.warning("Roteamento: Payload sensível detectado com rota em nuvem solicitada. Bloqueando saída externa.")
                    return RoutingDecision(
                        provider_type=ProviderType.LOCAL_OLLAMA,
                        provider_name=profile.primary_provider,
                        model_name=profile.primary_model,
                        privacy_level=PrivacyLevel.STRICT_LOCAL,
                        requires_user_confirmation=False,
                        reason="Trava de privacidade: dados sensíveis detectados; saída para nuvem bloqueada.",
                        is_sensitive=True,
                        session_authorized=False,
                        task_complexity=complexity,
                        estimated_vram_mib=estimate_model_vram_mib(profile.primary_model),
                    )

                session_auth = bool(
                    ctx.get("cloud_authorized")
                    or ctx.get("session_cloud_token")
                    or ctx.get("user_authorized_cloud")
                )
                requires_confirm = not session_auth
                return RoutingDecision(
                    provider_type=ProviderType.CLOUD_API,
                    provider_name=norm_prov,
                    model_name=target_model,
                    privacy_level=PrivacyLevel.CLOUD_AUTHORIZED,
                    requires_user_confirmation=requires_confirm,
                    reason=f"Provedor em nuvem '{norm_prov}' selecionado pelo usuário.",
                    is_sensitive=False,
                    session_authorized=session_auth,
                    task_complexity=complexity,
                    estimated_vram_mib=est_vram,
                )
            else:
                prov_type = (
                    ProviderType.LOCAL_OLLAMA
                    if target_model == profile.primary_model
                    else ProviderType.LOCAL_SECONDARY
                )
                return RoutingDecision(
                    provider_type=prov_type,
                    provider_name=norm_prov,
                    model_name=target_model,
                    privacy_level=PrivacyLevel.STRICT_LOCAL,
                    requires_user_confirmation=False,
                    reason=f"Provedor local '{norm_prov}:{target_model}' selecionado.",
                    is_sensitive=is_sensitive,
                    session_authorized=False,
                    task_complexity=complexity,
                    estimated_vram_mib=est_vram,
                )

        # Resolução padrão na hierarquia respeitando capacidade e telemetria:
        primary_is_local = profile.primary_provider.lower() in ("ollama", "local")
        ollama_available = self.circuit_breaker.can_execute()

        secondary_local: tuple[str, str] | None = None
        for cand_prov, cand_model in profile.fallback_chain:
            if cand_prov.lower() in ("ollama", "local") and (cand_prov, cand_model) != (profile.primary_provider, profile.primary_model):
                secondary_local = (cand_prov, cand_model)
                break

        # Verificação de telemetria local: degradação do modelo primário
        telem = get_cognitive_telemetry()
        primary_stats = telem.get_model_profile(profile.primary_model)
        has_high_degradation = primary_stats.get("empty_response_rate", 0.0) > 0.20

        # Rota de Capacidade: Se tarefa é de alta complexidade e modelo primário apresenta degradação
        if complexity == TaskComplexity.HIGH and has_high_degradation and secondary_local is not None and ollama_available:
            return RoutingDecision(
                provider_type=ProviderType.LOCAL_SECONDARY,
                provider_name=secondary_local[0],
                model_name=secondary_local[1],
                privacy_level=PrivacyLevel.STRICT_LOCAL,
                requires_user_confirmation=False,
                reason=f"Alta complexidade ({complexity.value}) e modelo primário com degradação registrada ({primary_stats['empty_response_rate']*100:.0f}% vazias); usando secundário local.",
                is_sensitive=is_sensitive,
                session_authorized=False,
                task_complexity=complexity,
                estimated_vram_mib=estimate_model_vram_mib(secondary_local[1]),
            )

        # 1. Rota Primária Local (Ollama)
        if primary_is_local and ollama_available:
            return RoutingDecision(
                provider_type=ProviderType.LOCAL_OLLAMA,
                provider_name=profile.primary_provider,
                model_name=profile.primary_model,
                privacy_level=PrivacyLevel.STRICT_LOCAL,
                requires_user_confirmation=False,
                reason="Rota primária local (Ollama) operacional.",
                is_sensitive=is_sensitive,
                session_authorized=False,
                task_complexity=complexity,
                estimated_vram_mib=estimate_model_vram_mib(profile.primary_model),
            )

        # 2. Rota Secundária Local (se o primário falhou ou está sobrecarregado)
        if secondary_local is not None and ollama_available:
            return RoutingDecision(
                provider_type=ProviderType.LOCAL_SECONDARY,
                provider_name=secondary_local[0],
                model_name=secondary_local[1],
                privacy_level=PrivacyLevel.STRICT_LOCAL,
                requires_user_confirmation=False,
                reason="Rota secundária local selecionada para resiliência.",
                is_sensitive=is_sensitive,
                session_authorized=False,
                task_complexity=complexity,
                estimated_vram_mib=estimate_model_vram_mib(secondary_local[1]),
            )

        # 3. Rota Cloud (Último recurso)
        if is_sensitive:
            return RoutingDecision(
                provider_type=ProviderType.LOCAL_OLLAMA,
                provider_name=profile.primary_provider,
                model_name=profile.primary_model,
                privacy_level=PrivacyLevel.STRICT_LOCAL,
                requires_user_confirmation=False,
                reason="Inferência local inoperante e payload contém dados confidenciais (rota em nuvem proibida).",
                is_sensitive=True,
                session_authorized=False,
                task_complexity=complexity,
                estimated_vram_mib=estimate_model_vram_mib(profile.primary_model),
            )

        is_cloud_allowed = self.allow_cloud_fallback or bool(ctx.get("allow_cloud_override"))
        if not is_cloud_allowed:
            return RoutingDecision(
                provider_type=ProviderType.LOCAL_OLLAMA,
                provider_name=profile.primary_provider,
                model_name=profile.primary_model,
                privacy_level=PrivacyLevel.STRICT_LOCAL,
                requires_user_confirmation=False,
                reason="Inferência local inoperante e saída para nuvem desativada nas políticas de privacidade.",
                is_sensitive=False,
                session_authorized=False,
                task_complexity=complexity,
                estimated_vram_mib=estimate_model_vram_mib(profile.primary_model),
            )

        cloud_candidate: tuple[str, str] | None = None
        for cand_prov, cand_model in profile.fallback_chain:
            if cand_prov.lower() not in ("ollama", "local"):
                cloud_candidate = (cand_prov, cand_model)
                break

        if cloud_candidate is None:
            available_cloud = self.config.get_available_providers()
            if available_cloud:
                first_cp = next(iter(available_cloud.values()))
                cloud_candidate = (first_cp.name, first_cp.model)
            else:
                cloud_candidate = ("openai", "gpt-4o")

        session_auth = bool(
            ctx.get("cloud_authorized")
            or ctx.get("session_cloud_token")
            or ctx.get("user_authorized_cloud")
        )

        return RoutingDecision(
            provider_type=ProviderType.CLOUD_API,
            provider_name=cloud_candidate[0],
            model_name=cloud_candidate[1],
            privacy_level=PrivacyLevel.CLOUD_AUTHORIZED,
            requires_user_confirmation=not session_auth,
            reason="Inferência local inoperante; fallback para nuvem preparado.",
            is_sensitive=False,
            session_authorized=session_auth,
            task_complexity=complexity,
            estimated_vram_mib=estimate_model_vram_mib(cloud_candidate[1]),
        )

    def dispatch(
        self,
        request: str | LLMRequest | ModelRequest,
        resolved_target: RoutingDecision | None = None,
        context: dict[str, Any] | None = None,
        agent_name: str = "ChatAgent",
    ) -> ModelResponse | LLMResponse:
        """Fase 2 do Roteamento Determinístico: Despacho estrito e seguro da requisição com telemetria."""
        ctx = context or {}
        prompt_text = ""
        if isinstance(request, str):
            prompt_text = request
        elif isinstance(request, LLMRequest):
            prompt_text = request.prompt or ""
        elif isinstance(request, ModelRequest):
            prompt_text = request.prompt or ""

        if resolved_target is None:
            resolved_target = self.resolve_target(prompt_text, context=ctx, agent_name=agent_name)

        logger.info(
            "Pre-flight Target Disclosure: provider=%s/%s type=%s privacy=%s confirm=%s sensitive=%s complexity=%s vram=%sMiB",
            resolved_target.provider_name,
            resolved_target.model_name,
            resolved_target.provider_type,
            resolved_target.privacy_level,
            resolved_target.requires_user_confirmation,
            resolved_target.is_sensitive,
            resolved_target.task_complexity,
            resolved_target.estimated_vram_mib,
        )

        # Trava 1: Payload sensível jamais sai para a nuvem
        if resolved_target.is_sensitive and resolved_target.provider_type == ProviderType.CLOUD_API:
            raise PrivacyPolicyViolationError(
                "Bloqueio de segurança: o payload contém dados sensíveis/confidenciais e não pode ser despachado para a nuvem."
            )

        # Trava 2: Se o alvo é local e o Ollama está inoperante/circuito aberto
        is_target_local = resolved_target.provider_type in (ProviderType.LOCAL_OLLAMA, ProviderType.LOCAL_SECONDARY)
        if is_target_local and not self.circuit_breaker.can_execute():
            can_go_cloud = (
                not resolved_target.is_sensitive
                and (self.allow_cloud_fallback or ctx.get("allow_cloud_override"))
                and (ctx.get("cloud_authorized") or ctx.get("session_cloud_token") or ctx.get("user_authorized_cloud"))
            )
            if not can_go_cloud:
                raise LocalInferenceUnavailableError(
                    "Inferência local (Ollama) indisponível e saída para nuvem não autorizada ou desativada por privacidade. "
                    "Inicie o serviço local com 'systemctl start ollama' ou 'ollama serve'.",
                    recovery_hint="Execute 'systemctl start ollama' ou 'ollama serve' no terminal.",
                )

        # Trava 3: Se o alvo é nuvem, requer autorização explícita
        if resolved_target.provider_type == ProviderType.CLOUD_API:
            if not self.allow_cloud_fallback and not ctx.get("allow_cloud_override"):
                raise PrivacyPolicyViolationError("Fallback para nuvem desativado nas configurações globais.")
            if resolved_target.requires_user_confirmation and not (ctx.get("cloud_authorized") or ctx.get("user_authorized_cloud")):
                raise PrivacyPolicyViolationError(
                    f"A rota externa para '{resolved_target.provider_name}' exige autorização explícita do usuário."
                )

        adapter = self.get_provider_adapter(resolved_target.provider_name, resolved_target.model_name)
        start_time = time.perf_counter()
        complexity_label = str(
            resolved_target.task_complexity.value
            if hasattr(resolved_target.task_complexity, "value")
            else resolved_target.task_complexity
        )

        try:
            if isinstance(request, ModelRequest):
                resp = adapter.generate(request)
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                if is_target_local:
                    self.circuit_breaker.record_success()
                is_empty = not bool(resp.text and resp.text.strip())
                get_cognitive_telemetry().record_inference(
                    model=resolved_target.model_name,
                    prompt_tokens=getattr(resp.metrics, "prompt_tokens", max(1, len(prompt_text.split()))),
                    output_tokens=getattr(resp.metrics, "response_tokens", len(resp.text.split())),
                    latency_ms=latency_ms,
                    is_empty=is_empty,
                    estimated_vram_mib=resolved_target.estimated_vram_mib,
                    task_type=complexity_label,
                    success=not is_empty,
                )
                return resp
            elif isinstance(request, LLMRequest):
                req_copy = copy.copy(request)
                req_copy.model = resolved_target.model_name
                resp = adapter.completion(req_copy)
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                if is_target_local:
                    self.circuit_breaker.record_success()
                is_empty = not bool(resp.content and resp.content.strip())
                get_cognitive_telemetry().record_inference(
                    model=resolved_target.model_name,
                    prompt_tokens=getattr(resp, "prompt_tokens", max(1, len(prompt_text.split()))),
                    output_tokens=getattr(resp, "completion_tokens", len(resp.content.split())),
                    latency_ms=latency_ms,
                    is_empty=is_empty,
                    estimated_vram_mib=resolved_target.estimated_vram_mib,
                    task_type=complexity_label,
                    success=not is_empty,
                )
                return resp
            else:
                llm_req = LLMRequest(prompt=prompt_text, model=resolved_target.model_name)
                resp = adapter.completion(llm_req)
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                if is_target_local:
                    self.circuit_breaker.record_success()
                is_empty = not bool(resp.content and resp.content.strip())
                get_cognitive_telemetry().record_inference(
                    model=resolved_target.model_name,
                    prompt_tokens=getattr(resp, "prompt_tokens", max(1, len(prompt_text.split()))),
                    output_tokens=getattr(resp, "completion_tokens", len(resp.content.split())),
                    latency_ms=latency_ms,
                    is_empty=is_empty,
                    estimated_vram_mib=resolved_target.estimated_vram_mib,
                    task_type=complexity_label,
                    success=not is_empty,
                )
                return resp
        except Exception as exc:
            if is_target_local:
                self.circuit_breaker.record_failure(exc)

            latency_ms = (time.perf_counter() - start_time) * 1000.0
            get_cognitive_telemetry().record_inference(
                model=resolved_target.model_name,
                prompt_tokens=max(1, len(prompt_text.split())),
                output_tokens=0,
                latency_ms=latency_ms,
                is_empty=True,
                estimated_vram_mib=resolved_target.estimated_vram_mib,
                task_type=complexity_label,
                success=False,
            )

            can_fallback_cloud = (
                not resolved_target.is_sensitive
                and (self.allow_cloud_fallback or ctx.get("allow_cloud_override"))
                and (ctx.get("cloud_authorized") or ctx.get("session_cloud_token") or ctx.get("user_authorized_cloud"))
            )
            if not can_fallback_cloud:
                raise LocalInferenceUnavailableError(
                    f"Falha na inferência local ({resolved_target.provider_name}/{resolved_target.model_name}): {exc}. "
                    "Saída para nuvem não autorizada.",
                    recovery_hint="Execute 'systemctl start ollama' ou 'ollama serve' no terminal.",
                ) from exc

            logger.warning(
                "Falha no alvo inicial %s/%s (%s). Executando cadeia de fallback autorizada.",
                resolved_target.provider_name,
                resolved_target.model_name,
                exc,
            )
            return self.execute_with_fallback(request, agent_name=agent_name)

    def execute_with_fallback(
        self,
        request: LLMRequest | ModelRequest | str,
        agent_name: str = "ChatAgent",
    ) -> ModelResponse | LLMResponse:
        """Executa a requisição percorrendo a cadeia de fallback de forma transparente com proteção de Circuit Breaker."""
        prompt_text = ""
        if isinstance(request, str):
            prompt_text = request
        elif isinstance(request, LLMRequest):
            prompt_text = request.prompt or ""
        elif isinstance(request, ModelRequest):
            prompt_text = request.prompt or ""

        is_sensitive = detect_sensitive_payload(prompt_text)
        chain = self.resolve_chain(agent_name)
        errors: list[tuple[str, str, Exception]] = []
        started_global = time.perf_counter()
        total_attempts = 0

        first_provider, first_model = chain[0]

        for idx, (provider_name, model_name) in enumerate(chain):
            total_attempts += 1
            is_local = provider_name.lower() in ("ollama", "local")

            # 1. Proteção Circuit Breaker para inferência local no Ollama (127.0.0.1:11434)
            if is_local and not self.circuit_breaker.can_execute():
                cb_err = CircuitBreakerOpenError(
                    f"Circuit Breaker ABERTO para {provider_name}/{model_name}: Ollama inoperante.",
                    provider=provider_name,
                )
                logger.warning(
                    "Circuit Breaker '%s' está ABERTO. Bypassing chamada local e chaveando diretamente para fallback em nuvem.",
                    provider_name,
                )
                errors.append((provider_name, model_name, cb_err))
                continue

            # 2. Respeito estrito aos toggles de privacidade do usuário e detecção de payload sensível
            if not is_local:
                if is_sensitive:
                    priv_err = PrivacyPolicyViolationError(
                        f"Fallback em nuvem para '{provider_name}' bloqueado: dados confidenciais/sensíveis detectados no payload."
                    )
                    logger.warning("Privacidade estrita ativada: payload sensível bloqueado de envio para %s.", provider_name)
                    errors.append((provider_name, model_name, priv_err))
                    continue

                if not self.allow_cloud_fallback:
                    priv_err = PrivacyPolicyViolationError(
                        f"Fallback em nuvem para '{provider_name}' bloqueado por configuração de privacidade estrita do usuário."
                    )
                    logger.info("Privacidade estrita ativada: chamada em nuvem para %s bloqueada.", provider_name)
                    errors.append((provider_name, model_name, priv_err))
                    continue

            adapter = self.get_provider_adapter(provider_name, model_name)
            logger.info("Executando tentativa %d/%d no agente '%s' via %s/%s", idx + 1, len(chain), agent_name, provider_name, model_name)

            try:
                if isinstance(request, ModelRequest):
                    response = adapter.generate(request)
                    if is_local:
                        self.circuit_breaker.record_success()
                    if idx > 0:
                        sanitized_reason = sanitize_log_text(str(errors[0][2]))
                        updated_metrics = replace(
                            response.metrics,
                            attempts=total_attempts,
                            fallback_from=f"{first_provider}:{first_model}",
                            fallback_reason=sanitized_reason,
                        )
                        response = ModelResponse(text=response.text, metrics=updated_metrics)
                    return response

                elif isinstance(request, LLMRequest):
                    req_copy = copy.copy(request)
                    req_copy.model = model_name
                    resp = adapter.completion(req_copy)
                    if is_local:
                        self.circuit_breaker.record_success()
                    return resp

                else:
                    llm_req = LLMRequest(prompt=str(request), model=model_name)
                    resp = adapter.completion(llm_req)
                    if is_local:
                        self.circuit_breaker.record_success()
                    return resp

            except (RateLimitError, LLMTimeoutError, ProviderAPIError, AuthenticationError, ContextWindowExceededError, EmptyResponseError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError, OSError, RuntimeError) as exc:
                if is_local:
                    self.circuit_breaker.record_failure(exc)

                sanitized_msg = sanitize_log_text(str(exc))
                logger.warning(
                    "Falha no provedor %s/%s para o agente '%s': %s. Acionando próximo na cadeia de fallback.",
                    provider_name,
                    model_name,
                    agent_name,
                    sanitized_msg,
                )
                errors.append((provider_name, model_name, exc))
                continue

        error_details = "; ".join(f"{p}/{m}: {sanitize_log_text(str(e))}" for p, m, e in errors)
        if not self.allow_cloud_fallback or is_sensitive:
            raise LocalInferenceUnavailableError(
                f"Inferência local indisponível e saída para nuvem desativada ou bloqueada por privacidade. Detalhes: {error_details}",
                recovery_hint="Execute 'systemctl start ollama' ou 'ollama serve' no terminal.",
            )
        raise FallbackChainExhaustedError(
            f"Todos os provedores na cadeia de fallback falharam para o agente '{agent_name}'. Detalhes: {error_details}"
        )

    def stream_with_fallback(
        self,
        request: LLMRequest,
        agent_name: str = "ChatAgent",
    ) -> Iterator[LLMStreamChunk]:
        """Inicia streaming no provedor primário e chaveia para o fallback se a conexão inicial falhar."""
        prompt_text = request.prompt or ""
        is_sensitive = detect_sensitive_payload(prompt_text)
        chain = self.resolve_chain(agent_name)
        errors: list[tuple[str, str, Exception]] = []

        for idx, (provider_name, model_name) in enumerate(chain):
            is_local = provider_name.lower() in ("ollama", "local")

            if is_local and not self.circuit_breaker.can_execute():
                logger.warning("Streaming: Circuit Breaker ABERTO para %s. Bypassing.", provider_name)
                errors.append((provider_name, model_name, CircuitBreakerOpenError(f"Circuito ABERTO para {provider_name}")))
                continue

            if not is_local:
                if is_sensitive:
                    logger.warning("Streaming: Provedor de nuvem %s bloqueado por dados sensíveis.", provider_name)
                    errors.append((provider_name, model_name, PrivacyPolicyViolationError(f"Provedor {provider_name} bloqueado por dados sensíveis.")))
                    continue

                if not self.allow_cloud_fallback:
                    logger.info("Streaming: Provedor de nuvem %s bloqueado por privacidade.", provider_name)
                    errors.append((provider_name, model_name, PrivacyPolicyViolationError(f"Provedor {provider_name} bloqueado por privacidade.")))
                    continue

            adapter = self.get_provider_adapter(provider_name, model_name)
            req_copy = copy.copy(request)
            req_copy.model = model_name

            try:
                stream_iter = adapter.stream(req_copy)
                first_chunk = next(stream_iter, None)
                if is_local:
                    self.circuit_breaker.record_success()
                if first_chunk is not None:
                    yield first_chunk
                yield from stream_iter
                return
            except Exception as exc:
                if is_local:
                    self.circuit_breaker.record_failure(exc)
                sanitized_msg = sanitize_log_text(str(exc))
                logger.warning(
                    "Streaming falhou no provedor %s/%s para o agente '%s': %s. Tentando próximo fallback.",
                    provider_name,
                    model_name,
                    agent_name,
                    sanitized_msg,
                )
                errors.append((provider_name, model_name, exc))
                continue

        error_details = "; ".join(f"{p}/{m}: {sanitize_log_text(str(e))}" for p, m, e in errors)
        if not self.allow_cloud_fallback or is_sensitive:
            raise LocalInferenceUnavailableError(
                f"Falha de streaming: inferência local indisponível e saída para nuvem desativada ou bloqueada por privacidade. Detalhes: {error_details}",
                recovery_hint="Execute 'systemctl start ollama' ou 'ollama serve' no terminal.",
            )
        raise FallbackChainExhaustedError(
            f"Falha de streaming em todos os provedores na cadeia de fallback para '{agent_name}': {error_details}"
        )


_ROUTER_SINGLETON: DynamicBrainRouter | None = None


def get_brain_router(config: LLMConfig | None = None) -> DynamicBrainRouter:
    """Retorna uma instância única compartilhada do roteador dinâmico."""
    global _ROUTER_SINGLETON
    if config is not None:
        _ROUTER_SINGLETON = DynamicBrainRouter(config)
    elif _ROUTER_SINGLETON is None:
        _ROUTER_SINGLETON = DynamicBrainRouter()
    return _ROUTER_SINGLETON
