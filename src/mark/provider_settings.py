
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

from .url_security import validate_url_scheme


CONFIG_PATH = Path.home() / ".config/mark/provider-settings.json"
PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "models_url": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
        "chat_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_env": "GEMINI_API_KEY",
    },
    "groq": {
        "label": "Groq",
        "models_url": "https://api.groq.com/openai/v1/models",
        "chat_url": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
    },
    "openrouter": {
        "label": "OpenRouter",
        "models_url": "https://openrouter.ai/api/v1/models",
        "chat_url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
    },
}
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


class ProviderError(RuntimeError):
    pass


def _provider(name: str) -> dict:
    if name not in PROVIDERS:
        raise ValueError("Provedor desconhecido.")
    return PROVIDERS[name]


def secret_tool_available() -> bool:
    return shutil.which("secret-tool") is not None


def _secret_args(name: str) -> list[str]:
    _provider(name)
    return ["service", "mark", "provider", name]


def store_api_key(name: str, value: str) -> None:
    provider = _provider(name)
    secret = value.strip() if isinstance(value, str) else ""
    if not secret or len(secret) > 4096 or "\n" in secret or "\r" in secret:
        raise ValueError("Informe uma chave válida em uma única linha.")
    executable = shutil.which("secret-tool")
    if not executable:
        raise ProviderError("O Secret Service não está disponível; a chave não foi salva.")
    try:
        result = subprocess.run(
            [executable, "store", "--label", f"MARK · {provider['label']}", *_secret_args(name)],
            input=secret + "\n", text=True, capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderError("Não consegui acessar o cofre do sistema; a chave não foi salva.") from None
    if result.returncode:
        raise ProviderError("O cofre do sistema recusou o armazenamento; nenhum arquivo local foi usado como alternativa.")


def get_api_key(name: str) -> str | None:
    provider = _provider(name)
    env_value = os.environ.get(provider["key_env"], "").strip()
    if env_value:
        return env_value
    executable = shutil.which("secret-tool")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "lookup", *_secret_args(name)], capture_output=True,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value and len(value) <= 4096 else None


def key_source(name: str) -> str | None:
    spec = _provider(name)
    if os.environ.get(spec["key_env"], "").strip():
        return "ambiente"
    return "cofre do sistema" if get_api_key(name) else None


