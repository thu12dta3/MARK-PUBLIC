"""Sistema de Contratos de Ferramentas e Autorização Granular por Capacidade (Capability-Based Access Control - P0).

Impõe permissões explícitas por ferramenta e por missão (CapabilityToken / AuthorizationLayer),
garantindo que o simples registro de uma ferramenta MCP jamais conceda autorização automática para
leitura de arquivos privados, manipulação de segredos ou execução de ações privilegiadas no sistema.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, asdict
import hashlib
import hmac
import math
import re
import secrets
import time
from typing import Any, Iterable, Mapping

from . import chemistry

FORBIDDEN_ARGUMENT_VALUE_PATTERNS = (
    re.compile(r"(?i)(?:/etc/shadow|/etc/gshadow|/etc/sudoers|\.ssh/id_|mobile\.token|smart_home_vault\.(?:key|enc)|mobile-devices\.db)"),
    re.compile(r"(?:^|/)\.\.(?:/|$)"),
)

FORBIDDEN_ARGUMENT_KEYS = frozenset({
    "raw_secret",
    "master_key",
    "private_key",
    "sudo_password",
    "bypass_auth",
})


class ToolCapabilityError(PermissionError):
    """Violação de Capability-Based Access Control (permissão de ferramenta ou missão ausente, expirada ou escopo negado)."""


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    risk_level: str
    timeout: float
    permissions: tuple[str, ...]
    side_effects: tuple[str, ...] = ()
    mission_scoped: bool = False
    modes: tuple[str, ...] = ("general", "professor", "engineer")


@dataclass(frozen=True)
class ActionRequest:
    tool: str
    arguments: dict
    mission_id: str | None = None


@dataclass(frozen=True)
class CapabilityToken:
    """Token criptográfico de capacidade explícita (Capability-Based Access Control) vinculado a missão e ferramentas."""

    mission_id: str
    allowed_tools: frozenset[str]
    permissions: frozenset[str]
    expires_at: float
    allow_privileged: bool = False
    signature: str = ""

    def is_expired(self, now: float | None = None) -> bool:
        ref = time.time() if now is None else float(now)
        return ref >= self.expires_at

    def Verify(self, secret: bytes) -> bool:
        expected = _sign_capability(
            secret,
            self.mission_id,
            self.allowed_tools,
            self.permissions,
            self.expires_at,
            self.allow_privileged,
        )
        return hmac.compare_digest(self.signature.encode("ascii"), expected.encode("ascii"))


def _sign_capability(
    secret: bytes,
    mission_id: str,
    allowed_tools: frozenset[str],
    permissions: frozenset[str],
    expires_at: float,
    allow_privileged: bool,
) -> str:
    tools_part = ",".join(sorted(allowed_tools))
    perms_part = ",".join(sorted(permissions))
    payload = f"MARK-CAP-V1|{mission_id}|{tools_part}|{perms_part}|{expires_at:.3f}|{int(allow_privileged)}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def inspect_arguments_for_private_or_secret_access(tool_name: str, arguments: Mapping[str, Any]) -> None:
    """Bloqueia qualquer tentativa de usar argumentos de ferramenta MCP para ler arquivos privados ou manipular segredos."""
    if not isinstance(arguments, Mapping):
        raise ValueError("Argumentos devem ser um dicionário/objeto.")
    for key, val in arguments.items():
        key_str = str(key).strip().lower()
        if key_str in FORBIDDEN_ARGUMENT_KEYS:
            raise ToolCapabilityError(
                f"Manipulação direta de segredo ou parâmetro privilegiado '{key}' bloqueada na ferramenta '{tool_name}'."
            )
        if isinstance(val, str):
            if "\x00" in val:
                raise ToolCapabilityError(f"Byte nulo bloqueado nos argumentos da ferramenta '{tool_name}'.")
            for pattern in FORBIDDEN_ARGUMENT_VALUE_PATTERNS:
                if pattern.search(val):
                    raise ToolCapabilityError(
                        f"Acesso a arquivo privado, credencial ou path traversal bloqueado na ferramenta '{tool_name}': {val}"
                    )
        elif isinstance(val, Mapping):
            inspect_arguments_for_private_or_secret_access(tool_name, val)
        elif isinstance(val, (list, tuple)):
            for item in val:
                if isinstance(item, str):
                    for pattern in FORBIDDEN_ARGUMENT_VALUE_PATTERNS:
                        if pattern.search(item):
                            raise ToolCapabilityError(
                                f"Acesso a arquivo privado ou path traversal bloqueado na ferramenta '{tool_name}': {item}"
                            )
                elif isinstance(item, Mapping):
                    inspect_arguments_for_private_or_secret_access(tool_name, item)


class AuthorizationLayer:
    """Camada de autorização granular baseada em capacidades por ferramenta e por missão."""

    def __init__(self, signing_secret: bytes | None = None):
        self._secret = signing_secret or secrets.token_bytes(32)
        self._mission_policies: dict[str, CapabilityToken] = {}

    def issue_capability(
        self,
        mission_id: str,
        *,
        allowed_tools: Iterable[str],
        permissions: Iterable[str],
        ttl_seconds: float = 900.0,
        allow_privileged: bool = False,
    ) -> CapabilityToken:
        if not isinstance(mission_id, str) or not mission_id.strip():
            raise ValueError("mission_id obrigatório para emitir CapabilityToken.")
        if ttl_seconds <= 0 or ttl_seconds > 86400:
            raise ValueError("ttl_seconds deve estar entre 1s e 86400s.")
        clean_mid = mission_id.strip()
        tools_set = frozenset(str(t).strip() for t in allowed_tools if str(t).strip())
        perms_set = frozenset(str(p).strip() for p in permissions if str(p).strip())
        if not tools_set or not perms_set:
            raise ValueError("CapabilityToken exige ao menos uma ferramenta e uma permissão explícita.")
        expires_at = time.time() + float(ttl_seconds)
        sig = _sign_capability(self._secret, clean_mid, tools_set, perms_set, expires_at, allow_privileged)
        token = CapabilityToken(
            mission_id=clean_mid,
            allowed_tools=tools_set,
            permissions=perms_set,
            expires_at=expires_at,
            allow_privileged=bool(allow_privileged),
            signature=sig,
        )
        self._mission_policies[clean_mid] = token
        return token

    def revoke_mission_capabilities(self, mission_id: str) -> None:
        self._mission_policies.pop(mission_id, None)

    def validate_registration(self, contract: ToolContract, allow_privileged: bool = False) -> None:
        """Valida o contrato durante o registro (sem conceder permissão de execução automática)."""
        if not contract.permissions or any(not isinstance(p, str) or not p.strip() for p in contract.permissions):
            raise ValueError("Contrato de ferramenta exige permissões explícitas.")
        if (contract.risk_level != "READ_ONLY" or contract.side_effects) and not allow_privileged:
            raise PermissionError("Esta fase só permite registrar ferramentas READ_ONLY sem side_effects no catálogo padrão.")

    def authorize(
        self,
        contract: ToolContract,
        grants: frozenset[str] | set[str] | CapabilityToken,
        *,
        mission_id: str | None = None,
        arguments: Mapping[str, Any] | None = None,
        allow_privileged: bool = False,
    ) -> None:
        if arguments is not None:
            inspect_arguments_for_private_or_secret_access(contract.name, arguments)

        if isinstance(grants, CapabilityToken):
            if not grants.Verify(self._secret):
                raise ToolCapabilityError("Assinatura criptográfica do CapabilityToken inválida.")
            if grants.is_expired():
                raise ToolCapabilityError("CapabilityToken da missão expirou.")
            if mission_id and grants.mission_id != mission_id:
                raise ToolCapabilityError(
                    f"CapabilityToken pertence à missão '{grants.mission_id}', não à missão solicitada '{mission_id}'."
                )
            if (
                contract.name not in grants.allowed_tools
                and "*" not in grants.allowed_tools
                and f"{contract.name.split('.', 1)[0]}.*" not in grants.allowed_tools
            ):
                raise ToolCapabilityError(
                    f"Ferramenta '{contract.name}' não está autorizada no CapabilityToken da missão '{grants.mission_id}'."
                )
            effective_grants = grants.permissions
            allow_privileged = allow_privileged or grants.allow_privileged
        else:
            effective_grants = frozenset(grants or ())
            if mission_id is not None:
                policy = self._mission_policies.get(mission_id)
                if policy is None:
                    raise ToolCapabilityError(f"Missão '{mission_id}' não possui CapabilityToken ativo registrado.")
                self.authorize(contract, policy, mission_id=mission_id, arguments=arguments, allow_privileged=allow_privileged)
                return

        if not effective_grants:
            raise ToolCapabilityError(
                f"Execução negada para '{contract.name}': nenhuma permissão (grant/capability) fornecida."
            )

        required_perms = set(contract.permissions)
        if not required_perms <= effective_grants:
            raise ToolCapabilityError(
                f"Permissão de tool ausente para '{contract.name}': requer {sorted(required_perms)}."
            )

        # Se os grants incluem restrição explícita por nome de tool (`tool:<nome>`), a tool invocada deve constar nela
        explicit_tool_grants = {g.removeprefix("tool:") for g in effective_grants if g.startswith("tool:")}
        if explicit_tool_grants and contract.name not in explicit_tool_grants:
            raise ToolCapabilityError(
                f"Escopo de capacidade por ferramenta bloqueou '{contract.name}' (permitidas: {sorted(explicit_tool_grants)})."
            )

        if contract.mission_scoped and not mission_id and not isinstance(grants, CapabilityToken):
            raise ToolCapabilityError(
                f"A ferramenta '{contract.name}' exige escopo explícito de missão (CapabilityToken ou mission_id)."
            )

        if (contract.risk_level != "READ_ONLY" or contract.side_effects) and not allow_privileged:
            raise PermissionError("Esta fase só permite leitura/cálculo sem privilégio de mutação.")


class BridgeCapabilityGuard:
    """Guarda de autorização granular para pontes MCP (Sandbox, SmartHome, HomeAssistant, Backup, ReverseEngine, GodsEye)."""

    def __init__(
        self,
        bridge_domain: str,
        tool_permissions: Mapping[str, tuple[str, ...]],
        *,
        privileged_tools: frozenset[str] = frozenset(),
        strict_mode: bool = False,
        audit_logger: Any = None,
    ):
        self.bridge_domain = bridge_domain
        self.tool_permissions = {k: tuple(v) for k, v in tool_permissions.items()}
        self.privileged_tools = frozenset(privileged_tools)
        self.strict_mode = bool(strict_mode)
        self.authorizer = AuthorizationLayer()
        self.audit_logger = audit_logger

    def issue_mission_capability(
        self,
        mission_id: str,
        *,
        allowed_tools: Iterable[str],
        permissions: Iterable[str] | None = None,
        ttl_seconds: float = 900.0,
        allow_privileged: bool = False,
    ) -> CapabilityToken:
        tools_list = list(allowed_tools)
        if permissions is None:
            inferred: set[str] = set()
            for t in tools_list:
                inferred.update(self.tool_permissions.get(t, ()))
            perms_iter: Iterable[str] = inferred
        else:
            perms_iter = permissions
        return self.authorizer.issue_capability(
            mission_id,
            allowed_tools=tools_list,
            permissions=perms_iter,
            ttl_seconds=ttl_seconds,
            allow_privileged=allow_privileged,
        )

    def check(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
        device_id: str = "local",
    ) -> None:
        try:
            self._do_check(tool_name, arguments, grants=grants, mission_id=mission_id)
            if self.audit_logger is not None:
                self.audit_logger.log_auth_decision(
                    device_id=device_id,
                    target=f"{self.bridge_domain}.{tool_name}",
                    allowed=True,
                    metadata={"mission_id": mission_id or "unspecified"},
                )
        except Exception as exc:
            if self.audit_logger is not None:
                self.audit_logger.log_auth_decision(
                    device_id=device_id,
                    target=f"{self.bridge_domain}.{tool_name}",
                    allowed=False,
                    reason=str(exc),
                    metadata={"mission_id": mission_id or "unspecified"},
                )
            raise

    def _do_check(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> None:
        if tool_name not in self.tool_permissions:
            raise ToolCapabilityError(f"Ferramenta MCP '{tool_name}' não registrada na ponte '{self.bridge_domain}'.")

        inspect_arguments_for_private_or_secret_access(tool_name, arguments)
        required = set(self.tool_permissions[tool_name])
        is_privileged = tool_name in self.privileged_tools

        if grants is None and mission_id is None:
            if self.strict_mode:
                raise ToolCapabilityError(
                    f"Autorização explícita (CapabilityToken ou grants) obrigatória na ponte '{self.bridge_domain}' para '{tool_name}'."
                )
            return

        if isinstance(grants, CapabilityToken):
            if not grants.Verify(self.authorizer._secret):
                raise ToolCapabilityError("Assinatura do CapabilityToken inválida na ponte MCP.")
            if grants.is_expired():
                raise ToolCapabilityError("CapabilityToken expirado na ponte MCP.")
            if mission_id and grants.mission_id != mission_id:
                raise ToolCapabilityError("Conflito de mission_id no CapabilityToken da ponte MCP.")
            if (
                tool_name not in grants.allowed_tools
                and f"{self.bridge_domain}.{tool_name}" not in grants.allowed_tools
                and "*" not in grants.allowed_tools
            ):
                raise ToolCapabilityError(
                    f"Ferramenta MCP '{tool_name}' não autorizada para a missão '{grants.mission_id}'."
                )
            if not required <= grants.permissions:
                raise ToolCapabilityError(
                    f"Permissões insuficientes no CapabilityToken para '{tool_name}': requer {sorted(required)}."
                )
            if is_privileged and not grants.allow_privileged:
                raise ToolCapabilityError(
                    f"Ação privilegiada '{tool_name}' negada: CapabilityToken não possui allow_privileged=True."
                )
            return

        if mission_id is not None and grants is None:
            policy = self.authorizer._mission_policies.get(mission_id)
            if policy is None:
                raise ToolCapabilityError(f"Missão '{mission_id}' sem CapabilityToken ativo na ponte '{self.bridge_domain}'.")
            self._do_check(tool_name, arguments, grants=policy, mission_id=mission_id)
            return

        effective = frozenset(grants or ())
        if not required <= effective:
            raise ToolCapabilityError(
                f"Permissão MCP ausente para '{tool_name}': requer {sorted(required)}, recebido {sorted(effective)}."
            )
        explicit_tools = {g.removeprefix("tool:") for g in effective if g.startswith("tool:")}
        if explicit_tools and tool_name not in explicit_tools and f"{self.bridge_domain}.{tool_name}" not in explicit_tools:
            raise ToolCapabilityError(f"Escopo por ferramenta negou execução de '{tool_name}'.")
        if is_privileged and "capability:privileged" not in effective:
            raise ToolCapabilityError(
                f"Ação privilegiada '{tool_name}' negada: exige CapabilityToken com allow_privileged=True ou grant 'capability:privileged'."
            )


def validate_object(value, schema):
    if not isinstance(value, dict):
        raise ValueError("Objeto esperado")
    properties = schema.get("properties", {})
    allow_additional = schema.get("additionalProperties", False)
    if not allow_additional and (set(value) - set(properties)):
        raise ValueError("Campos inválidos/ausentes")
    if set(schema.get("required", ())) - set(value):
        raise ValueError("Campos inválidos/ausentes")
    for key, item in value.items():
        if key not in properties:
            continue
        rule = properties[key]
        kind = rule.get("type")
        if kind == "number" and (type(item) not in (int, float) or not math.isfinite(item)):
            raise ValueError("Número inválido")
        if kind == "integer" and type(item) is not int:
            raise ValueError("Inteiro inválido")
        if kind == "string":
            if not isinstance(item, str):
                raise ValueError("Texto inválido")
            min_len = rule.get("minLength", 1)
            if len(item) < min_len:
                raise ValueError("Texto inválido")
            max_len = rule.get("maxLength", 256)
            if len(item) > max_len:
                raise ValueError("Texto inválido")
        if kind == "array" and not isinstance(item, list):
            raise ValueError("Lista inválida")
        if kind == "boolean" and type(item) is not bool:
            raise ValueError("Boolean inválido")
        if kind == "object" and not isinstance(item, dict):
            raise ValueError("Objeto inválido")
        if "minimum" in rule and item < rule["minimum"]:
            raise ValueError("Valor abaixo do mínimo")
        if "maximum" in rule and item > rule["maximum"]:
            raise ValueError("Valor acima do máximo")
        if "enum" in rule and item not in rule["enum"]:
            raise ValueError("Valor fora do enum")


class ToolRegistry:
    def __init__(self):
        self._entries: dict[str, tuple[ToolContract, Any]] = {}
        self.authorization = AuthorizationLayer()

    def register(self, contract: ToolContract, function, allow_privileged: bool = False) -> None:
        if contract.name in self._entries:
            raise ValueError("Tool duplicada")
        if contract.timeout <= 0 or not contract.permissions:
            raise ValueError("Contrato incompleto")
        self.authorization.validate_registration(contract, allow_privileged=allow_privileged)
        self._entries[contract.name] = (contract, function)

    def contracts(self):
        return [asdict(c) for c, _ in self._entries.values()]

    def get_tool(self, name: str) -> tuple[ToolContract, Any] | None:
        return self._entries.get(name)

    def list_for_mode(self, mode: str) -> list[ToolContract]:
        return [c for c, _ in self._entries.values() if mode in c.modes]

    def get_openai_tools(self, mode: str | None = None) -> list[dict[str, Any]]:
        tools = []
        for contract, _ in self._entries.values():
            if mode is not None and mode not in contract.modes:
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": contract.name,
                    "description": contract.description,
                    "parameters": contract.input_schema,
                },
            })
        return tools

    def get_anthropic_tools(self, mode: str | None = None) -> list[dict[str, Any]]:
        tools = []
        for contract, _ in self._entries.values():
            if mode is not None and mode not in contract.modes:
                continue
            tools.append({
                "name": contract.name,
                "description": contract.description,
                "input_schema": contract.input_schema,
            })
        return tools

    def issue_mission_capability(
        self,
        mission_id: str,
        *,
        allowed_tools: Iterable[str],
        permissions: Iterable[str] | None = None,
        ttl_seconds: float = 900.0,
    ) -> CapabilityToken:
        tools_list = list(allowed_tools)
        for t_name in tools_list:
            if t_name != "*" and not t_name.endswith(".*") and t_name not in self._entries:
                raise ValueError(f"Tool '{t_name}' não está registrada no ToolRegistry.")
        if permissions is None:
            inferred: set[str] = set()
            for t_name in tools_list:
                if t_name in self._entries:
                    inferred.update(self._entries[t_name][0].permissions)
            perms_iter: Iterable[str] = inferred
        else:
            perms_iter = permissions
        return self.authorization.issue_capability(
            mission_id,
            allowed_tools=tools_list,
            permissions=perms_iter,
            ttl_seconds=ttl_seconds,
            allow_privileged=False,
        )

    def invoke(
        self,
        request: ActionRequest,
        grants: frozenset[str] | set[str] | CapabilityToken = frozenset(),
        *,
        mission_id: str | None = None,
        allow_privileged: bool = False,
    ):
        if request.tool not in self._entries:
            raise ValueError("Tool não registrada")
        c, function = self._entries[request.tool]
        effective_mission = mission_id if mission_id is not None else request.mission_id
        self.authorization.authorize(
            c,
            grants if isinstance(grants, CapabilityToken) else frozenset(grants),
            mission_id=effective_mission,
            arguments=request.arguments,
            allow_privileged=allow_privileged,
        )
        validate_object(request.arguments, c.input_schema)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mark-tool")
        future = executor.submit(function, **request.arguments)
        try:
            result = future.result(timeout=c.timeout)
            validate_object(result, c.output_schema)
            return result
        except TimeoutError:
            raise TimeoutError("Tool excedeu o orçamento de tempo") from None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


NUMBER = {"type": "number", "minimum": 0, "maximum": 1e12}
POSITIVE = {"type": "number", "minimum": 1e-12, "maximum": 1e12}
TEXT = {"type": "string", "maxLength": 256}
LONG_TEXT = {"type": "string", "maxLength": 1_000_000, "minLength": 0}


def default_registry(knowledge_path=None, allow_mutating=True):
    registry = ToolRegistry()
    outputs = {
        "density": {"value": NUMBER, "unit": TEXT},
        "molar_mass": {"formula": TEXT, "value": NUMBER, "unit": TEXT, "atoms": {"type": "object"}, "reference": TEXT, "precision": TEXT},
        "stoichiometry": {"value": NUMBER, "unit": TEXT, "assumption": TEXT},
        "unit_conversion": {"value": {"type": "number"}, "unit": TEXT},
        "ideal_gas": {"value": NUMBER, "unit": TEXT, "assumption": TEXT},
        "ph_calculation": {"value": {"type": "number"}, "unit": TEXT, "assumption": TEXT},
        "periodic_table_lookup": {"symbol": TEXT, "value": NUMBER, "unit": TEXT, "reference": TEXT},
    }
    inputs = {
        "density": {"mass": NUMBER, "mass_unit": TEXT, "volume": POSITIVE, "volume_unit": TEXT},
        "molar_mass": {"formula": TEXT},
        "stoichiometry": {"known_moles": NUMBER, "known_coefficient": {"type": "integer", "minimum": 1, "maximum": 1000}, "target_coefficient": {"type": "integer", "minimum": 1, "maximum": 1000}},
        "unit_conversion": {"value": {"type": "number", "minimum": -1e12, "maximum": 1e12}, "from_unit": TEXT, "to_unit": TEXT},
        "ideal_gas": {"moles": NUMBER, "temperature_kelvin": POSITIVE, "pressure_pa": POSITIVE},
        "ph_calculation": {"concentration": {"type": "number", "minimum": 1e-6, "maximum": 1}},
        "periodic_table_lookup": {"symbol": TEXT},
    }
    for name, properties in inputs.items():
        registry.register(
            ToolContract(
                "chemistry." + name,
                "Cálculo determinístico educacional: " + name,
                schema(properties),
                schema(outputs[name]),
                "READ_ONLY",
                1.0,
                ("tools.calculate",),
                modes=("general", "professor", "engineer"),
            ),
            getattr(chemistry, name),
        )
    from .chemical_facts import molecular_formula

    registry.register(
        ToolContract(
            "chemistry.molecular_formula",
            "Identidade molecular curada com fonte, sem fabricação",
            schema({"compound": TEXT}),
            schema({"name": TEXT, "formula": TEXT, "reference": TEXT, "verified_on": TEXT}),
            "READ_ONLY",
            1.0,
            ("knowledge.read",),
            modes=("professor", "engineer"),
        ),
        molecular_formula,
    )
    from pathlib import Path
    from .knowledge import KnowledgeStore, DEFAULT_DB

    path = Path(knowledge_path or DEFAULT_DB)

    def knowledge_search(query):
        if not path.exists():
            return {"hits": []}
        with KnowledgeStore(path, read_only=True) as store:
            return {"hits": [h.to_dict() for h in store.search(query)]}

    def knowledge_stats():
        if not path.exists():
            return {"sources": 0, "active_sources": 0, "chunks": 0, "available": False}
        with KnowledgeStore(path, read_only=True) as store:
            data = store.stats()
            return {key: data[key] for key in ("sources", "active_sources", "chunks")} | {"available": True}

    for name, function, inputs, outputs in (
        ("knowledge.search", knowledge_search, {"query": TEXT}, {"hits": {"type": "array"}}),
        ("knowledge.stats", knowledge_stats, {}, {"sources": NUMBER, "active_sources": NUMBER, "chunks": NUMBER, "available": {"type": "boolean"}}),
    ):
        registry.register(
            ToolContract(name, "Leitura local de conhecimento", schema(inputs), schema(outputs), "READ_ONLY", 1.0, ("knowledge.read",), modes=("professor", "engineer")),
            function,
        )

    # 1. Calculadora Universal
    def calculator_eval(expression: str):
        from ..brain_direct import direct_math
        val = direct_math(expression)
        if val is None:
            raise ValueError(f"Expressão matemática não avaliada: {expression}")
        return {"expression": expression, "result": str(val)}

    registry.register(
        ToolContract(
            "calculator.eval",
            "Cálculo matemático determinístico de expressões aritméticas",
            schema({"expression": TEXT}),
            schema({"expression": TEXT, "result": TEXT}),
            "READ_ONLY",
            2.0,
            ("tools.calculate",),
            modes=("general", "professor", "engineer"),
        ),
        calculator_eval,
    )

    # 2. Pesquisa Web Segura
    def web_search_action(query: str):
        from ..web import search
        text = search(query, timeout=10)
        return {"query": query, "results": str(text)[:32000], "status": "ok"}

    registry.register(
        ToolContract(
            "web.search",
            "Pesquisa segura na internet com sumarização de fontes públicas",
            schema({"query": TEXT}),
            schema({"query": TEXT, "results": LONG_TEXT, "status": TEXT}),
            "READ_ONLY",
            15.0,
            ("web.search",),
            modes=("professor", "engineer"),
        ),
        web_search_action,
    )

    # 3. Integração GitHub
    def gh_read_file(repo: str, path: str):
        from ..github import read_file
        sha, content = read_file(repo, "main", path)
        return {"repo": repo, "path": path, "content": str(content)[:32000], "sha": str(sha)}

    registry.register(
        ToolContract(
            "github.read_file",
            "Lê o conteúdo de um arquivo de texto de um repositório no GitHub",
            schema({"repo": TEXT, "path": TEXT}),
            schema({"repo": TEXT, "path": TEXT, "content": LONG_TEXT, "sha": TEXT}),
            "READ_ONLY",
            30.0,
            ("github.read",),
            modes=("engineer",),
        ),
        gh_read_file,
    )

    def gh_list_files(repo: str):
        from ..github import list_root
        files = list_root(repo, "main")
        return {"repo": repo, "files": files}

    registry.register(
        ToolContract(
            "github.list_files",
            "Lista os arquivos da raiz de um repositório no GitHub",
            schema({"repo": TEXT}),
            schema({"repo": TEXT, "files": {"type": "array"}}),
            "READ_ONLY",
            30.0,
            ("github.read",),
            modes=("engineer",),
        ),
        gh_list_files,
    )

    def gh_get_metadata(repo: str):
        from ..github_assistant import metadata
        data = metadata(repo)
        return {
            "name": str(data["name"]),
            "default_branch": str(data["default_branch"]),
            "push_allowed": bool(data["push_allowed"]),
            "url": str(data["url"]),
        }

    registry.register(
        ToolContract(
            "github.get_metadata",
            "Consulta metadados e permissões de um repositório no GitHub",
            schema({"repo": TEXT}),
            schema({"name": TEXT, "default_branch": TEXT, "push_allowed": {"type": "boolean"}, "url": TEXT}),
            "READ_ONLY",
            15.0,
            ("github.read",),
            modes=("engineer",),
        ),
        gh_get_metadata,
    )

    def gh_update_file(repo: str, path: str, message: str, content: str, sha: str):
        from ..github import update_file
        res = update_file(repo, "main", path, sha, message, content)
        return {"repo": repo, "path": path, "result": str(res), "status": "ok"}

    if allow_mutating:
        registry.register(
            ToolContract(
                "github.update_file",
                "Cria ou atualiza um arquivo no repositório GitHub através de um commit",
                schema({"repo": TEXT, "path": TEXT, "message": TEXT, "content": LONG_TEXT, "sha": TEXT}),
                schema({"repo": TEXT, "path": TEXT, "result": TEXT, "status": TEXT}),
                "MUTATING",
                30.0,
                ("github.write",),
                side_effects=("github.commit",),
                modes=("engineer",),
            ),
            gh_update_file,
            allow_privileged=True,
        )

    # 4. Relógio e Calendário do Sistema
    def sys_clock(query: str):
        from ..brain_direct import direct_clock
        res = direct_clock(query) or "Consulta de relógio realizada."
        return {"query": query, "result": str(res)}

    registry.register(
        ToolContract(
            "system.clock",
            "Consulta a data e horário atual do sistema operacional",
            schema({"query": TEXT}),
            schema({"query": TEXT, "result": TEXT}),
            "READ_ONLY",
            1.0,
            ("system.read",),
            modes=("general", "professor", "engineer"),
        ),
        sys_clock,
    )

    # 5. Memória Simples
    def mem_recall(query: str):
        return {"query": query, "facts": []}

    registry.register(
        ToolContract(
            "memory.recall",
            "Recupera memórias salvas do usuário na sessão atual",
            schema({"query": TEXT}),
            schema({"query": TEXT, "facts": {"type": "array"}}),
            "READ_ONLY",
            2.0,
            ("memory.read",),
            modes=("general", "professor", "engineer"),
        ),
        mem_recall,
    )

    from ..home_assistant import HomeAssistantMCPBridge
    from ..backup_manager import BackupMCPBridge
    from ..reverse_engine import ReverseEngineMCPBridge
    from ..smart_home_gateway import SmartHomeMCPBridge

    HomeAssistantMCPBridge().register_in_cognition_registry(registry)
    BackupMCPBridge().register_in_cognition_registry(registry)
    ReverseEngineMCPBridge().register_in_cognition_registry(registry)
    SmartHomeMCPBridge().register_in_cognition_registry(registry)

    try:
        from ..mcp_gods_eye import GodsEyeMCPBridge
        GodsEyeMCPBridge().register_in_cognition_registry(registry)
    except Exception:
        pass

    try:
        from ..mcp_sandbox import SandboxMCPBridge
        SandboxMCPBridge().register_in_cognition_registry(registry)
    except Exception:
        pass

    return registry

