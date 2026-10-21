
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import shutil
import subprocess
from uuid import uuid4


@dataclass(frozen=True)
class InstallRequest:
    manager: str
    package: str


def parse_install_request(text: str) -> InstallRequest | None:

    match = re.match(
        r"^\s*(?:(?:por favor|mark[, :]*|quero que (?:o )?mark)\s*)?(?:baixe e instale|instale|instalar|instala)\s+"
        r"(?:(pacman|flatpak|pipx)\s+)?(?:o pacote\s+|o app\s+|o aplicativo\s+)?"
        r"(?:['\"]([^'\"]{1,128})['\"]|([A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}))\s*[.!?]*\s*$",
        text, re.I,
    )
    if not match:
        return None
    manager = (match.group(1) or "").lower()
    package = (match.group(2) or match.group(3) or "").strip()
    if not package or package.startswith("-") or "/" in package or "\\" in package:
        return None
    flatpak_id = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*){1,8}", package)
    if manager == "flatpak" or (not manager and flatpak_id):
        if not flatpak_id:
            return None
        return InstallRequest("flatpak", package)
    if manager == "pipx":
        return InstallRequest("pipx", package)
    if manager not in {"", "pacman"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@_.+-]{0,127}", package):
        return None
    return InstallRequest("pacman", package)


