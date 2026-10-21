
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .url_security import validate_url_scheme


DEFAULT_HOME = Path.home() / ".local/share/mark/gods-eye-view"
ALLOWED_TOOLS = frozenset({
    "get_earthquakes", "get_active_fires", "get_recent_launches",
    "aircraft_in_area", "find_aircraft", "get_aircraft_track", "get_aircraft_info",
    "vessels_in_area", "find_vessel", "get_vessel_track", "next_satellite_pass",
    "satellites_overhead", "get_weather_map", "get_recent_imagery",
    "find_submarine_cables", "find_infrastructure", "get_cyclones",
    "get_fire_perimeters", "get_terrain_height", "get_map_features",
    "show_in_gods_eye_view",
})
_PORTS = range(4173, 4183)
class GodseyeError(RuntimeError):
    pass


def supported_node_version(value: str) -> bool:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(value))
    if not match:
        return False
    major, minor, _patch = map(int, match.groups())
    return (major == 24 and minor >= 14) or major == 26


def _node_candidates() -> list[str]:
    configured = os.environ.get("MARK_NODE", "").strip()
    candidates = [configured] if configured else []
    candidates.extend([shutil.which("node") or ""])


    candidates.extend(sorted(glob.glob(str(Path.home() / ".cache/codex-runtimes/*/dependencies/node/bin/node"))))
    return list(dict.fromkeys(item for item in candidates if item))


def find_compatible_node() -> tuple[str, str]:
    for candidate in _node_candidates():
        path = Path(candidate).expanduser()
        try:
            result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        version = result.stdout.strip()
        if result.returncode == 0 and supported_node_version(version):
            return str(path.resolve()), version
    raise GodseyeError("God's Eye View exige Node 24.14+ ou 26. Instale uma versão compatível ou defina MARK_NODE; o serviço não foi iniciado.")


def _check_install(home: Path) -> None:
    required = (home / "package.json", home / "node_modules/vite/bin/vite.js", home / "server/mcp/stdio.js", home / "dist/panel/index.html")
    if not home.is_dir() or any(not path.is_file() for path in required):
        raise GodseyeError("A instalação separada do God's Eye View está incompleta. Nenhum serviço foi iniciado.")


def _free_loopback_port() -> int:
    for port in _PORTS:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise GodseyeError("Não encontrei uma porta livre entre 4173 e 4182 no loopback.")


def _validate_api_base(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise GodseyeError("Endereço local inválido.") from None
    if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"} or parts.username or parts.password or port not in _PORTS:
        raise GodseyeError("O God's Eye View só pode usar HTTP em uma porta local permitida.")
    return f"http://127.0.0.1:{port}"


def _schema_check(schema: dict, value, where: str = "arguments") -> None:

    expected = schema.get("type")
    valid = {
        "object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str), "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
        "integer": lambda x: isinstance(x, int) and not isinstance(x, bool), "boolean": lambda x: isinstance(x, bool),
    }
    if expected in valid and not valid[expected](value):
        raise ValueError(f"{where} deve ser {expected}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{where} não pertence às opções aceitas.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{where} está fora dos limites aceitos.")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"] or "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{where} está fora do tamanho aceito.")
    if expected == "object":
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ValueError(f"{where} não contém todos os campos obrigatórios.")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"{where} contém campos não permitidos.")
        for key, item in value.items():
            if key in properties:
                _schema_check(properties[key], item, f"{where}.{key}")
    if expected == "array":
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{where} contém itens demais.")
        if "items" in schema:
            for index, item in enumerate(value):
                _schema_check(schema["items"], item, f"{where}[{index}]")


