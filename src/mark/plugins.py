
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import CONFIG_FILE


PLUGINS = (
    {
        "id": "web",
        "name": "Internet e páginas",
        "description": "Pesquisa na web e leitura de páginas públicas quando o pedido exigir.",
        "permissions": ("Pesquisar na internet", "Ler páginas públicas"),
        "default_enabled": True,
        "source": "Nativo · protegido pelo leitor web do MARK",
    },
    {
        "id": "github",
        "name": "GitHub",
        "description": "Lê repositórios indicados e permite editar pelo painel GitHub do PC.",
        "permissions": ("Ler repositórios", "Editar exige confirmação na ação"),
        "default_enabled": True,
        "source": "Nativo · credenciais ficam no sistema",
    },
    {
        "id": "gods_eye",
        "name": "God’s Eye View",
        "description": "Consulta ferramentas geográficas e abre a visualização local sob demanda.",
        "permissions": ("Consultar dados geográficos", "Abrir página local de visualização"),
        "default_enabled": True,
        "source": "Nativo · lista de ferramentas permitidas",
    },
)


def enabled(plugin_id: str, cfg: dict | None = None) -> bool:

    spec = next((item for item in PLUGINS if item["id"] == plugin_id), None)
    if spec is None:
        return False
    cfg = cfg or {}
    plugins = cfg.get("plugins", {})


    try:
        saved = json.loads(Path(CONFIG_FILE).read_text(encoding="utf-8"))
        saved_plugins = saved.get("plugins", {})
        if isinstance(saved_plugins, dict) and plugin_id in saved_plugins:
            plugins = saved_plugins
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    if plugin_id in plugins:
        return plugins[plugin_id] is True

    if plugin_id == "web":
        return cfg.get("web", {}).get("enabled", spec["default_enabled"]) is not False
    if plugin_id == "github":
        return cfg.get("github", {}).get("enabled", spec["default_enabled"]) is not False
    return spec["default_enabled"]


def save_enabled(plugin_id: str, is_enabled: bool) -> None:

    if plugin_id not in {item["id"] for item in PLUGINS} or type(is_enabled) is not bool:
        raise ValueError("Preferência de plugin inválida.")
    path = Path(CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        raise ValueError("Não consegui ler a configuração local; nenhum plugin foi alterado.") from None
    if not isinstance(current, dict):
        raise ValueError("A configuração local não tem o formato esperado.")
    selected = current.setdefault("plugins", {})
    if not isinstance(selected, dict):
        raise ValueError("A seção de plugins na configuração está inválida.")
    selected[plugin_id] = is_enabled
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".mark-config-", delete=False) as stream:
            temp_name = stream.name
            os.chmod(temp_name, 0o600)
            json.dump(current, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except OSError:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise ValueError("Não consegui salvar a preferência do plugin.") from None