def delete_api_key(name: str) -> bool:
    _provider(name)
    executable = shutil.which("secret-tool")
    if not executable:
        raise ProviderError("O Secret Service não está disponível; nenhuma chave foi removida.")
    try:
        result = subprocess.run(
            [executable, "clear", *_secret_args(name)], capture_output=True,
            text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderError("Não consegui acessar o cofre do sistema.") from None
    if result.returncode:
        raise ProviderError("O cofre do sistema não confirmou a remoção.")
    return True


def _load_settings() -> dict:
    path = CONFIG_PATH
    if not path.exists():
        return {name: {"enabled": False, "free_only_confirmed": False, "model": ""} for name in PROVIDERS}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ProviderError("As configurações dos provedores não estão em um arquivo local privado.")
    if info.st_size > 32_768:
        raise ProviderError("O arquivo de provedores é grande demais.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProviderError("As configurações locais dos provedores não puderam ser lidas.") from None
    if not isinstance(value, dict):
        raise ProviderError("Formato de configurações local inválido.")
    result = {name: {"enabled": False, "free_only_confirmed": False, "model": ""} for name in PROVIDERS}
    for name in PROVIDERS:
        raw = value.get(name, {})
        if not isinstance(raw, dict):
            continue
        model = raw.get("model", "")
        result[name] = {
            "enabled": raw.get("enabled") is True,
            "free_only_confirmed": raw.get("free_only_confirmed") is True,
            "model": model if isinstance(model, str) and _MODEL_ID.fullmatch(model) else "",
        }
    return result


def provider_settings(name: str) -> dict:
    _provider(name)
    return dict(_load_settings()[name])


def set_provider_settings(name: str, *, model: str, enabled: bool, free_only_confirmed: bool) -> None:
    _provider(name)
    if type(enabled) is not bool or type(free_only_confirmed) is not bool:
        raise ValueError("Opções inválidas para o provedor.")
    model = model.strip() if isinstance(model, str) else ""
    if model and not _MODEL_ID.fullmatch(model):
        raise ValueError("Identificador de modelo inválido.")
    if enabled and (not model or not free_only_confirmed):
        raise ValueError("Selecione um modelo e confirme manualmente a condição sem cobrança.")
    if name == "openrouter" and enabled and model != "openrouter/free" and not model.endswith(":free"):
        raise ValueError("No OpenRouter, o roteador aceita somente modelos marcados como :free.")
    current = _load_settings()
    current[name] = {"enabled": enabled, "free_only_confirmed": free_only_confirmed, "model": model}
    path = CONFIG_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_uid != os.getuid():
        raise ProviderError("A pasta de configurações não pertence ao usuário atual.")
    if not path.parent.is_dir():
        raise ProviderError("A pasta de configurações local não é um diretório.")
    path.parent.chmod(0o700)
    payload = json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def provider_enabled(name: str) -> bool:
    try:
        config = provider_settings(name)
    except (ValueError, ProviderError):
        return False
    return config["enabled"] and config["free_only_confirmed"] and bool(config["model"])


def selected_model(name: str) -> str | None:
    try:
        config = provider_settings(name)
    except (ValueError, ProviderError):
        return None
    return config["model"] if config["enabled"] and config["free_only_confirmed"] else None


def _request_models(name: str) -> list[dict]:
    spec = _provider(name)
    key = get_api_key(name)
    if not key:
        raise ProviderError("Nenhuma chave configurada no cofre do sistema ou no ambiente.")
    headers = {"Accept": "application/json", "User-Agent": "MARK-local/1"}
    if name == "gemini":
        headers["x-goog-api-key"] = key
    else:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(spec["models_url"], headers=headers, method="GET")
    validate_url_scheme(request)
    try:
        with urllib.request.urlopen(request, timeout=12) as response:  # nosec: B310
            if response.status != 200:
                raise ProviderError(f"O provedor respondeu HTTP {response.status}.")
            body = response.read(1_000_001)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ProviderError("Chave recusada pelo provedor (401).") from None
        if exc.code == 403:
            raise ProviderError("Acesso negado pelo provedor (403); confira restrições ou permissões da conta.") from None
        if exc.code == 429:
            raise ProviderError("Limite ou cota do provedor atingido (429).") from None
        raise ProviderError(f"O provedor recusou a consulta de modelos (HTTP {exc.code}).") from None
    except (OSError, urllib.error.URLError, TimeoutError):
        raise ProviderError("Não consegui alcançar o provedor; o MARK local continua disponível.") from None
    if len(body) > 1_000_000:
        raise ProviderError("A lista de modelos retornada excede o limite local.")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ProviderError("A resposta do provedor não contém uma lista de modelos válida.") from None
    if name == "gemini":
        rows = data.get("models", []) if isinstance(data, dict) else []
        models = [
            {"id": row.get("name", "").removeprefix("models/"), "free_candidate": False}
            for row in rows if isinstance(row, dict)
            and "generateContent" in row.get("supportedGenerationMethods", [])
        ]
    else:
        rows = data.get("data", []) if isinstance(data, dict) else []
        models = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            free = name == "openrouter" and (row["id"].endswith(":free") or row["id"] == "openrouter/free")
            models.append({"id": row["id"], "free_candidate": free})
    result = []
    for row in models:
        model_id = row.get("id")
        if isinstance(model_id, str) and _MODEL_ID.fullmatch(model_id):
            result.append({"id": model_id, "free_candidate": row.get("free_candidate") is True})
    return sorted(result, key=lambda item: item["id"])[:500]


def list_models(name: str) -> list[dict]:

    return _request_models(name)
