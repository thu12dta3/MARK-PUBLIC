
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

CONFIG = Path.home() / ".config/mark/jetbrains-mcp.json"
MAX_RESPONSE = 2 * 1024 * 1024


def validate_endpoint(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Endereço MCP inválido.")
    parsed = urlsplit(value.strip())
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Por segurança, o servidor JetBrains precisa ser HTTP local (localhost) e sem credenciais.")
    return value.strip().rstrip("/")


def endpoint_from_clipboard(value: str) -> str:

    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return validate_endpoint(value)
    urls = []
    def visit(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key.casefold() in {"url", "endpoint"} and isinstance(child, str):
                    urls.append(child)
                else:
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(data)
    if len(urls) != 1:
        raise ValueError("Cole uma única configuração HTTP Stream da IDE ou o endereço local MCP.")
    return validate_endpoint(urls[0])


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JetBrainsMCP:
    def __init__(self, endpoint: str, timeout: float = 12):
        self.endpoint = validate_endpoint(endpoint)
        self.timeout = timeout
        self.session_id = None
        self.protocol = "2025-03-26"
        self._id = 0
        self._opener = build_opener(_NoRedirect())

    def _post(self, method: str, params=None, notification=False):
        self._id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self._id
        if params is not None:
            payload["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = self.protocol
        req = Request(self.endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                self.session_id = response.headers.get("MCP-Session-Id", self.session_id)
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as exc:
            if notification and exc.code in {202, 204}:
                return None
            raise RuntimeError(f"A IDE recusou a conexão MCP (HTTP {exc.code}).") from None
        except (URLError, TimeoutError, OSError):
            raise RuntimeError("Não consegui alcançar o MCP local. Confirme se o servidor está ativo na IDE.") from None
        if notification:
            return None
        if len(raw) > MAX_RESPONSE:
            raise RuntimeError("A resposta da IDE ultrapassou o limite de 2 MiB.")
        content_type = response.headers.get("Content-Type", "")
        try:
            if "text/event-stream" in content_type:
                events = [line[5:].strip() for line in raw.decode("utf-8").splitlines() if line.startswith("data:")]
                decoded = [json.loads(item) for item in events if item]
                reply = next(item for item in decoded if item.get("id") == self._id)
            else:
                reply = json.loads(raw)
        except (ValueError, UnicodeDecodeError, StopIteration):
            raise RuntimeError("A IDE retornou uma resposta MCP inválida.") from None
        if reply.get("error"):
            message = str(reply["error"].get("message", "erro MCP"))[:240]
            raise RuntimeError("Falha MCP da IDE: " + message)
        return reply.get("result", {})

    def connect(self):
        result = self._post("initialize", {"protocolVersion": self.protocol, "capabilities": {},
                                           "clientInfo": {"name": "MARK Desktop", "version": "1.0"}})
        if not isinstance(result, dict) or "serverInfo" not in result:
            raise RuntimeError("A IDE não confirmou a inicialização MCP.")
        self.protocol = result.get("protocolVersion", self.protocol)
        self._post("notifications/initialized", notification=True)
        return result["serverInfo"]

    def list_tools(self):
        result = self._post("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise RuntimeError("A lista de ferramentas da IDE é inválida.")
        return tools[:200]

    def call_tool(self, name: str, arguments: dict):
        result = self._post("tools/call", {"name": name, "arguments": arguments})
        if isinstance(result, dict) and result.get("isError"):
            details = next((item.get("text", "") for item in result.get("content", [])
                            if isinstance(item, dict) and item.get("type") == "text"), "falha da ferramenta")
            raise RuntimeError("A ferramenta da IDE falhou: " + str(details)[:240])
        return result


def save_endpoints(values: dict[str, str]):
    if set(values) != {"pycharm", "clion"}:
        raise ValueError("Configuração JetBrains inválida.")
    clean = {key: validate_endpoint(value) if value.strip() else "" for key, value in values.items()}
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG.parent.chmod(0o700)
    fd, name = tempfile.mkstemp(prefix=".jetbrains-mcp-", dir=CONFIG.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(clean, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, CONFIG)
        os.chmod(CONFIG, 0o600)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def load_endpoints():
    if CONFIG.is_symlink():
        raise ValueError("Configuração JetBrains em symlink recusada.")
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"pycharm": "", "clion": ""}
    except (OSError, ValueError):
        raise ValueError("Não consegui ler a configuração local das IDEs.") from None
    if not isinstance(data, dict):
        raise ValueError("A configuração local das IDEs está inválida.")
    return {key: validate_endpoint(data.get(key, "")) if data.get(key) else "" for key in ("pycharm", "clion")}
