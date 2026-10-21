"""Camada Abstrata de Provedores de LLM e Adaptadores Agnósticos do MARK.

Inspirado na arquitetura modular do OpenHands, este módulo oferece:
1. Interface unificada `BaseLLMProvider` (compatível com `ModelProvider` do MARK).
2. Adaptadores dedicados:
   - OpenAI / Azure OpenAI
   - Anthropic (Claude 3.5 Sonnet / Haiku / Opus)
   - Google Gemini (AI Studio / Vertex AI)
   - Groq / OpenRouter / DeepSeek / Mistral
   - Provedores Locais (Ollama, vLLM, LM Studio e endpoints compatíveis com OpenAI)
3. Normalização completa de:
   - Respostas em streaming Server-Sent Events (SSE)
   - Contagem e métricas de tokens (prompt, completion, total)
   - Tratamento e tradução de erros unificada (Auth, RateLimit, Timeout, ContextWindow, API)
   - Limites de contexto por família de modelo
"""

from __future__ import annotations

import abc
import copy
from dataclasses import dataclass, field
import json
import logging
import os
import re
import time
from typing import Any, Callable, Iterator, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

from ..url_security import validate_url_scheme
from .providers import (
    ModelMetrics,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    sanitize_chat_output,
)
from .resilience import compress_request_history, tolerant_json_parse

logger = logging.getLogger("mark.cognition.provider_adapter")

# ============================================================================
# Exceções Unificadas de LLM
# ============================================================================


class LLMError(Exception):
    """Exceção base para erros em provedores de modelos de linguagem."""

    def __init__(self, message: str, *, provider: str = "", model: str = "", status_code: int | None = None, raw_error: Any = None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.raw_error = raw_error


class AuthenticationError(LLMError):
    """Falha de autenticação (HTTP 401 ou 403) ou chave de API inválida."""


class RateLimitError(LLMError):
    """Cota esgotada ou limite de taxa atingido (HTTP 429)."""


class LLMTimeoutError(LLMError, TimeoutError):
    """Tempo limite de conexão ou geração excedido."""


class ContextWindowExceededError(LLMError):
    """Número de tokens de entrada/saída excede a janela de contexto suportada pelo modelo."""


class EmptyResponseError(LLMError, RuntimeError):
    """Resposta vazia ou nula retornada pelo modelo após tentativa de re-prompt."""


class ProviderAPIError(LLMError):
    """Erro interno retornado pelo provedor upstream (HTTP 5xx ou resposta malformada)."""


# ============================================================================
# Modelos de Dados e Mensagens
# ============================================================================


@dataclass(frozen=True)
class LLMMessage:
    """Mensagem de conversa para o provedor de LLM."""

    role: str
    content: str
    name: str | None = None

    def to_dict(self) -> dict[str, str]:
        res = {"role": self.role, "content": self.content}
        if self.name:
            res["name"] = self.name
        return res


@dataclass
class LLMRequest:
    """Requisição agnóstica de inferência para qualquer provedor de LLM."""

    prompt: str = ""
    messages: list[LLMMessage] | list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    system: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = 2048
    stream: bool = False
    stop: list[str] | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    output_format: Any = None
    timeout: float = 60.0

    def resolved_messages(self) -> list[dict[str, str]]:
        """Gera a lista final de mensagens normalizadas em formato dict."""
        output: list[dict[str, str]] = []
        if self.system:
            output.append({"role": "system", "content": self.system})

        for msg in self.messages:
            if isinstance(msg, LLMMessage):
                output.append(msg.to_dict())
            elif isinstance(msg, dict):
                output.append({"role": str(msg.get("role", "user")), "content": str(msg.get("content", ""))})

        if self.prompt:
            output.append({"role": "user", "content": self.prompt})

        return output


@dataclass(frozen=True)
class LLMStreamChunk:
    """Fatia normalizada de resposta em streaming (SSE)."""

    delta_text: str
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    is_final: bool = False


@dataclass(frozen=True)
class LLMResponse:
    """Resposta normalizada e consolidada do modelo."""

    content: str
    model: str
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    raw_response: dict[str, Any] | None = None


# ============================================================================
# Estimativa de Tokens e Limites de Janela de Contexto
# ============================================================================

MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
    # Anthropic
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    # Google Gemini
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.0-flash-lite": 1_048_576,
    "gemini-1.5-pro": 2_097_152,
    "gemini-1.5-flash": 1_048_576,
    # OpenSource / Meta / Mistral / DeepSeek
    "llama-3.3-70b": 131_072,
    "llama-3.1-70b": 131_072,
    "llama-3.1-8b": 131_072,
    "mixtral-8x7b": 32_768,
    "mistral-large": 128_000,
    "mistral-small": 32_768,
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
    # Modelos locais / Qwen / Heretic
    "qwen2.5-coder": 32_768,
    "qwen2.5": 32_768,
    "qwen3": 32_768,
    "heretic": 32_768,
}

