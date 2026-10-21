"""Conector Local Somente Leitura para o Home Assistant (Fase 7).

Permite consultar entidades sob demanda filtradas por cômodo/área ou domínio,
com armazenamento local privado (0600) do token de longa duração, proibição
estrita de mutação (somente GET) e tratamento explícito de estados
indisponíveis (`unavailable`, `unknown`) ou desatualizados (`stale`).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import socket
import stat
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .cognition.domains import normalize
from .cognition.storage import private_file
from .cognition.tools import BridgeCapabilityGuard, CapabilityToken, ToolContract, ToolRegistry
from .gods_eye_view import _schema_check
from .url_security import validate_url_scheme

log = logging.getLogger("mark.home_assistant")

DEFAULT_HA_URL = "http://127.0.0.1:8123"
DEFAULT_TOKEN_FILE = Path.home() / ".local/state/mark/home_assistant.token"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 15.0
DEFAULT_STALE_SECONDS = 3600
MAX_HA_PAYLOAD_BYTES = 512 * 1024

ALLOWED_HA_DOMAINS = frozenset({
    "sensor",
    "binary_sensor",
    "light",
    "switch",
    "climate",
    "cover",
    "fan",
    "lock",
    "media_player",
    "weather",
    "person",
    "device_tracker",
    "sun",
    "vacuum",
    "water_heater",
    "humidifier",
    "air_quality",
})

BLOCKED_HA_DOMAINS = frozenset({
    "script",
    "automation",
    "shell_command",
    "command_line",
    "notify",
    "homeassistant",
    "hassio",
    "persistent_notification",
    "scene",
    "button",
    "input_button",
})

DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "luz": ("light",),
    "luzes": ("light",),
    "lampada": ("light",),
    "lampadas": ("light",),
    "iluminacao": ("light",),
    "temperatura": ("sensor", "climate", "weather"),
    "umidade": ("sensor", "humidifier", "weather"),
    "clima": ("climate", "weather", "sensor"),
    "ar condicionado": ("climate",),
    "termostato": ("climate",),
    "porta": ("binary_sensor", "lock", "cover"),
    "portas": ("binary_sensor", "lock", "cover"),
    "janela": ("binary_sensor", "cover"),
    "janelas": ("binary_sensor", "cover"),
    "tranca": ("lock",),
    "fechadura": ("lock",),
    "energia": ("sensor",),
    "consumo": ("sensor",),
    "bateria": ("sensor", "binary_sensor"),
    "presenca": ("binary_sensor", "person", "device_tracker"),
    "movimento": ("binary_sensor",),
    "tomada": ("switch",),
    "interruptor": ("switch",),
    "ventilador": ("fan",),
    "persiana": ("cover",),
    "cortina": ("cover",),
    "garagem": ("cover", "binary_sensor"),
    "tv": ("media_player",),
    "midia": ("media_player",),
    "aspirador": ("vacuum",),
}

ROOM_ALIASES: dict[str, tuple[str, ...]] = {
    "sala": ("sala", "living", "living_room", "estar"),
    "quarto": ("quarto", "bedroom", "suite", "dormitorio"),
    "cozinha": ("cozinha", "kitchen", "copa"),
    "escritorio": ("escritorio", "office", "estudo", "lab"),
    "banheiro": ("banheiro", "bathroom", "wc", "lavabo"),
    "garagem": ("garagem", "garage"),
    "varanda": ("varanda", "balcony", "sacada", "quintal", "externo", "jardim"),
    "corredor": ("corredor", "hall", "hallway", "entrada"),
    "lavanderia": ("lavanderia", "laundry", "servico"),
}

_TOKEN_SCRUB_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._\-]+|eyJ[A-Za-z0-9._\-]{10,}|(?:token|auth|key|password|access_token)\s*[:=]\s*[^\s&\"',]+|[?&](?:token|auth|key|password|access_token)=[^\s&\"',]+)"
)


class HomeAssistantError(RuntimeError):
    """Erro base seguro (sem vazamento de token) para falhas do conector Home Assistant."""


class HomeAssistantAuthError(HomeAssistantError):
    """Credenciais ausentes, inválidas (401/403) ou arquivo de token inseguro."""


class HomeAssistantSecurityError(PermissionError):
    """Tentativa de escrita/mutação, domínio restrito ou URL não confiável."""


@dataclass(frozen=True)
class EntitySummary:
    entity_id: str
    domain: str
    friendly_name: str
    state: str
    unit: str = ""
    room: str = ""
    device_class: str = ""
    available: bool = True
    stale: bool = False
    last_updated: str = ""
    age_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact_line(self) -> str:
        status_flags = []
        if not self.available:
            status_flags.append("INDISPONÍVEL")
        elif self.stale:
            status_flags.append("DESATUALIZADO")
        value = f"{self.state} {self.unit}".strip() if self.available else self.state
        room_tag = f"[{self.room}] " if self.room else ""
        flag_tag = f" ({', '.join(status_flags)})" if status_flags else ""
        return f"- {room_tag}{self.friendly_name} (`{self.entity_id}`): {value}{flag_tag}"

BLOCKED_IP_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local / Cloud metadata (AWS, GCP, Azure)
    ipaddress.ip_network("224.0.0.0/4"),        # IPv4 Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved
    ipaddress.ip_network("255.255.255.255/32"), # Broadcast
    ipaddress.ip_network("0.0.0.0/8"),          # Unspecified / current network
    ipaddress.ip_network("fe80::/10"),          # IPv6 Link-local
    ipaddress.ip_network("ff00::/8"),           # IPv6 Multicast
    ipaddress.ip_network("::/128"),             # IPv6 Unspecified
)

BLOCKED_HA_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
    "169.254.169.254",
    "169.254.169.253",
    "0.0.0.0",  # nosec: B104
    "::",
})


def is_loopback_host(hostname: str) -> bool:
    clean = hostname.strip().lower().strip("[]")
    if clean in {"127.0.0.1", "::1", "localhost", "localhost.localdomain"}:
        return True
    try:
        ip = ipaddress.ip_address(clean)
        return ip.is_loopback
    except ValueError:
        return False


def validate_ssrf_host(hostname: str, *, allow_loopback: bool = True) -> None:
    clean = hostname.strip().lower().strip("[]")
    if not clean:
        raise HomeAssistantSecurityError("Host do Home Assistant não pode ser vazio.")
    if clean in BLOCKED_HA_HOSTNAMES:
        raise HomeAssistantSecurityError(f"Acesso bloqueado ao host restrito por política SSRF: {hostname}")
    if "metadata" in clean or "169.254" in clean:
        raise HomeAssistantSecurityError(f"Host suspeito de metadados de nuvem bloqueado (SSRF): {hostname}")

    try:
        ip = ipaddress.ip_address(clean)
        if ip.is_loopback:
            if not allow_loopback:
                raise HomeAssistantSecurityError(f"Acesso a loopback desativado pela política de segurança: {clean}")
            return
        for net in BLOCKED_IP_NETWORKS:
            if ip in net:
                raise HomeAssistantSecurityError(f"Endereço IP {clean} bloqueado pela política de proteção contra SSRF ({net}).")
    except ValueError:
        pass


def scrub_ha_secrets(text: str) -> str:
    if not isinstance(text, str) or not text:
        return ""
    return _TOKEN_SCRUB_RE.sub("[REDACTED_HA_TOKEN]", text)


def validate_ha_base_url(url: str, *, require_tls: bool = False, allow_loopback: bool = True) -> str:
    """Valida a URL do Home Assistant prevenindo SSRF, credenciais e exigindo TLS para conexões."""
    if not isinstance(url, str) or not url.strip():
        raise HomeAssistantSecurityError("URL do Home Assistant ausente ou inválida.")
    cleaned = url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HomeAssistantSecurityError("URL do Home Assistant deve usar HTTP ou HTTPS com host válido.")
    if parsed.username or parsed.password:
        raise HomeAssistantSecurityError("URL do Home Assistant não pode conter credenciais na string de conexão.")
    if parsed.query or parsed.fragment:
        raise HomeAssistantSecurityError("URL base do Home Assistant não deve conter query string ou fragmentos.")

    validate_ssrf_host(parsed.hostname, allow_loopback=allow_loopback)

    is_loopback = is_loopback_host(parsed.hostname)
    if require_tls and parsed.scheme != "https":
        raise HomeAssistantSecurityError("Obrigatoriedade de TLS: conexões com Home Assistant devem utilizar HTTPS.")
    if not is_loopback and parsed.scheme != "https":
        raise HomeAssistantSecurityError("Obrigatoriedade de TLS: conexões remotas com Home Assistant devem utilizar HTTPS.")

    return cleaned


def save_ha_token(token: str, path: str | Path = DEFAULT_TOKEN_FILE) -> Path:
    """Salva o token de acesso do Home Assistant localmente com permissão estrita 0600."""
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Token do Home Assistant não pode ser vazio.")
    clean_token = token.strip()
    if "\n" in clean_token or "\r" in clean_token or "\x00" in clean_token or len(clean_token) < 8:
        raise ValueError("Formato de token do Home Assistant inválido.")
    target = private_file(Path(path))
    target.write_text(clean_token + "\n", encoding="utf-8")
    target.chmod(0o600)
    return target


def load_ha_token(path: str | Path = DEFAULT_TOKEN_FILE) -> str:
    """Carrega o token local do Home Assistant verificando propriedade do usuário e modo 0600."""
    target = Path(path)
    if target.is_symlink():
        raise HomeAssistantAuthError("Symlink não é permitido para o arquivo de token do Home Assistant.")
    if not target.exists():
        raise HomeAssistantAuthError("Token local do Home Assistant não encontrado.")
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or (info.st_mode & 0o077):
        raise HomeAssistantAuthError("Arquivo de token do Home Assistant deve pertencer ao usuário e ter permissão 0600.")
    token = target.read_text(encoding="utf-8").strip()
    if not token or len(token) < 8:
        raise HomeAssistantAuthError("Token do Home Assistant está vazio ou truncado.")
    return token


def _infer_room(entity_id: str, attributes: dict[str, Any]) -> str:
    explicit_area = str(attributes.get("area") or attributes.get("room") or attributes.get("area_name") or "").strip()
    if explicit_area:
        return explicit_area
    haystack = normalize(f"{entity_id} {attributes.get('friendly_name', '')}")
    for canonical, aliases in ROOM_ALIASES.items():
        if any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", haystack) for alias in aliases):
            return canonical
    return ""


def _parse_iso_timestamp(value: str) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


class HomeAssistantClient:
    """Cliente local e estritamente somente leitura para a API REST do Home Assistant."""

    READ_ONLY = True

    def __init__(
        self,
        base_url: str = DEFAULT_HA_URL,
        *,
        token_path: str | Path = DEFAULT_TOKEN_FILE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        stale_seconds: int = DEFAULT_STALE_SECONDS,
        allowed_domains: set[str] | frozenset[str] | None = None,
        require_tls: bool = False,
    ):
        self.require_tls = require_tls
        self.base_url = validate_ha_base_url(base_url, require_tls=require_tls)
        self.token_path = Path(token_path)
        if timeout <= 0:
            raise ValueError("Timeout deve ser positivo.")
        self.timeout = min(float(timeout), MAX_TIMEOUT_SECONDS)
        self.stale_seconds = max(60, int(stale_seconds))
        if allowed_domains is not None:
            normalized_domains = {d.strip().lower() for d in allowed_domains if isinstance(d, str) and d.strip()}
            forbidden = normalized_domains & BLOCKED_HA_DOMAINS
            if forbidden:
                raise HomeAssistantSecurityError(
                    f"Domínios de mutação/execução são estritamente proibidos no Home Assistant: {sorted(forbidden)}"
                )
            self.allowed_domains = frozenset(normalized_domains)
        else:
            self.allowed_domains = ALLOWED_HA_DOMAINS

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> HomeAssistantClient:
        ha_cfg = cfg.get("home_assistant", {}) if isinstance(cfg, dict) else {}
        url = str(ha_cfg.get("url") or os.environ.get("MARK_HA_URL") or DEFAULT_HA_URL)
        token_file = Path(ha_cfg.get("token_file") or os.environ.get("MARK_HA_TOKEN_FILE") or DEFAULT_TOKEN_FILE)
        timeout = float(ha_cfg.get("timeout", DEFAULT_TIMEOUT_SECONDS))
        stale_seconds = int(ha_cfg.get("stale_seconds", DEFAULT_STALE_SECONDS))
        domains = ha_cfg.get("allowed_domains")
        require_tls = bool(ha_cfg.get("require_tls", False))
        return cls(
            url,
            token_path=token_file,
            timeout=timeout,
            stale_seconds=stale_seconds,
            allowed_domains=set(domains) if isinstance(domains, list) else None,
            require_tls=require_tls,
        )

    def _validate_domain(self, domain: str) -> str:
        clean = (domain or "").strip().lower()
        if not clean or not re.fullmatch(r"[a-z0-9_]{2,48}", clean):
            raise HomeAssistantSecurityError(f"Domínio Home Assistant inválido: {domain!r}")
        if clean in BLOCKED_HA_DOMAINS:
            raise HomeAssistantSecurityError(f"Domínio restrito/mutável bloqueado pela política somente leitura: {clean}")
        if clean not in self.allowed_domains:
            raise HomeAssistantSecurityError(f"Domínio não autorizado na allowlist do Home Assistant: {clean}")
        return clean

    def _validate_entity_id(self, entity_id: str) -> tuple[str, str]:
        if not isinstance(entity_id, str) or "." not in entity_id:
            raise ValueError("Identificador de entidade inválido; esperado formato 'dominio.nome'.")
        domain, object_id = entity_id.strip().lower().split(".", 1)
        self._validate_domain(domain)
        if not re.fullmatch(r"[a-z0-9_]{1,128}", object_id):
            raise ValueError(f"Nome de objeto inválido na entidade: {entity_id!r}")
        return f"{domain}.{object_id}", domain

    def _request_get(self, endpoint: str, *, method: str = "GET") -> Any:
        if method.upper() != "GET":
            raise HomeAssistantSecurityError(
                f"Operação '{method}' bloqueada: o conector Home Assistant do MARK é estritamente SOMENTE LEITURA."
            )
        if not endpoint.startswith("/api/"):
            raise HomeAssistantSecurityError("Endpoint inválido para a API do Home Assistant.")
        blocked_prefixes = (
            "/api/services/",
            "/api/events/",
            "/api/template",
            "/api/config",
            "/api/webhook",
            "/api/intent/",
            "/api/conversation/",
        )
        if any(b in endpoint.lower() for b in blocked_prefixes):
            raise HomeAssistantSecurityError("Endpoints de acionamento de serviço, eventos ou templates são proibidos.")

        token = load_ha_token(self.token_path)
        url = self.base_url + endpoint
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "MARK-HomeAssistant-ReadOnly/1.0",
            },
        )
        validate_url_scheme(req)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:  # nosec: B310
                raw = response.read(MAX_HA_PAYLOAD_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = getattr(exc, "code", 0)
            if status in (401, 403):
                raise HomeAssistantAuthError(
                    f"Autenticação recusada pelo Home Assistant (HTTP {status}). Verifique o token local."
                ) from None
            if status == 404:
                raise HomeAssistantError("Entidade ou endpoint não encontrado no Home Assistant (HTTP 404).") from None
            raise HomeAssistantError(f"Home Assistant retornou erro HTTP {status}.") from None
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError(
                f"Timeout ao consultar o Home Assistant local após {self.timeout:.1f}s."
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise TimeoutError(
                    f"Timeout ao consultar o Home Assistant local após {self.timeout:.1f}s."
                ) from exc
            safe_reason = scrub_ha_secrets(str(reason))
            raise HomeAssistantError(f"Falha de conexão com o Home Assistant local: {safe_reason}") from None
        except OSError as exc:
            safe_err = scrub_ha_secrets(str(exc))
            raise HomeAssistantError(f"Erro de rede ao acessar o Home Assistant: {safe_err}") from None

        if len(raw) > MAX_HA_PAYLOAD_BYTES:
            raise HomeAssistantError("Resposta do Home Assistant excedeu o limite seguro de 512 KB.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HomeAssistantError("Resposta JSON inválida recebida do Home Assistant.") from exc

    def call_service(self, *args, **kwargs) -> None:
        """Bloqueio explícito contra qualquer tentativa de mutação de estado."""
        raise HomeAssistantSecurityError(
            "Acionamento de serviços (call_service) é proibido: conector Home Assistant opera em modo SOMENTE LEITURA."
        )

    def _to_summary(self, item: dict[str, Any], now: datetime | None = None) -> EntitySummary | None:
        if not isinstance(item, dict):
            return None
        raw_id = str(item.get("entity_id") or "").strip().lower()
        if "." not in raw_id:
            return None
        domain = raw_id.split(".", 1)[0]
        if domain in BLOCKED_HA_DOMAINS or domain not in self.allowed_domains:
            return None

        raw_state = str(item.get("state") if item.get("state") is not None else "unknown").strip()
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        friendly_name = str(attrs.get("friendly_name") or raw_id).strip()[:160]
        unit = str(attrs.get("unit_of_measurement") or "").strip()[:32]
        device_class = str(attrs.get("device_class") or "").strip()[:64]
        room = _infer_room(raw_id, attrs)

        lowered_state = raw_state.lower()
        available = lowered_state not in {"unavailable", "unknown", "none", ""}
        last_updated = str(item.get("last_updated") or item.get("last_changed") or "").strip()

        age_seconds: float | None = None
        stale = False
        ts = _parse_iso_timestamp(last_updated)
        if ts is not None:
            ref_now = now or datetime.now(timezone.utc)
            age_seconds = max(0.0, round((ref_now - ts).total_seconds(), 1))
            if age_seconds > self.stale_seconds:
                stale = True

        return EntitySummary(
            entity_id=raw_id,
            domain=domain,
            friendly_name=friendly_name,
            state=raw_state[:120],
            unit=unit,
            room=room,
            device_class=device_class,
            available=available,
            stale=stale,
            last_updated=last_updated[:64],
            age_seconds=age_seconds,
        )

    def get_entity_state(self, entity_id: str, *, now: datetime | None = None) -> EntitySummary:
        """Consulta o estado de uma única entidade permitida."""
        clean_id, _ = self._validate_entity_id(entity_id)
        payload = self._request_get(f"/api/states/{urllib.parse.quote(clean_id, safe='.')}")
        summary = self._to_summary(payload, now=now)
        if summary is None:
            raise HomeAssistantError(f"Não foi possível interpretar o estado da entidade {clean_id}.")
        return summary

    def query_entities(
        self,
        *,
        room: str | None = None,
        domain: str | None = None,
        query: str | None = None,
        include_unavailable: bool = True,
        limit: int = 25,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Filtra entidades sob demanda por cômodo, domínio ou termo de busca e gera um sumário compacto."""
        if not 1 <= int(limit) <= 100:
            raise ValueError("O limite de entidades deve estar entre 1 e 100.")

        target_domains: set[str] = set()
        if domain:
            norm_dom = normalize(domain).strip()
            if norm_dom in DOMAIN_ALIASES:
                for mapped in DOMAIN_ALIASES[norm_dom]:
                    target_domains.add(self._validate_domain(mapped))
            else:
                target_domains.add(self._validate_domain(norm_dom))

        room_terms: tuple[str, ...] = ()
        canonical_room = ""
        if room and room.strip():
            norm_room = normalize(room).strip()
            canonical_room = norm_room
            room_terms = ROOM_ALIASES.get(norm_room, (norm_room,))

        query_terms: list[str] = []
        if query and query.strip():
            norm_q = normalize(query).strip()
            for alias_key, mapped_domains in DOMAIN_ALIASES.items():
                if re.search(r"(?<!\w)" + re.escape(alias_key) + r"(?!\w)", norm_q):
                    for d in mapped_domains:
                        if d in self.allowed_domains:
                            target_domains.add(d)
            for room_key, aliases in ROOM_ALIASES.items():
                if not room_terms and any(re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", norm_q) for a in aliases):
                    canonical_room = room_key
                    room_terms = aliases
            stop = {
                "como", "esta", "estao", "qual", "quais", "status", "estado", "da", "do", "de",
                "na", "no", "nas", "nos", "em", "casa", "home", "assistant", "mark", "por", "favor",
                "mostre", "ver", "verificar", "consultar", "sensor", "sensores",
            }
            for w in re.findall(r"[a-z0-9_]{2,}", norm_q):
                if w not in stop and w not in ROOM_ALIASES and w not in DOMAIN_ALIASES:
                    query_terms.append(w)

        raw_states = self._request_get("/api/states")
        if not isinstance(raw_states, list):
            raise HomeAssistantError("Formato inesperado retornado por /api/states.")

        ref_now = now or datetime.now(timezone.utc)
        matched: list[EntitySummary] = []
        unavailable_count = 0
        stale_count = 0

        for item in raw_states:
            summary = self._to_summary(item, now=ref_now)
            if summary is None:
                continue
            if target_domains and summary.domain not in target_domains:
                continue
            haystack = normalize(f"{summary.entity_id} {summary.friendly_name} {summary.room} {summary.device_class}")
            if room_terms:
                if summary.room != canonical_room and not any(
                    re.search(r"(?<!\w)" + re.escape(rt) + r"(?!\w)", haystack) for rt in room_terms
                ):
                    continue
            if query_terms and not all(term in haystack for term in query_terms):
                continue
            if not summary.available:
                unavailable_count += 1
                if not include_unavailable:
                    continue
            if summary.stale:
                stale_count += 1
            matched.append(summary)

        matched.sort(key=lambda e: (not e.available, e.stale, e.room or "zzz", e.domain, e.friendly_name.lower()))
        selected = matched[: int(limit)]

        lines = [item.compact_line() for item in selected]
        header_parts = [f"Entidades retornadas: {len(selected)} de {len(matched)}"]
        if canonical_room:
            header_parts.append(f"cômodo: {canonical_room}")
        if target_domains:
            header_parts.append(f"domínios: {', '.join(sorted(target_domains))}")
        if unavailable_count:
            header_parts.append(f"indisponíveis: {unavailable_count}")
        if stale_count:
            header_parts.append(f"desatualizadas: {stale_count}")

        compact_summary = (
            "Resumo Home Assistant (somente leitura — " + " · ".join(header_parts) + "):\n" + "\n".join(lines)
            if lines
            else "Nenhuma entidade correspondente foi encontrada no Home Assistant para os filtros solicitados."
        )

        return {
            "count": len(selected),
            "total_matched": len(matched),
            "unavailable_count": unavailable_count,
            "stale_count": stale_count,
            "entities": [e.to_dict() for e in selected],
            "summary": compact_summary,
            "read_only": True,
        }


HA_MCP_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "ha_query_entities": {
        "description": "Consulta sob demanda entidades locais do Home Assistant por cômodo, domínio ou busca textual (somente leitura).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "room": {"type": "string", "minLength": 1, "maxLength": 64},
                "domain": {"type": "string", "minLength": 1, "maxLength": 64},
                "query": {"type": "string", "minLength": 1, "maxLength": 160},
                "include_unavailable": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    "ha_get_entity_state": {
        "description": "Obtém o estado detalhado e status de disponibilidade/atualidade de uma entidade específica do Home Assistant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "minLength": 3, "maxLength": 160},
            },
            "required": ["entity_id"],
            "additionalProperties": False,
        },
    },
}