def inspect(request: InstallRequest) -> dict:

    if request.manager == "pacman":
        binary = shutil.which("pacman")
        if not binary:
            raise RuntimeError("O pacman não está disponível neste computador.")
        result = subprocess.run([binary, "-Si", request.package], capture_output=True, text=True,
                                timeout=20, check=False)
        if result.returncode:
            raise RuntimeError("Não encontrei esse pacote nos repositórios oficiais já configurados. O MARK não usa AUR nem executa PKGBUILDs.")
        fields = {}
        for line in result.stdout.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                if key.strip() in {"Repository", "Name", "Version", "Description"}:
                    fields[key.strip()] = value.strip()[:240]
        official_repos = {"core", "extra", "multilib"}
        transaction = subprocess.run([binary, "-Sp", "--print-format", "%r/%n", request.package],
                                     capture_output=True, text=True, timeout=30, check=False)
        packages = [line.strip() for line in transaction.stdout.splitlines() if "/" in line]
        repositories = {item.split("/", 1)[0] for item in packages}
        if transaction.returncode or not packages or repositories - official_repos:
            raise RuntimeError("A transação inclui uma origem não oficial ou não pôde ser conferida; o MARK não iniciou a instalação.")
        return {"manager": "pacman", "package": fields.get("Name", request.package),
                "version": fields.get("Version", "versão do repositório"),
                "source": "repositório oficial configurado: " + fields.get("Repository", "desconhecido"),
                "description": fields.get("Description", ""), "transaction_count": len(packages),
                "transaction_preview": packages[:12]}
    if request.manager == "flatpak":
        binary = shutil.which("flatpak")
        if not binary:
            raise RuntimeError("O Flatpak não está instalado neste computador.")
        result = subprocess.run([binary, "remote-info", "--user", "flathub", request.package],
                                capture_output=True, text=True, timeout=20, check=False)
        if result.returncode:
            raise RuntimeError("Não encontrei esse aplicativo no Flathub do usuário. Nenhum outro repositório foi consultado.")
        fields = {}
        for line in result.stdout.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                if key.strip() in {"Name", "Application", "Version", "Summary"}:
                    fields[key.strip()] = value.strip()[:240]
        return {"manager": "flatpak", "package": fields.get("Application", request.package),
                "version": fields.get("Version", "versão do Flathub"), "source": "Flathub (instalação no usuário)",
                "description": fields.get("Summary", "")}
    if request.manager == "pipx":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", request.package):
            raise RuntimeError("Nome de pacote PyPI inválido.")
        if not shutil.which("pipx"):
            raise RuntimeError("O pipx não está instalado. Instale o pipx por pacman e tente novamente.")
        from .web_access import fetch
        from urllib.parse import quote
        result = fetch("https://pypi.org/pypi/" + quote(request.package, safe="") + "/json", timeout=15)
        try:
            info = json.loads(result["text"])["info"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise RuntimeError("O PyPI não devolveu metadados válidos para esse pacote.") from None
        return {"manager": "pipx", "package": str(info.get("name") or request.package)[:128],
                "version": str(info.get("version") or "versão mais recente")[:80],
                "source": "PyPI · ambiente isolado do usuário (pipx)",
                "description": str(info.get("summary") or "")[:240],
                "warning": "Pacotes Python podem executar rotinas de instalação dentro do ambiente isolado do pipx."}
    raise RuntimeError("Gerenciador de instalação não reconhecido.")


def install(request: InstallRequest) -> str:

    backup = backup_package_state(request)
    env = None
    if request.manager == "pacman":
        pacman = shutil.which("pacman")
        pkexec = shutil.which("pkexec")
        if not pacman or not pkexec:
            raise RuntimeError("Preciso do pacman e do Polkit (pkexec) para pedir autorização do sistema.")
        argv = [pkexec, pacman, "-S", "--needed", "--noconfirm", request.package]
    else:
        if request.manager == "flatpak":
            flatpak = shutil.which("flatpak")
            if not flatpak:
                raise RuntimeError("O Flatpak deixou de estar disponível.")
            argv = [flatpak, "install", "--user", "--noninteractive", "flathub", request.package]
            env = None
        else:
            pipx = shutil.which("pipx")
            if not pipx:
                raise RuntimeError("O pipx deixou de estar disponível.")
            argv = [pipx, "install", request.package]
            env = os.environ.copy()
            for key in tuple(env):
                if key.startswith("PIP_"):
                    env.pop(key, None)
            env.update({"PIP_CONFIG_FILE": os.devnull, "PIP_INDEX_URL": "https://pypi.org/simple", "PIP_NO_INPUT": "1"})
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False,
                                stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("A instalação ultrapassou 15 minutos e foi encerrada.") from None
    except OSError:
        raise RuntimeError("Não consegui iniciar a instalação autorizada.") from None
    if result.returncode:

        raise RuntimeError("A instalação não foi concluída. Confira a solicitação do sistema e os detalhes do gerenciador de pacotes.")
    return f"Instalação concluída. Backup do inventário anterior: {backup}"


def backup_package_state(request: InstallRequest) -> str:

    inventories = {}
    for manager, command in (("pacman", ["pacman", "-Q"]),
                             ("flatpak", ["flatpak", "list", "--user", "--app", "--columns=application,origin,branch"]),
                             ("pipx", ["pipx", "list", "--short"])):
        binary = shutil.which(command[0])
        if not binary:
            inventories[manager] = {"available": False}
            continue
        try:
            result = subprocess.run([binary, *command[1:],], capture_output=True, text=True,
                                    timeout=60, check=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("Não consegui salvar o inventário de programas; nada foi instalado.") from None
        if result.returncode:
            raise RuntimeError("O inventário de programas não pôde ser salvo; nada foi instalado.")
        inventories[manager] = {"available": True, "packages": result.stdout[:2_000_000]}
    if not inventories.get(request.manager, {}).get("available"):
        raise RuntimeError("Não consegui criar o backup do gerenciador escolhido; nada foi instalado.")
    folder = Path.home() / ".local/state/mark/install-backups"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("A pasta de backup não pertence ao usuário; nada foi instalado.")
    folder.chmod(0o700)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".json"
    target = folder / name
    data = json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
                       "requested_package": request.package, "manager": request.manager,
                       "inventories": inventories,
                       "note": "Inventário dos programas instalados antes da mudança; não é imagem completa do sistema nem backup de dados pessoais."},
                      ensure_ascii=False, indent=2).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return str(target)
