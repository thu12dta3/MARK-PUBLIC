"""Ponte MCP para o ExecutionSandbox e EngineerAgent (Fase 6 - The Execution Core).

Expõe as capacidades do sandbox seguro e do agente autônomo de engenharia
através de ferramentas MCP padronizadas para acionamento programático.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .cognition.tools import (
    BridgeCapabilityGuard,
    CapabilityToken,
    ToolCapabilityError,
    ToolContract,
    ToolRegistry,
)
from .engineer_agent import EngineerAgent
from .gods_eye_view import _schema_check
from .reverse_engine import REVERSE_MCP_TOOL_SCHEMAS, ReverseEngine, UNTRUSTED_PROVENANCE_META
from .sandbox import ExecutionSandbox, SandboxSecurityError

log = logging.getLogger("mark.mcp_sandbox")

ALLOWED_SANDBOX_TOOLS = frozenset({
    "sandbox_run_command",
    "sandbox_run_python",
    "sandbox_run_pytest",
    "sandbox_run_rust_build",
    "engineer_solve_task",
    "rea_inspect_artifact",
    "rea_decompile_function",
    "rea_compare_artifacts",
})

SANDBOX_TOOL_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "sandbox_run_command": ("sandbox.execute",),
    "sandbox_run_python": ("sandbox.execute",),
    "sandbox_run_pytest": ("sandbox.verify",),
    "sandbox_run_rust_build": ("sandbox.verify",),
    "engineer_solve_task": ("engineer.modify", "sandbox.verify"),
    "rea_inspect_artifact": ("reverse.inspect",),
    "rea_decompile_function": ("reverse.inspect",),
    "rea_compare_artifacts": ("reverse.inspect",),
}

PRIVILEGED_SANDBOX_TOOLS = frozenset({
    "sandbox_run_command",
    "sandbox_run_python",
    "engineer_solve_task",
})

SANDBOX_MCP_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "sandbox_run_command": {
        "description": "Executa um comando permitido da allowlist em ambiente isolado com timeout estrito de 30s.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "minLength": 1, "maxLength": 1024},
                "timeout": {"type": "number", "minimum": 1, "maximum": 30},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    "sandbox_run_python": {
        "description": "Executa um script ou trecho Python em ambiente isolado sem acesso a variáveis sensíveis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code_or_path": {"type": "string", "minLength": 1, "maxLength": 65536},
                "timeout": {"type": "number", "minimum": 1, "maximum": 30},
            },
            "required": ["code_or_path"],
            "additionalProperties": False,
        },
    },
    "sandbox_run_pytest": {
        "description": "Executa uma suíte ou arquivo de testes pytest dentro do ExecutionSandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "minLength": 1, "maxLength": 512},
                "timeout": {"type": "number", "minimum": 1, "maximum": 30},
            },
            "additionalProperties": False,
        },
    },
    "sandbox_run_rust_build": {
        "description": "Executa verificação ou compilação Rust (cargo_check, cargo_build, maturin_develop) no sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["cargo_check", "cargo_build", "maturin_develop"]},
                "manifest_path": {"type": "string", "minLength": 1, "maxLength": 512},
                "release": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "engineer_solve_task": {
        "description": "Aciona o EngineerAgent em loop fechado (Observe -> Modify -> Validate -> Fix) com limite de 3 tentativas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "minLength": 3, "maxLength": 4096},
                "target_file": {"type": "string", "minLength": 1, "maxLength": 512},
                "test_target": {"type": "string", "minLength": 1, "maxLength": 512},
                "initial_error": {"type": "string", "minLength": 0, "maxLength": 8192},
            },
            "required": ["task", "target_file"],
            "additionalProperties": False,
        },
    },
    **REVERSE_MCP_TOOL_SCHEMAS,
}


class SandboxMCPBridge:
    """Ponte MCP que expõe o ExecutionSandbox e o EngineerAgent programaticamente."""

    def __init__(
        self,
        sandbox: ExecutionSandbox | None = None,
        agent: EngineerAgent | None = None,
        *,
        workspace: str | Path | None = None,
        require_explicit_capabilities: bool = False,
    ):
        self.sandbox = sandbox or ExecutionSandbox(workspace=workspace)
        self.agent = agent or EngineerAgent(sandbox=self.sandbox)
        self.reverse_engine = ReverseEngine(sandbox=self.sandbox)
        self.capability_guard = BridgeCapabilityGuard(
            "sandbox",
            SANDBOX_TOOL_PERMISSIONS,
            privileged_tools=PRIVILEGED_SANDBOX_TOOLS,
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
        for name in sorted(ALLOWED_SANDBOX_TOOLS):
            spec = SANDBOX_MCP_TOOL_SCHEMAS.get(name)
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
        if name not in ALLOWED_SANDBOX_TOOLS or name not in SANDBOX_MCP_TOOL_SCHEMAS:
            raise PermissionError(f"Ferramenta MCP de Sandbox não autorizada: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Os argumentos da ferramenta MCP devem ser um objeto JSON.")
        self.capability_guard.check(name, arguments, grants=grants, mission_id=mission_id)
        schema = SANDBOX_MCP_TOOL_SCHEMAS[name]["inputSchema"]
        _schema_check(schema, arguments)
        return arguments

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        grants: frozenset[str] | set[str] | CapabilityToken | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        """Executa uma ferramenta MCP do sandbox ou do EngineerAgent e retorna o resultado estruturado."""
        args = self.validate_call(name, arguments, grants=grants, mission_id=mission_id)

        if name == "sandbox_run_command":
            res = self.sandbox.run(args["command"], timeout=args.get("timeout"))
            return {"tool": name, "status": "ok" if res.success else "error", "result": res.to_dict()}

        if name == "sandbox_run_python":
            res = self.sandbox.run_python_script(args["code_or_path"], timeout=args.get("timeout"))
            return {"tool": name, "status": "ok" if res.success else "error", "result": res.to_dict()}

        if name == "sandbox_run_pytest":
            target = args.get("target", "tests")
            res = self.sandbox.run_pytest(target=target, timeout=args.get("timeout"))
            return {"tool": name, "status": "ok" if res.success else "error", "result": res.to_dict()}

        if name == "sandbox_run_rust_build":
            mode = args.get("mode", "cargo_check")
            manifest = args.get("manifest_path", "src-rust/mark_core/Cargo.toml")
            release = bool(args.get("release", False))
            res = self.sandbox.run_rust_build(mode=mode, manifest_path=manifest, release=release)
            return {"tool": name, "status": "ok" if res.success else "error", "result": res.to_dict()}

        if name == "engineer_solve_task":
            report = self.agent.solve(
                task=args["task"],
                target_file=args["target_file"],
                test_target=args.get("test_target"),
                initial_error=args.get("initial_error", ""),
            )
            return {
                "tool": name,
                "status": "ok" if report.success else "failed",
                "result": report.to_dict(),
            }

        if name == "rea_inspect_artifact":
            res = self.reverse_engine.inspect_artifact(
                args["path"],
                min_string_len=int(args.get("min_string_len", 4)),
                max_strings=int(args.get("max_strings", 50)),
            )
            return {"tool": name, "status": "ok", "result": res, "_meta": UNTRUSTED_PROVENANCE_META}

        if name == "rea_decompile_function":
            res = self.reverse_engine.decompile_function(
                args["path"],
                symbol=str(args.get("symbol", "")),
                max_lines=int(args.get("max_lines", 80)),
            )
            return {"tool": name, "status": "ok", "result": res, "_meta": UNTRUSTED_PROVENANCE_META}

        if name == "rea_compare_artifacts":
            res = self.reverse_engine.compare_artifacts(args["path_a"], args["path_b"])
            return {"tool": name, "status": "ok", "result": res, "_meta": UNTRUSTED_PROVENANCE_META}

        raise PermissionError(f"Ferramenta não suportada: {name}")

    def register_in_cognition_registry(self, registry: ToolRegistry | None = None) -> ToolRegistry:
        """Registra ferramentas de verificação de leitura/diagnóstico no ToolRegistry de cognição."""
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
            name = tool["name"]
            if name not in ("sandbox_run_pytest", "sandbox_run_rust_build"):
                continue

            def _make_runner(tool_name: str):
                def _runner(**kwargs):
                    return self.execute_tool(tool_name, kwargs)
                return _runner

            contract = ToolContract(
                name=f"sandbox.{name}",
                description=tool["description"],
                input_schema=tool["inputSchema"],
                output_schema=output_schema,
                risk_level="READ_ONLY",
                timeout=30.0,
                permissions=("sandbox.verify",),
            )
            target.register(contract, _make_runner(name))
        return target
