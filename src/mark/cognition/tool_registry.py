"""
Módulo de Integração MCP com o ToolRegistry e Cognição do MARK.

Acopla a ponte MCP nativa em Rust (mark_core.McpBridge) ao ToolRegistry do MARK,
permitindo que agentes LLM (Ollama, Qwen, Claude, OpenAI) descubram ferramentas
nativas via esquemas JSON padronizados (OpenAI function calling e Anthropic tool_use),
interceptando execuções de ferramentas e garantindo isolamento Zero-Trust de memória e caminhos.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import mark_core
    from mark_core import McpBridge
except ImportError:
    mark_core = None  # type: ignore[assignment]
    McpBridge = None  # type: ignore[assignment]

from .tools import (
    ActionRequest,
    AuthorizationLayer,
    CapabilityToken,
    ToolCapabilityError,
    ToolContract,
    ToolRegistry,
    default_registry,
    schema,
)

logger = logging.getLogger("mark.cognition.tool_registry")

_GLOBAL_MCP_BRIDGE: Any | None = None
_GLOBAL_WORKSPACE: str | None = None

DEFAULT_MCP_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_source": {
        "description": "Lê o conteúdo de um arquivo de código-fonte dentro do workspace com validação de path traversal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 1024, "minLength": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "maxLength": 10_000_000, "minLength": 0},
                "status": {"type": "string", "maxLength": 256, "minLength": 1},
            },
            "required": [],
            "additionalProperties": True,
        },
        "capability": "ReadOnly",
        "risk_level": "READ_ONLY",
        "permissions": ("mcp.read",),
    },
    "write_patch": {
        "description": "Aplica alterações ou grava um arquivo de código-fonte no workspace auditado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 1024, "minLength": 1},
                "content": {"type": "string", "maxLength": 10_000_000, "minLength": 0},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "maxLength": 10_000_000, "minLength": 0},
                "status": {"type": "string", "maxLength": 256, "minLength": 1},
            },
            "required": [],
            "additionalProperties": True,
        },
        "capability": "WriteMutate",
        "risk_level": "MUTATING",
        "permissions": ("mcp.write",),
    },
    "inspect_ast": {
        "description": "Analisa a árvore sintática (AST) de um arquivo de código com isolamento seguro.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 1024, "minLength": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "maxLength": 10_000_000, "minLength": 0},
                "status": {"type": "string", "maxLength": 256, "minLength": 1},
            },
            "required": [],
            "additionalProperties": True,
        },
        "capability": "ReadOnly",
        "risk_level": "READ_ONLY",
        "permissions": ("mcp.read",),
    },
}


def get_global_mcp_bridge(workspace_dir: str | Path | None = None) -> Any | None:
    """Recupera a instância global da McpBridge se inicializada."""
    global _GLOBAL_MCP_BRIDGE
    if _GLOBAL_MCP_BRIDGE is None and workspace_dir is not None:
        return init_global_mcp_bridge(workspace_dir)
    return _GLOBAL_MCP_BRIDGE


def set_global_mcp_bridge(bridge: Any | None, workspace_dir: str | None = None) -> None:
    """Define explicitamente a instância global da McpBridge."""
    global _GLOBAL_MCP_BRIDGE, _GLOBAL_WORKSPACE
    _GLOBAL_MCP_BRIDGE = bridge
    _GLOBAL_WORKSPACE = workspace_dir


def init_global_mcp_bridge(workspace_dir: str | Path | None = None) -> Any:
    """Inicializa e registra a instância global da McpBridge nativa em Rust."""
    global _GLOBAL_MCP_BRIDGE, _GLOBAL_WORKSPACE
    if McpBridge is None:
        raise RuntimeError("Extensão nativa 'mark_core' não disponível para inicializar McpBridge.")
    target_dir = str(workspace_dir or _GLOBAL_WORKSPACE or os.getcwd())
    _GLOBAL_MCP_BRIDGE = McpBridge(target_dir)
    _GLOBAL_WORKSPACE = target_dir
    logger.info("McpBridge nativa inicializada no workspace: %s", target_dir)
    return _GLOBAL_MCP_BRIDGE


class McpCognitionBridge:
    """Adaptador de alto nível para integrar a McpBridge nativa ao ToolRegistry e LLMs."""

    def __init__(
        self,
        bridge: Any | None = None,
        workspace_dir: str | Path | None = None,
    ) -> None:
        self.workspace_dir = str(workspace_dir or os.getcwd())
        if bridge is not None:
            self.bridge = bridge
        else:
            self.bridge = get_global_mcp_bridge(self.workspace_dir)
            if self.bridge is None and McpBridge is not None:
                self.bridge = init_global_mcp_bridge(self.workspace_dir)
        self._tool_specs: dict[str, dict[str, Any]] = dict(DEFAULT_MCP_SCHEMAS)

    def register_tool(
        self,
        name: str,
        capability: str = "ReadOnly",
        allowed_paths: list[str] | None = None,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        modes: tuple[str, ...] = ("engineer", "general", "professor"),
    ) -> None:
        """Registra ferramenta na McpBridge nativa e no catálogo de schemas cognitivos."""
        if self.bridge is not None:
            paths = allowed_paths or [self.workspace_dir]
            self.bridge.register_tool(name, capability, paths)

        clean_cap = capability.lower()
        is_mutate = clean_cap in ("writemutate", "write_mutate", "write")
        in_schema = input_schema or {
            "type": "object",
            "properties": {
                "path": {"type": "string", "maxLength": 1024, "minLength": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        }
        out_schema = output_schema or {
            "type": "object",
            "properties": {
                "content": {"type": "string", "maxLength": 10_000_000, "minLength": 0},
                "status": {"type": "string", "maxLength": 256, "minLength": 1},
            },
            "required": [],
            "additionalProperties": True,
        }

        self._tool_specs[name] = {
            "description": description or f"Ferramenta MCP nativa '{name}'.",
            "input_schema": in_schema,
            "output_schema": out_schema,
            "capability": capability,
            "risk_level": "MUTATING" if is_mutate else "READ_ONLY",
            "permissions": ("mcp.write",) if is_mutate else ("mcp.read",),
            "modes": modes,
        }

    def list_tools(self) -> list[str]:
        """Retorna as ferramentas autorizadas pela McpBridge nativa."""
        if self.bridge is not None:
            return self.bridge.list_tools()
        return list(self._tool_specs.keys())

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | str,
        is_mutation: bool = False,
    ) -> str:
        """Executa a ferramenta na McpBridge nativa com isolamento e normalização."""
        if self.bridge is None:
            raise RuntimeError("McpBridge não está inicializada.")
        args_json = arguments if isinstance(arguments, str) else json.dumps(arguments)
        return self.bridge.call_tool(name, args_json, is_mutation)

    def get_openai_tools(self, mode: str | None = None) -> list[dict[str, Any]]:
        """Retorna as ferramentas MCP no formato OpenAI / Ollama function calling."""
        tools: list[dict[str, Any]] = []
        for name, spec in self._tool_specs.items():
            if mode is not None and mode not in spec.get("modes", ()):
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": f"mcp.{name}",
                    "description": spec["description"],
                    "parameters": spec["input_schema"],
                },
            })
        return tools

    def get_anthropic_tools(self, mode: str | None = None) -> list[dict[str, Any]]:
        """Retorna as ferramentas MCP no formato Anthropic tool_use."""
        tools: list[dict[str, Any]] = []
        for name, spec in self._tool_specs.items():
            if mode is not None and mode not in spec.get("modes", ()):
                continue
            tools.append({
                "name": f"mcp.{name}",
                "description": spec["description"],
                "input_schema": spec["input_schema"],
            })
        return tools

    def register_in_registry(
        self,
        registry: ToolRegistry,
        prefix: str = "mcp.",
    ) -> ToolRegistry:
        """Registra as ferramentas da McpBridge no ToolRegistry do MARK."""
        available_tools = self.list_tools()

        for tool_name in available_tools:
            spec = self._tool_specs.get(tool_name) or DEFAULT_MCP_SCHEMAS.get(
                tool_name,
                {
                    "description": f"Ferramenta MCP nativa '{tool_name}'.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "maxLength": 1024, "minLength": 1},
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "maxLength": 10_000_000, "minLength": 0},
                            "status": {"type": "string", "maxLength": 256, "minLength": 1},
                        },
                        "required": [],
                        "additionalProperties": True,
                    },
                    "capability": "ReadOnly",
                    "risk_level": "READ_ONLY",
                    "permissions": ("mcp.read",),
                    "modes": ("engineer", "general", "professor"),
                },
            )

            is_mutation = spec.get("risk_level") == "MUTATING"

            def _make_runner(t_name: str, is_mutate: bool):
                def _runner(**kwargs):
                    res_str = self.call_tool(t_name, kwargs, is_mutate)
                    try:
                        parsed = json.loads(res_str)
                        if isinstance(parsed, dict):
                            content_val = parsed.get("content")
                            if content_val is None:
                                content_val = json.dumps(parsed, ensure_ascii=False)
                            elif not isinstance(content_val, str):
                                content_val = json.dumps(content_val, ensure_ascii=False)
                            status_val = str(parsed.get("status", "ok"))
                            return {"content": content_val, "status": status_val}
                        return {"content": str(parsed), "status": "ok"}
                    except Exception:
                        return {"content": res_str, "status": "ok"}

                return _runner

            contract = ToolContract(
                name=f"{prefix}{tool_name}",
                description=spec["description"],
                input_schema=spec["input_schema"],
                output_schema=spec["output_schema"],
                risk_level=spec.get("risk_level", "READ_ONLY"),
                timeout=30.0,
                permissions=tuple(spec.get("permissions", ("mcp.read",))),
                modes=tuple(spec.get("modes", ("engineer", "general", "professor"))),
            )

            registry.register(
                contract,
                _make_runner(tool_name, is_mutation),
                allow_privileged=is_mutation,
            )

        return registry


__all__ = [
    "ActionRequest",
    "AuthorizationLayer",
    "CapabilityToken",
    "DEFAULT_MCP_SCHEMAS",
    "McpBridge",
    "McpCognitionBridge",
    "ToolCapabilityError",
    "ToolContract",
    "ToolRegistry",
    "default_registry",
    "get_global_mcp_bridge",
    "init_global_mcp_bridge",
    "set_global_mcp_bridge",
]