HA_TOOL_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "ha_query_entities": ("home_assistant.read",),
    "ha_get_entity_state": ("home_assistant.read",),
}


class HomeAssistantMCPBridge:
    """Ponte MCP para o conector somente leitura do Home Assistant."""

    def __init__(
        self,
        client: HomeAssistantClient | None = None,
        *,
        cfg: dict[str, Any] | None = None,
        require_explicit_capabilities: bool = False,
    ):
        self.client = client or HomeAssistantClient.from_config(cfg or {})
        self.capability_guard = BridgeCapabilityGuard(
            "home_assistant",
            HA_TOOL_PERMISSIONS,
            strict_mode=require_explicit_capabilities,
        )

    def issue_mission_capability(
        self,
        mission_id: str,
        *,
        allowed_tools: list[str] | set[str] | tuple[str, ...],
        permissions: list[str] | set[str] | tuple[str, ...] | None = None,
        ttl_seconds: float = 900.0,
        allow_privileged: bool = False,
    ) -> CapabilityToken:
        return self.capability_guard.issue_mission_capability(
            mission_id,
            allowed_tools=allowed_tools,
            permissions=permissions,
            ttl_seconds=ttl_seconds,
            allow_privileged=allow_privileged,
        )

    def get_mcp_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
            for name, spec in sorted(HA_MCP_TOOL_SCHEMAS.items())
        ]

    def validate_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        if name not in HA_MCP_TOOL_SCHEMAS:
            raise HomeAssistantSecurityError(f"Ferramenta MCP Home Assistant não autorizada: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Os argumentos devem ser um objeto JSON.")
        self.capability_guard.check(name, arguments, grants=grants, mission_id=mission_id)
        _schema_check(HA_MCP_TOOL_SCHEMAS[name]["inputSchema"], arguments)
        return arguments

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        args = self.validate_call(name, arguments, grants=grants, mission_id=mission_id)
        if name == "ha_query_entities":
            result = self.client.query_entities(
                room=args.get("room"),
                domain=args.get("domain"),
                query=args.get("query"),
                include_unavailable=bool(args.get("include_unavailable", True)),
                limit=int(args.get("limit", 25)),
            )
            return {"tool": name, "status": "ok", "result": result}
        if name == "ha_get_entity_state":
            summary = self.client.get_entity_state(args["entity_id"])
            return {
                "tool": name,
                "status": "ok",
                "result": {
                    "entity": summary.to_dict(),
                    "summary": summary.compact_line(),
                    "read_only": True,
                },
            }
        raise HomeAssistantSecurityError(f"Ferramenta não suportada: {name}")

    def register_in_cognition_registry(self, registry: ToolRegistry | None = None) -> ToolRegistry:
        target = registry if registry is not None else ToolRegistry()
        output_schema = {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "maxLength": 128},
                "status": {"type": "string", "maxLength": 64},
                "result": {"type": "object"},
            },
            "required": ["tool", "status", "result"],
            "additionalProperties": False,
        }
        for tool in self.get_mcp_tools():
            t_name = tool["name"]

            def _make_runner(tool_name: str):
                def _runner(**kwargs):
                    return self.execute_tool(tool_name, kwargs)
                return _runner

            contract = ToolContract(
                name=f"home_assistant.{t_name}",
                description=tool["description"],
                input_schema=tool["inputSchema"],
                output_schema=output_schema,
                risk_level="READ_ONLY",
                timeout=self.client.timeout,
                permissions=("home_assistant.read",),
            )
            target.register(contract, _make_runner(t_name))
        return target
