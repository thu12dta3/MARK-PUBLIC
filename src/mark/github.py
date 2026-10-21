
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from urllib.parse import quote


REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^[A-Za-z0-9_./-]+$")


class GitHubError(RuntimeError):
    pass


def _public_api(endpoint: str) -> dict | list:

    from .web_access import fetch
    result = fetch("https://api.github.com/" + endpoint.lstrip("/"), timeout=15)
    if result["content_type"] != "application/json":
        raise GitHubError("A API pública do GitHub não devolveu JSON.")
    try:
        return json.loads(result["text"])
    except json.JSONDecodeError:
        raise GitHubError("A resposta pública do GitHub estava malformada.") from None


def _gh(args: list[str], timeout: int = 30, input_data: str | None = None) -> str:
    executable = shutil.which("gh")
    if not executable:
        raise GitHubError("Instale o GitHub CLI (gh) para conectar o MARK ao GitHub.")
    try:
        result = subprocess.run(
            [executable, *args], capture_output=True, text=True, timeout=timeout,
            check=False, env=None, input=input_data,
        )
    except subprocess.TimeoutExpired:
        raise GitHubError("O GitHub demorou demais para responder. Tente novamente.") from None
    except OSError:
        raise GitHubError("Não consegui iniciar o GitHub CLI.") from None
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if "not logged" in detail.lower() or "gh auth login" in detail.lower():
            raise GitHubError(
                "Conclua a autorização que está aberta no Brave. Depois, volte aqui e toque em Atualizar lista."
            )
        raise GitHubError((detail[:500] or "O GitHub recusou a solicitação.").replace("\n", " "))
    return result.stdout


def account() -> str:
    return _gh(["api", "user", "--jq", ".login"], timeout=15).strip()


def repositories(limit: int = 100) -> list[dict]:
    data = json.loads(_gh([
        "repo", "list", "--limit", str(max(1, min(int(limit), 100))),
        "--json", "nameWithOwner,description,defaultBranchRef",
    ], timeout=30))
    repos = []
    for item in data:
        branch = item.get("defaultBranchRef") or {}
        repos.append({
            "name": item.get("nameWithOwner", ""),
            "branch": branch.get("name", "main"),
            "description": item.get("description") or "",
        })
    return repos


def _validate(repo: str, branch: str, path: str) -> tuple[str, str, str]:
    repo, branch, path = repo.strip(), branch.strip(), path.strip().strip("/")
    if not REPOSITORY.fullmatch(repo):
        raise GitHubError("Informe o repositório como dono/nome.")
    if not BRANCH.fullmatch(branch) or ".." in branch or branch.startswith("/"):
        raise GitHubError("Nome de branch inválido.")
    parts = path.split("/")
    if not path or any(part in {"", ".", ".."} for part in parts):
        raise GitHubError("Informe o caminho de um arquivo válido dentro do repositório.")
    return repo, branch, "/".join(parts)


def read_file(repo: str, branch: str, path: str) -> tuple[str, str]:
    repo, branch, path = _validate(repo, branch, path)
    endpoint = f"repos/{repo}/contents/{quote(path, safe='/')}?ref={quote(branch, safe='')}"
    try:
        data = json.loads(_gh(["api", endpoint], timeout=30))
    except GitHubError:
        data = _public_api(endpoint)
    if data.get("type") != "file" or not data.get("content"):
        raise GitHubError("Esse caminho não aponta para um arquivo de texto compatível.")
    try:
        content = base64.b64decode(data["content"], validate=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError, KeyError):
        raise GitHubError("O arquivo não está em texto UTF-8; não abri no editor.") from None
    return str(data["sha"]), content


def list_root(repo: str, branch: str) -> list[dict]:

    repo, branch, _ = _validate(repo, branch, "README.md")
    endpoint = f"repos/{repo}/contents?ref={quote(branch, safe='')}"
    try:
        data = json.loads(_gh(["api", endpoint], timeout=30))
    except GitHubError:
        data = _public_api(endpoint)
    if not isinstance(data, list):
        raise GitHubError("O GitHub não devolveu uma lista de arquivos para a raiz.")
    return [{"name": str(item.get("name", ""))[:255], "type": item.get("type", "")}
            for item in data[:100] if isinstance(item, dict)]


def update_file(repo: str, branch: str, path: str, sha: str,
                message: str, content: str) -> str:
    repo, branch, path = _validate(repo, branch, path)
    message = message.strip()
    if not message or len(message) > 200:
        raise GitHubError("Escreva uma mensagem de commit de até 200 caracteres.")
    raw = content.encode("utf-8")
    if len(raw) > 1_500_000:
        raise GitHubError("O arquivo excede o limite de 1,5 MB do editor do MARK.")
    endpoint = f"repos/{repo}/contents/{quote(path, safe='/')}"
    payload = json.dumps({
        "message": message,
        "content": base64.b64encode(raw).decode("ascii"),
        "sha": sha,
        "branch": branch,
    })
    data = json.loads(_gh(
        ["api", "--method", "PUT", endpoint, "--input", "-"],
        timeout=60, input_data=payload,
    ))
    commit = data.get("commit") or {}
    return str(commit.get("html_url") or commit.get("sha") or "Commit criado.")
