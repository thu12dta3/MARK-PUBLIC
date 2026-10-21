from __future__ import annotations

import json
import logging
import re
from typing import Any

from .cognition.tools import ActionRequest, BridgeCapabilityGuard, CapabilityToken, ToolContract, ToolRegistry
from .gods_eye_intent import _safe_visualizer_url, handle_godseye_intent, parse_godseye_intent
from .gods_eye_view import ALLOWED_TOOLS, GodsEyeManager, GodseyeError, _schema_check, _validate_api_base

log = logging.getLogger("mark.mcp_gods_eye")

MAX_OSINT_PAYLOAD_BYTES = 64 * 1024

GODS_EYE_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_earthquakes": {
        "description": "Consulta eventos sísmicos e terremotos recentes por magnitude mínima e janela temporal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_magnitude": {"type": "number", "minimum": 0, "maximum": 10},
                "hours": {"type": "integer", "minimum": 1, "maximum": 168},
                "region": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            "additionalProperties": False,
        },
    },
    "get_active_fires": {
        "description": "Localiza focos de incêndio ativos via sensores orbitais em uma região ou coordenada.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                "radius_km": {"type": "number", "minimum": 1, "maximum": 5000},
                "region": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            "additionalProperties": False,
        },
    },
    "get_recent_launches": {
        "description": "Lista lançamentos espaciais recentes e futuros próximos.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    "aircraft_in_area": {
        "description": "Varre aeronaves e voos ativos em uma área geográfica delimitada por coordenadas e raio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                "radius_km": {"type": "number", "minimum": 1, "maximum": 2000},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
    },
    "find_aircraft": {
        "description": "Busca uma aeronave específica por callsign, código ICAO24 ou prefixo de registro.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 64},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "get_aircraft_track": {
        "description": "Recupera a trajetória histórica recente de uma aeronave por código ICAO24 ou callsign.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "icao24": {"type": "string", "minLength": 2, "maxLength": 32},
            },
            "required": ["icao24"],
            "additionalProperties": False,
        },
    },
    "get_aircraft_info": {
        "description": "Obtém metadados técnicos e operacionais de uma aeronave.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "icao24": {"type": "string", "minLength": 2, "maxLength": 32},
            },
            "required": ["icao24"],
            "additionalProperties": False,
        },
    },
    "vessels_in_area": {
        "description": "Varre embarcações marítimas AIS ativas em uma área geográfica.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                "radius_km": {"type": "number", "minimum": 1, "maximum": 2000},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
    },
    "find_vessel": {
        "description": "Localiza uma embarcação por MMSI, IMO ou nome.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 80},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "get_vessel_track": {
        "description": "Recupera o rastro marítimo recente de uma embarcação por MMSI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mmsi": {"type": "string", "minLength": 5, "maxLength": 20},
            },
            "required": ["mmsi"],
            "additionalProperties": False,
        },
    },
    "next_satellite_pass": {
        "description": "Calcula a próxima passagem orbital de um satélite sobre uma coordenada.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "satellite": {"type": "string", "minLength": 1, "maxLength": 80},
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
            },
            "required": ["satellite", "lat", "lon"],
            "additionalProperties": False,
        },
    },
    "satellites_overhead": {
        "description": "Lista satélites em órbita atualmente acima de uma coordenada geográfica.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                "radius_deg": {"type": "number", "minimum": 1, "maximum": 90},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
    },
    "get_weather_map": {
        "description": "Obtém camadas meteorológicas e condições atmosféricas para uma região ou coordenada.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                "layer": {"type": "string", "enum": ["clouds", "precipitation", "wind", "temperature"]},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
    },
    "get_recent_imagery": {
        "description": "Consulta metadados de imagens de satélite recentes para uma coordenada.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
    },
    "find_submarine_cables": {
        "description": "Mapeia cabos submarinos de telecomunicações próximos a uma região ou ponto.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 120},
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
            },
            "additionalProperties": False,
        },
    },
    "find_infrastructure": {
        "description": "Pesquisa infraestrutura crítica e instalações estratégicas (OSINT espacial).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 120},
                "category": {"type": "string", "minLength": 1, "maxLength": 64},
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
            },
            "additionalProperties": False,
        },
    },
    "get_cyclones": {
        "description": "Lista ciclones tropicais, tufões e tempestades severas ativas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "basin": {"type": "string", "minLength": 1, "maxLength": 64},
            },
            "additionalProperties": False,
        },
    },
    "get_fire_perimeters": {
        "description": "Obtém perímetros georreferenciados de incêndios florestais.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            "additionalProperties": False,
        },
    },
    "get_terrain_height": {
        "description": "Consulta a altitude topográfica do terreno em metros para uma coordenada.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
    },
    "get_map_features": {
        "description": "Extrai feições geográficas e pontos de interesse em uma região.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "place": {"type": "string", "minLength": 1, "maxLength": 160},
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
            },
            "additionalProperties": False,
        },
    },
    "show_in_gods_eye_view": {
        "description": "Abre ou atualiza a visualização 3D local do God's Eye View em uma região ou coordenada.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "area": {
                    "type": "object",
                    "properties": {"place": {"type": "string", "minLength": 1, "maxLength": 160}},
                    "required": ["place"],
                    "additionalProperties": False,
                },
                "camera": {
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "minimum": -90, "maximum": 90},
                        "lon": {"type": "number", "minimum": -180, "maximum": 180},
                        "altitude_m": {"type": "number", "minimum": 100, "maximum": 50_000_000},
                        "pitch_deg": {"type": "number", "minimum": -90, "maximum": 0},
                    },
                    "required": ["lat", "lon"],
                    "additionalProperties": False,
                },
                "layers": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "enum": ["flights", "earthquakes", "satellites", "vessels", "fires", "cables"]},
                },
                "style": {"type": "string", "enum": ["normal", "surveillance", "thermal"]},
            },
            "additionalProperties": False,
        },
    },
}