def _rpc_request(node: str, home: Path, api_base: str, method: str, params: dict | None = None, timeout: int = 30):
    api_base = _validate_api_base(api_base)
    request = {"jsonrpc": "2.0", "id": 2, "method": method}
    if params is not None:
        request["params"] = params
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "MARK", "version": "0.1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request,
    ]
    executable = home / "server/mcp/stdio.js"
    try:
        result = subprocess.run([node, str(executable), "--api-base", api_base], cwd=home,
            input="".join(json.dumps(row, ensure_ascii=False) + "\n" for row in lines),
            capture_output=True, text=True, timeout=timeout, check=False, env={**os.environ, "NO_COLOR": "1"})
    except subprocess.TimeoutExpired:
        raise GodseyeError("A operação MCP excedeu o tempo limite e foi cancelada.") from None
    except OSError:
        raise GodseyeError("Não consegui iniciar o cliente MCP local.") from None
    if result.returncode:
        raise GodseyeError("O cliente MCP terminou com erro. O serviço local continua sob controle do MARK.")
    responses = []
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("id") == 1 and "error" in entry:
            raise GodseyeError("A negociação inicial do MCP falhou.")
        if entry.get("id") == 2:
            responses.append(entry)
    if not responses:
        raise GodseyeError("O servidor MCP não devolveu uma resposta para a operação.")
    if "error" in responses[-1]:
        error = responses[-1]["error"]
        message = str(error.get("message", "operação recusada"))[:240]
        raise GodseyeError(f"O God's Eye View recusou a operação: {message}")
    result = responses[-1].get("result", {})
    if not isinstance(result, dict):
        raise GodseyeError("O servidor MCP retornou um resultado com formato inválido.")
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise GodseyeError("O servidor MCP retornou dados que não são JSON válido.") from None
    if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
        raise GodseyeError("A resposta MCP excedeu o limite local de 2 MiB.")
    return result


class GodseyeMCP:

    def __init__(self, home: Path = DEFAULT_HOME, node: str | None = None, api_base: str = "http://127.0.0.1:4173"):
        self.home = Path(home).expanduser()
        self.node = node
        self.api_base = _validate_api_base(api_base)

    def _node(self) -> str:
        return self.node or find_compatible_node()[0]

    def list_tools(self) -> list[dict]:
        _check_install(self.home)
        result = _rpc_request(self._node(), self.home, self.api_base, "tools/list", {}, timeout=15)
        return [tool for tool in result.get("tools", []) if tool.get("name") in ALLOWED_TOOLS]

    def call_tool(self, name: str, arguments: dict, timeout: int = 30) -> dict:
        if name not in ALLOWED_TOOLS:
            raise PermissionError("Esta ferramenta MCP não está autorizada pelo MARK.")
        if not isinstance(arguments, dict):
            raise ValueError("Os argumentos precisam ser um objeto JSON.")
        tools = {tool["name"]: tool for tool in self.list_tools()}
        tool = tools.get(name)
        if not tool:
            raise GodseyeError("A ferramenta solicitada não está disponível no servidor atual.")
        _schema_check(tool.get("inputSchema", {"type": "object"}), arguments)
        result = _rpc_request(self._node(), self.home, self.api_base, "tools/call",
                              {"name": name, "arguments": arguments}, timeout=timeout)
        if result.get("isError"):
            details = next((item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"), "falha da ferramenta")
            raise GodseyeError(f"A ferramenta falhou: {details[:240]}")
        return result


class GodsEyeManager:

    def __init__(self, home: Path = DEFAULT_HOME, node_lookup=find_compatible_node, popen=subprocess.Popen):
        self.home = Path(home).expanduser()
        self.node_lookup = node_lookup
        self.popen = popen
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self.api_base: str | None = None
        self._node: str | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, timeout: float = 12) -> str:
        with self._lock:
            if self.running and self.api_base:
                return self.api_base
            _check_install(self.home)
            node, _version = self.node_lookup()
            port = _free_loopback_port()
            base = f"http://127.0.0.1:{port}"
            argv = [node, str(self.home / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1",
                    "--port", str(port), "--strictPort"]
            try:
                process = self.popen(argv, cwd=self.home, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            except OSError:
                raise GodseyeError("Não consegui iniciar o serviço local. Verifique Node e dependências.") from None
            self._process = process
            self._node = node
            self.api_base = base
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self._clear()
                    raise GodseyeError("O servidor local encerrou durante a inicialização.")
                try:
                    probe_url = base + "/"
                    validate_url_scheme(probe_url)
                    with urllib.request.urlopen(probe_url, timeout=0.4) as response:  # nosec: B310
                        if response.status == 200:
                            return base
                except (OSError, urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(0.1)
            self.stop()
            raise GodseyeError("O servidor não ficou pronto no tempo limite; o processo iniciado foi encerrado.")

    def _clear(self):
        self._process = None
        self._node = None
        self.api_base = None

    def stop(self, timeout: float = 4) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=2)
                except ProcessLookupError:
                    pass
            self._clear()

    def call_tool(self, name: str, arguments: dict, timeout: int = 30) -> dict:
        with self._lock:
            if not self.running or not self.api_base:
                raise GodseyeError("O serviço local do globo está desligado.")
            client = GodseyeMCP(self.home, self._node, self.api_base)
        return client.call_tool(name, arguments, timeout)

    def close(self) -> None:
        self.stop()