DEFAULT_CONTEXT_WINDOW = 8_192


def estimate_tokens(text: str) -> int:
    """Estimador de tokens rápido e independente sem bibliotecas externas."""
    if not text:
        return 0
    # Heurística com base em palavras e pontuações do Unicode (~3.8 caracteres/token)
    tokens = re.findall(r"\w+|[^\w\s]", text, re.UNICODE)
    return max(1, len(tokens))


def get_model_context_window(model_name: str) -> int:
    """Retorna o limite de janela de contexto em tokens para o modelo especificado."""
    if not model_name:
        return DEFAULT_CONTEXT_WINDOW
    name_lower = model_name.lower()
    for prefix, limit in MODEL_CONTEXT_WINDOWS.items():
        if prefix in name_lower:
            return limit
    return DEFAULT_CONTEXT_WINDOW


# ============================================================================
# Interface Base do Provedor de LLM
# ============================================================================


class BaseLLMProvider(ModelProvider, abc.ABC):
    """Interface unificada e abstrata para qualquer provedor de modelos de linguagem."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = float(timeout)
        self.extra_headers = dict(extra_headers or {})
        self.extra_config = kwargs
        self.last_metrics: ModelMetrics | None = None

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Nome identificador do provedor (ex.: 'openai', 'anthropic', 'ollama')."""
        ...

    @abc.abstractmethod
    def completion(self, request: LLMRequest) -> LLMResponse:
        """Executa chamada não-bloqueante/síncrona de conclusão de texto."""
        ...

    @abc.abstractmethod
    def stream(self, request: LLMRequest) -> Iterator[LLMStreamChunk]:
        """Executa chamada em streaming retornando fatias padronizadas (SSE)."""
        ...

    def count_tokens(self, text: str) -> int:
        """Calcula ou estima o número de tokens para uma dada string."""
        return estimate_tokens(text)

    def get_context_window(self, model: str | None = None) -> int:
        """Retorna o limite de contexto do modelo configurado."""
        return get_model_context_window(model or self.model)

    def describe(self) -> dict[str, Any]:
        """Metadados do provedor para telemetria e diagnóstico."""
        return {
            "provider": self.provider_name,
            "model": self.model,
            "base_url": self.base_url,
            "context_window": self.get_context_window(),
        }

    def set_keep_alive(self, seconds: int) -> None:
        """Implementação padrão de controle de ciclo de vida (sobrescrita por Ollama)."""
        pass

    def check_context_window(self, request: LLMRequest) -> None:
        """Valida se o prompt excede a janela máxima de contexto, aplicando compressão deslizante se necessário."""
        max_context = self.get_context_window(request.model or self.model)
        total_chars = len(request.prompt) + len(request.system or "")
        for msg in request.messages:
            if isinstance(msg, LLMMessage):
                total_chars += len(msg.content)
            elif isinstance(msg, dict):
                total_chars += len(str(msg.get("content", "")))
        est_tokens = max(1, total_chars // 4)

        if est_tokens > max_context:
            # Tenta compressão automática do histórico anterior preservando System e Prompt atual
            if request.messages:
                dict_messages = []
                for m in request.messages:
                    if isinstance(m, LLMMessage):
                        dict_messages.append(m.to_dict())
                    elif isinstance(m, dict):
                        dict_messages.append({"role": str(m.get("role", "user")), "content": str(m.get("content", ""))})

                compressed_msgs, was_compressed = compress_request_history(
                    dict_messages,
                    system_prompt=request.system,
                    current_prompt=request.prompt,
                    max_context=max_context,
                    reserve_tokens=min(512, max_context // 4),
                )
                if was_compressed:
                    request.messages = [LLMMessage(role=m["role"], content=m["content"]) for m in compressed_msgs]
                    new_chars = len(request.prompt) + len(request.system or "")
                    for m in request.messages:
                        new_chars += len(m.content)
                    if (new_chars // 4) <= max_context:
                        logger.info("Janela deslizante ativada: histórico comprimido para caber em %d tokens.", max_context)
                        return

            raise ContextWindowExceededError(
                f"Entrada com ~{est_tokens} tokens excede o limite máximo de {max_context} tokens do modelo '{self.model}'.",
                provider=self.provider_name,
                model=self.model,
            )

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Compatibilidade direta com a camada `ModelProvider` nativa do MARK."""
        started = time.perf_counter()
        history_msgs = []
        for turn in getattr(request, "history", ()):
            if isinstance(turn, dict) and "role" in turn and "content" in turn:
                history_msgs.append(LLMMessage(role=turn["role"], content=turn["content"]))

        llm_req = LLMRequest(
            prompt=request.prompt,
            system=request.system,
            messages=history_msgs,
            model=self.model,
            max_tokens=request.num_predict,
            output_format=request.output_format,
            timeout=self.timeout,
        )

        try:
            resp = self.completion(llm_req)
            text = (resp.content or "").strip()

            # Resposta vazia ou nula: dispara re-prompt automático
            if not text:
                logger.warning("Resposta vazia retornada por '%s'. Acionando re-prompt interno.", self.model)
                retry_req = copy.copy(llm_req)
                retry_req.prompt = f"{llm_req.prompt}\n\n[Forneça agora uma resposta para a solicitação anterior, sem omitir conteúdo.]"
                try:
                    retry_resp = self.completion(retry_req)
                    text = (retry_resp.content or "").strip()
                except Exception as retry_exc:
                    logger.warning("Falha no re-prompt interno: %s", retry_exc)
                    text = ""

                if not text:
                    raise EmptyResponseError(
                        f"Resposta vazia retornada pelo modelo '{self.model}' após re-prompt interno.",
                        provider=self.provider_name,
                        model=self.model,
                    )

            if request.output_format:
                parsed = tolerant_json_parse(text)
                if parsed is not None:
                    text = json.dumps(parsed, ensure_ascii=False)
            else:
                text = sanitize_chat_output(text)

            latency = (time.perf_counter() - started) * 1000
            metrics = ModelMetrics(
                model=resp.model or self.model,
                latency_ms=latency,
                prompt_tokens=resp.prompt_tokens,
                output_tokens=resp.completion_tokens or estimate_tokens(text),
                attempts=1,
                success=True,
            )
            self.last_metrics = metrics
            return ModelResponse(text=text, metrics=metrics)
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000
            self.last_metrics = ModelMetrics(
                model=self.model,
                latency_ms=latency,
                prompt_tokens=0,
                output_tokens=0,
                attempts=1,
                success=False,
            )
            raise


# ============================================================================
# Provedor Base Compatível com OpenAI (Usado por OpenAI, Groq, DeepSeek, etc.)
# ============================================================================


class OpenAICompatibleProvider(BaseLLMProvider):
    """Adaptador para APIs compatíveis com a especificação OpenAI v1."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        chat_endpoint: str = "/chat/completions",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            extra_headers=extra_headers,
            **kwargs,
        )
        self.chat_endpoint = chat_endpoint

    @property
    def provider_name(self) -> str:
        return "openai-compatible"

    def _build_url(self) -> str:
        endpoint = self.chat_endpoint
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{self.base_url}{endpoint}"

    def _build_headers(self, request: LLMRequest) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MARK-Assistant/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        headers.update(request.extra_headers)
        return headers

    def _build_payload(self, request: LLMRequest, stream: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": request.resolved_messages(),
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.stop:
            payload["stop"] = request.stop
        if request.output_format == "json" or request.output_format == {"type": "json_object"}:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _translate_error(self, exc: Exception) -> Exception:
        if isinstance(exc, urllib.error.HTTPError):
            code = exc.code
            try:
                body = exc.read().decode("utf-8")
                data = json.loads(body)
                msg = data.get("error", {}).get("message") or body
            except Exception:
                msg = exc.reason or f"HTTP {code}"

            if code in (401, 403):
                return AuthenticationError(f"Erro de autenticação ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            if code == 429:
                return RateLimitError(f"Limite de taxa/cota atingido (429): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            if code >= 500:
                return ProviderAPIError(f"Erro interno do servidor upstream ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            return ProviderAPIError(f"Erro na requisição ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)

        if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError) or "timed out" in str(reason).lower():
                return LLMTimeoutError(f"Timeout ao conectar com provedor: {reason}", provider=self.provider_name, model=self.model, raw_error=exc)
            return ProviderAPIError(f"Erro de conexão com o provedor: {reason}", provider=self.provider_name, model=self.model, raw_error=exc)

        return exc

    def completion(self, request: LLMRequest) -> LLMResponse:
        self.check_context_window(request)
        url = self._build_url()
        headers = self._build_headers(request)
        payload = self._build_payload(request, stream=False)

        validate_url_scheme(url)
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=request.timeout or self.timeout) as resp:  # nosec: B310
                raw = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise self._translate_error(exc) from None

        latency = (time.perf_counter() - started) * 1000
        choice = (raw.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content") or ""
        finish_reason = choice.get("finish_reason") or "stop"
        usage = raw.get("usage") or {}

        p_tokens = int(usage.get("prompt_tokens", 0)) or estimate_tokens(request.prompt)
        c_tokens = int(usage.get("completion_tokens", 0)) or estimate_tokens(content)
        t_tokens = int(usage.get("total_tokens", 0)) or (p_tokens + c_tokens)

        return LLMResponse(
            content=content,
            model=raw.get("model", self.model),
            finish_reason=finish_reason,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=t_tokens,
            latency_ms=latency,
            raw_response=raw,
        )

    def stream(self, request: LLMRequest) -> Iterator[LLMStreamChunk]:
        self.check_context_window(request)
        url = self._build_url()
        headers = self._build_headers(request)
        payload = self._build_payload(request, stream=True)

        validate_url_scheme(url)
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        prompt_tokens = estimate_tokens(request.prompt)
        accumulated_text = ""

        try:
            with urllib.request.urlopen(req, timeout=request.timeout or self.timeout) as resp:  # nosec: B310
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[len("data:") :].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choice = (chunk_data.get("choices") or [{}])[0]
                        delta = choice.get("delta", {}).get("content") or ""
                        finish_reason = choice.get("finish_reason")
                        usage = chunk_data.get("usage")

                        if delta:
                            accumulated_text += delta
                            yield LLMStreamChunk(
                                delta_text=delta,
                                finish_reason=finish_reason,
                            )
                        if finish_reason:
                            c_tokens = usage.get("completion_tokens") if usage else estimate_tokens(accumulated_text)
                            p_tokens = usage.get("prompt_tokens") if usage else prompt_tokens
                            t_tokens = usage.get("total_tokens") if usage else (p_tokens + c_tokens)
                            yield LLMStreamChunk(
                                delta_text="",
                                finish_reason=finish_reason,
                                prompt_tokens=p_tokens,
                                completion_tokens=c_tokens,
                                total_tokens=t_tokens,
                                is_final=True,
                            )
                            return
        except Exception as exc:
            raise self._translate_error(exc) from None

        # Chunk final de encerramento caso o provedor não envie finish_reason explícito
        c_tokens = estimate_tokens(accumulated_text)
        yield LLMStreamChunk(
            delta_text="",
            finish_reason="stop",
            prompt_tokens=prompt_tokens,
            completion_tokens=c_tokens,
            total_tokens=prompt_tokens + c_tokens,
            is_final=True,
        )


# ============================================================================
# Adaptadores Específicos
# ============================================================================


class OpenAIProvider(OpenAICompatibleProvider):
    """Adaptador oficial para a API da OpenAI."""

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url,
            timeout=timeout,
            **kwargs,
        )

    @property
    def provider_name(self) -> str:
        return "openai"


class AzureOpenAIProvider(OpenAICompatibleProvider):
    """Adaptador para instâncias do Azure OpenAI Service."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        azure_endpoint: str | None = None,
        api_version: str = "2024-08-01-preview",
        deployment_name: str | None = None,
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        endpoint = (azure_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).rstrip("/")
        dep = deployment_name or model
        base_url = f"{endpoint}/openai/deployments/{dep}"
        chat_endpoint = f"/chat/completions?api-version={api_version}"
        key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")

        super().__init__(
            model=model,
            api_key=key,
            base_url=base_url,
            timeout=timeout,
            chat_endpoint=chat_endpoint,
            extra_headers={"api-key": key or ""},
            **kwargs,
        )

    @property
    def provider_name(self) -> str:
        return "azure-openai"


class AnthropicProvider(BaseLLMProvider):
    """Adaptador nativo para a API da Anthropic (Claude 3.5 Sonnet / Haiku / Opus)."""

    def __init__(
        self,
        *,
        model: str = "claude-3-5-sonnet-20241022",
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 60.0,
        anthropic_version: str = "2023-06-01",
        **kwargs: Any,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        super().__init__(model=model, api_key=key, base_url=base_url, timeout=timeout, **kwargs)
        self.anthropic_version = anthropic_version

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _build_headers(self, request: LLMRequest) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "User-Agent": "MARK-Assistant/1.0",
        }
        headers.update(self.extra_headers)
        headers.update(request.extra_headers)
        return headers

    def _build_payload(self, request: LLMRequest, stream: bool = False) -> dict[str, Any]:
        system_text = request.system or ""
        messages_out: list[dict[str, str]] = []

        for msg in request.messages:
            role = msg.role if isinstance(msg, LLMMessage) else str(msg.get("role", "user"))
            content = msg.content if isinstance(msg, LLMMessage) else str(msg.get("content", ""))
            if role == "system":
                system_text = f"{system_text}\n{content}".strip() if system_text else content
            else:
                anthropic_role = "assistant" if role == "assistant" else "user"
                messages_out.append({"role": anthropic_role, "content": content})

        if request.prompt:
            messages_out.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": messages_out,
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
            "stream": stream,
        }
        if system_text:
            payload["system"] = system_text
        if request.stop:
            payload["stop_sequences"] = request.stop
        return payload

    def _translate_error(self, exc: Exception) -> Exception:
        if isinstance(exc, urllib.error.HTTPError):
            code = exc.code
            try:
                body = exc.read().decode("utf-8")
                data = json.loads(body)
                msg = data.get("error", {}).get("message") or body
            except Exception:
                msg = exc.reason or f"HTTP {code}"

            if code in (401, 403):
                return AuthenticationError(f"Anthropic autenticação ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            if code == 429:
                return RateLimitError(f"Anthropic limite de taxa (429): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            if code >= 500:
                return ProviderAPIError(f"Anthropic erro interno ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            return ProviderAPIError(f"Anthropic erro ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)

        if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError) or "timed out" in str(reason).lower():
                return LLMTimeoutError(f"Timeout Anthropic: {reason}", provider=self.provider_name, model=self.model, raw_error=exc)
            return ProviderAPIError(f"Erro de conexão Anthropic: {reason}", provider=self.provider_name, model=self.model, raw_error=exc)

        return exc

    def completion(self, request: LLMRequest) -> LLMResponse:
        self.check_context_window(request)
        url = f"{self.base_url}/messages"
        headers = self._build_headers(request)
        payload = self._build_payload(request, stream=False)

        validate_url_scheme(url)
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=request.timeout or self.timeout) as resp:  # nosec: B310
                raw = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise self._translate_error(exc) from None

        latency = (time.perf_counter() - started) * 1000
        content_blocks = raw.get("content") or []
        text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
        usage = raw.get("usage") or {}
        p_tokens = int(usage.get("input_tokens", 0))
        c_tokens = int(usage.get("output_tokens", 0))

        return LLMResponse(
            content=text,
            model=raw.get("model", self.model),
            finish_reason=raw.get("stop_reason") or "stop",
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=p_tokens + c_tokens,
            latency_ms=latency,
            raw_response=raw,
        )

    def stream(self, request: LLMRequest) -> Iterator[LLMStreamChunk]:
        self.check_context_window(request)
        url = f"{self.base_url}/messages"
        headers = self._build_headers(request)
        payload = self._build_payload(request, stream=True)

        validate_url_scheme(url)
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        prompt_tokens = 0
        completion_tokens = 0

        try:
            with urllib.request.urlopen(req, timeout=request.timeout or self.timeout) as resp:  # nosec: B310
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type")
                    if event_type == "message_start":
                        usage = data.get("message", {}).get("usage") or {}
                        prompt_tokens = usage.get("input_tokens", 0)
                    elif event_type == "content_block_delta":
                        delta_text = data.get("delta", {}).get("text", "")
                        if delta_text:
                            yield LLMStreamChunk(delta_text=delta_text)
                    elif event_type == "message_delta":
                        usage = data.get("usage") or {}
                        completion_tokens = usage.get("output_tokens", 0)
                        stop_reason = data.get("delta", {}).get("stop_reason")
                        yield LLMStreamChunk(
                            delta_text="",
                            finish_reason=stop_reason,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=prompt_tokens + completion_tokens,
                            is_final=True,
                        )
                        return
        except Exception as exc:
            raise self._translate_error(exc) from None

        yield LLMStreamChunk(
            delta_text="",
            finish_reason="stop",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            is_final=True,
        )


class GeminiProvider(BaseLLMProvider):
    """Adaptador para Google Gemini (Google AI Studio ou Vertex AI)."""

    def __init__(
        self,
        *,
        model: str = "gemini-2.0-flash",
        api_key: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY")
        super().__init__(model=model, api_key=key, base_url=base_url, timeout=timeout, **kwargs)

    @property
    def provider_name(self) -> str:
        return "gemini"

    def _build_url(self, stream: bool = False) -> str:
        clean_model = self.model.removeprefix("models/")
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{self.base_url}/models/{clean_model}:{method}"

    def _build_headers(self, request: LLMRequest) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MARK-Assistant/1.0",
        }
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        headers.update(self.extra_headers)
        headers.update(request.extra_headers)
        return headers

    def _build_payload(self, request: LLMRequest) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        system_text = request.system or ""

        for msg in request.messages:
            role = msg.role if isinstance(msg, LLMMessage) else str(msg.get("role", "user"))
            content = msg.content if isinstance(msg, LLMMessage) else str(msg.get("content", ""))
            if role == "system":
                system_text = f"{system_text}\n{content}".strip() if system_text else content
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({"role": gemini_role, "parts": [{"text": content}]})

        if request.prompt:
            contents.append({"role": "user", "parts": [{"text": request.prompt}]})

        gen_config: dict[str, Any] = {"temperature": request.temperature}
        if request.max_tokens:
            gen_config["maxOutputTokens"] = request.max_tokens
        if request.stop:
            gen_config["stopSequences"] = request.stop

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_config,
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        return payload

    def _translate_error(self, exc: Exception) -> Exception:
        if isinstance(exc, urllib.error.HTTPError):
            code = exc.code
            try:
                body = exc.read().decode("utf-8")
                data = json.loads(body)
                msg = data.get("error", {}).get("message") or body
            except Exception:
                msg = exc.reason or f"HTTP {code}"

            if code in (401, 403):
                return AuthenticationError(f"Gemini autenticação ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            if code == 429:
                return RateLimitError(f"Gemini limite de cota (429): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            if code >= 500:
                return ProviderAPIError(f"Gemini erro interno ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)
            return ProviderAPIError(f"Gemini erro ({code}): {msg}", provider=self.provider_name, model=self.model, status_code=code, raw_error=exc)

        if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError) or "timed out" in str(reason).lower():
                return LLMTimeoutError(f"Timeout Gemini: {reason}", provider=self.provider_name, model=self.model, raw_error=exc)
            return ProviderAPIError(f"Erro de conexão Gemini: {reason}", provider=self.provider_name, model=self.model, raw_error=exc)

        return exc

    def completion(self, request: LLMRequest) -> LLMResponse:
        self.check_context_window(request)
        url = self._build_url(stream=False)
        headers = self._build_headers(request)
        payload = self._build_payload(request)

        validate_url_scheme(url)
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=request.timeout or self.timeout) as resp:  # nosec: B310
                raw = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise self._translate_error(exc) from None

        latency = (time.perf_counter() - started) * 1000
        candidates = raw.get("candidates") or [{}]
        first = candidates[0]
        parts = first.get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        finish_reason = first.get("finishReason") or "STOP"

        usage = raw.get("usageMetadata") or {}
        p_tokens = int(usage.get("promptTokenCount", 0))
        c_tokens = int(usage.get("candidatesTokenCount", 0))
        t_tokens = int(usage.get("totalTokenCount", 0)) or (p_tokens + c_tokens)

        return LLMResponse(
            content=text,
            model=self.model,
            finish_reason=finish_reason,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=t_tokens,
            latency_ms=latency,
            raw_response=raw,
        )

    def stream(self, request: LLMRequest) -> Iterator[LLMStreamChunk]:
        self.check_context_window(request)
        url = self._build_url(stream=True)
        headers = self._build_headers(request)
        payload = self._build_payload(request)

        validate_url_scheme(url)
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        p_tokens = 0
        c_tokens = 0
        t_tokens = 0

        try:
            with urllib.request.urlopen(req, timeout=request.timeout or self.timeout) as resp:  # nosec: B310
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    candidates = data.get("candidates") or []
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts") or []
                        delta_text = "".join(p.get("text", "") for p in parts if "text" in p)
                        if delta_text:
                            yield LLMStreamChunk(delta_text=delta_text)

                    usage = data.get("usageMetadata")
                    if usage:
                        p_tokens = usage.get("promptTokenCount", p_tokens)
                        c_tokens = usage.get("candidatesTokenCount", c_tokens)
                        t_tokens = usage.get("totalTokenCount", t_tokens)
        except Exception as exc:
            raise self._translate_error(exc) from None

        yield LLMStreamChunk(
            delta_text="",
            finish_reason="STOP",
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=t_tokens or (p_tokens + c_tokens),
            is_final=True,
        )


class GroqProvider(OpenAICompatibleProvider):
    """Adaptador para a API de ultra-baixa latência da Groq."""

    def __init__(
        self,
        *,
        model: str = "llama-3.3-70b-versatile",
        api_key: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.environ.get("GROQ_API_KEY")
        super().__init__(model=model, api_key=key, base_url=base_url, timeout=timeout, **kwargs)

    @property
    def provider_name(self) -> str:
        return "groq"


class OpenRouterProvider(OpenAICompatibleProvider):
    """Adaptador para o agregador global OpenRouter."""

    def __init__(
        self,
        *,
        model: str = "openrouter/auto",
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        headers = {
            "HTTP-Referer": "https://mark-assistant.local",
            "X-Title": "MARK Cognitive Assistant",
        }
        headers.update(kwargs.pop("extra_headers", {}))
        super().__init__(model=model, api_key=key, base_url=base_url, timeout=timeout, extra_headers=headers, **kwargs)

    @property
    def provider_name(self) -> str:
        return "openrouter"


class DeepSeekProvider(OpenAICompatibleProvider):
    """Adaptador para a API oficial da DeepSeek (deepseek-chat / deepseek-reasoner)."""

    def __init__(
        self,
        *,
        model: str = "deepseek-chat",
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        super().__init__(model=model, api_key=key, base_url=base_url, timeout=timeout, **kwargs)

    @property
    def provider_name(self) -> str:
        return "deepseek"


class MistralProvider(OpenAICompatibleProvider):
    """Adaptador para a API da Mistral AI."""

    def __init__(
        self,
        *,
        model: str = "mistral-large-latest",
        api_key: str | None = None,
        base_url: str = "https://api.mistral.ai/v1",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        key = api_key or os.environ.get("MISTRAL_API_KEY")
        super().__init__(model=model, api_key=key, base_url=base_url, timeout=timeout, **kwargs)

    @property
    def provider_name(self) -> str:
        return "mistral"


class LocalProvider(OpenAICompatibleProvider):
    """Adaptador genérico para provedores locais (Ollama, vLLM, LM Studio, etc.)."""

    def __init__(
        self,
        *,
        model: str = "haervwe/qwen3-vl-4b-heretic:latest",
        api_key: str | None = None,
        base_url: str = "http://127.0.0.1:11434/v1",
        timeout: float = 120.0,
        engine_type: str = "ollama",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key or "local-no-key",
            base_url=base_url,
            timeout=timeout,
            **kwargs,
        )
        self.engine_type = engine_type

    @property
    def provider_name(self) -> str:
        return f"local-{self.engine_type}"


class OllamaProviderAdapter(LocalProvider):
    """Adaptador para instâncias do Ollama via endpoint OpenAI compatível ou nativo."""

    def __init__(
        self,
        *,
        model: str = "haervwe/qwen3-vl-4b-heretic:latest",
        base_url: str = "http://127.0.0.1:11434/v1",
        timeout: float = 120.0,
        **kwargs: Any,
    ) -> None:
        url = os.environ.get("MARK_OLLAMA_URL") or os.environ.get("OLLAMA_HOST") or base_url
        if not url.endswith("/v1"):
            url = f"{url.rstrip('/')}/v1"
        super().__init__(model=model, base_url=url, timeout=timeout, engine_type="ollama", **kwargs)

    @classmethod
    def get_installed_models(cls, base_url: str | None = None, timeout: float = 1.5) -> list[str]:
        """Consulta dinamicamente os modelos instalados no Ollama via endpoint /api/tags."""
        target_url = (
            base_url
            or os.environ.get("MARK_OLLAMA_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://localhost:11434"
        )
        clean_url = target_url.rstrip("/")
        if clean_url.endswith("/v1"):
            clean_url = clean_url[:-3].rstrip("/")
        tags_url = f"{clean_url}/api/tags"

        try:
            req = urllib.request.Request(tags_url, headers={"User-Agent": "MARK/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status != 200:
                    return []
                raw = response.read().decode("utf-8")
                payload = json.loads(raw)
                models = payload.get("models", [])
                result: list[str] = []
                for m in models:
                    if isinstance(m, dict):
                        name = m.get("name") or m.get("model")
                        if name and isinstance(name, str):
                            result.append(name.strip())
                return result
        except Exception as exc:
            logger.debug("Falha ao consultar modelos instalados no Ollama (%s): %s", tags_url, exc)
            return []

    @property
    def provider_name(self) -> str:
        return "ollama"


class VLLMProvider(LocalProvider):
    """Adaptador para servidores de inferência vLLM."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:8000/v1",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, base_url=base_url, timeout=timeout, engine_type="vllm", **kwargs)

    @property
    def provider_name(self) -> str:
        return "vllm"


class LMStudioProvider(LocalProvider):
    """Adaptador para servidores locais LM Studio."""

    def __init__(
        self,
        *,
        model: str = "local-model",
        base_url: str = "http://127.0.0.1:1234/v1",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, base_url=base_url, timeout=timeout, engine_type="lmstudio", **kwargs)

    @property
    def provider_name(self) -> str:
        return "lmstudio"


# ============================================================================
# Registro e Fábrica de Provedores
# ============================================================================

PROVIDER_REGISTRY: dict[str, type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "azure": AzureOpenAIProvider,
    "azure-openai": AzureOpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "google": GeminiProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "deepseek": DeepSeekProvider,
    "mistral": MistralProvider,
    "ollama": OllamaProviderAdapter,
    "vllm": VLLMProvider,
    "lmstudio": LMStudioProvider,
    "local": LocalProvider,
}


def create_llm_provider(
    provider_name: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
    **kwargs: Any,
) -> BaseLLMProvider:
    """Instancia um adaptador de provedor com base no nome e opções fornecidas."""
    normalized = provider_name.strip().lower()
    cls = PROVIDER_REGISTRY.get(normalized)
    if cls is None:
        raise ValueError(f"Provedor de LLM não reconhecido: '{provider_name}'. Suportados: {sorted(PROVIDER_REGISTRY.keys())}")

    init_kwargs: dict[str, Any] = {"timeout": timeout}
    if model is not None:
        init_kwargs["model"] = model
    if api_key is not None:
        init_kwargs["api_key"] = api_key
    if base_url is not None:
        init_kwargs["base_url"] = base_url
    init_kwargs.update(kwargs)

    return cls(**init_kwargs)