_SECRET_SCRUB = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9._~+/=-]+|token\s*[:=]\s*\S+|api[_-]?key\s*[:=]\s*\S+)\b"
)


def sanitize_mcp_output(result: dict[str, Any]) -> dict[str, Any]:
    """Valida e higieniza o payload retornado pelo God's Eye View antes de entregá-lo ao agente/UI."""
    if not isinstance(result, dict):
        raise GodseyeError("Resposta MCP inválida: objeto JSON esperado.")
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_OSINT_PAYLOAD_BYTES:
        raise GodseyeError("Resposta MCP excedeu o limite seguro de payload OSINT.")
    scrubbed = _SECRET_SCRUB.sub("[REDACTED]", encoded)
    clean = json.loads(scrubbed)
    structured = clean.get("structuredContent")
    if isinstance(structured, dict) and "url" in structured:
        if not _safe_visualizer_url(structured["url"]):
            raise GodseyeError("URL de visualização não confiável bloqueada pelo filtro de segurança.")
    return clean


class GodsEyeMCPBridge:
    """Ponte MCP padronizada que expõe as ferramentas do God's Eye View para o aplicativo principal e roteador."""

    def __init__(
        self,
        manager: GodsEyeManager | None = None,
        api_base: str = "http://127.0.0.1:4173",
        *,
        require_explicit_capabilities: bool = False,
    ):
        self.api_base = _validate_api_base(api_base)
        self.manager = manager or GodsEyeManager()
        tool_perms = {
            name: ("gods_eye.visualize", "gods_eye.read") if name == "show_in_gods_eye_view" else ("gods_eye.read",)
            for name in ALLOWED_TOOLS
        }
        self.capability_guard = BridgeCapabilityGuard(
            "gods_eye",
            tool_perms,
            privileged_tools=frozenset({"show_in_gods_eye_view"}),
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
        """Retorna a lista de definições de ferramentas no padrão MCP (name, description, inputSchema)."""
        tools = []
        for name in sorted(ALLOWED_TOOLS):
            spec = GODS_EYE_TOOL_SCHEMAS.get(name)
            if spec:
                tools.append({
                    "name": name,
                    "description": spec["description"],
                    "inputSchema": spec["inputSchema"],
                })
        return tools

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Retorna os schemas no formato OpenAI/Ollama tool calling."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["inputSchema"],
                },
            }
            for tool in self.get_mcp_tools()
        ]

    def validate_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        """Valida o nome da ferramenta contra a allowlist, capacidades da missão e JSON Schema."""
        if name not in ALLOWED_TOOLS or name not in GODS_EYE_TOOL_SCHEMAS:
            raise PermissionError(f"Ferramenta MCP não autorizada: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Os argumentos da ferramenta MCP devem ser um objeto JSON.")
        self.capability_guard.check(name, arguments, grants=grants, mission_id=mission_id)
        schema = GODS_EYE_TOOL_SCHEMAS[name]["inputSchema"]
        _schema_check(schema, arguments)
        return arguments

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: int = 30,
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        """Valida parâmetros, garante o servidor local ativo em loopback e executa a tool MCP com sanitização."""
        validated_args = self.validate_call(name, arguments, grants=grants, mission_id=mission_id)
        if not self.manager.running:
            self.manager.start()
        raw_result = self.manager.call_tool(name, validated_args, timeout=timeout)
        return sanitize_mcp_output(raw_result)

    def register_in_cognition_registry(self, registry: ToolRegistry | None = None) -> ToolRegistry:
        """Registra as ferramentas de leitura OSINT/espacial do God's Eye no ToolRegistry de cognição do MARK."""
        target = registry if registry is not None else ToolRegistry()
        output_schema = {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "maxLength": 128},
                "status": {"type": "string", "maxLength": 64},
                "data": {"type": "object"},
            },
            "required": ["tool", "status", "data"],
            "additionalProperties": False,
        }
        for tool in self.get_mcp_tools():
            name = tool["name"]
            if name == "show_in_gods_eye_view":
                continue

            def _make_runner(tool_name: str):
                def _runner(**kwargs):
                    res = self.execute_tool(tool_name, kwargs)
                    return {"tool": tool_name, "status": "ok", "data": res}
                return _runner

            contract = ToolContract(
                name=f"gods_eye.{name}",
                description=tool["description"],
                input_schema=tool["inputSchema"],
                output_schema=output_schema,
                risk_level="READ_ONLY",
                timeout=15.0,
                permissions=("gods_eye.read",),
            )
            target.register(contract, _make_runner(name))
        return target

    def dispatch_from_prompt(self, prompt: str, *, open_browser: bool = False) -> dict[str, Any] | None:
        """Analisa intenções espaciais/OSINT no prompt e aciona a ferramenta MCP correspondente."""
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        intent = parse_godseye_intent(prompt)
        if intent is not None:
            reply = handle_godseye_intent(prompt, self.manager, open_browser=open_browser)
            return {"matched": True, "mode": "visualizer", "tool": "show_in_gods_eye_view", "reply": reply}

        lower = prompt.casefold()
        if re.search(r"\b(terremotos?|abalos?\s+s[ií]smicos?|sismos?)\b", lower):
            res = self.execute_tool("get_earthquakes", {"min_magnitude": 4.5, "hours": 24})
            return {"matched": True, "mode": "osint", "tool": "get_earthquakes", "result": res}
        if re.search(r"\b(ciclones?|furac[oõ]es?|tuf[oõ]es?)\b", lower):
            res = self.execute_tool("get_cyclones", {})
            return {"matched": True, "mode": "osint", "tool": "get_cyclones", "result": res}
        if re.search(r"\b(lan[cç]amentos?\s+espaciais|foguetes?\s+lan[cç]ados)\b", lower):
            res = self.execute_tool("get_recent_launches", {"limit": 5})
            return {"matched": True, "mode": "osint", "tool": "get_recent_launches", "result": res}
        if re.search(r"\b(cabos?\s+submarinos?)\b", lower):
            res = self.execute_tool("find_submarine_cables", {"query": "global"})
            return {"matched": True, "mode": "osint", "tool": "find_submarine_cables", "result": res}
        return None
