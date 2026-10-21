




from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .url_security import validate_url_scheme

HOME = Path.home()
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "mark/sentinel"
REPORT_FILE = STATE_DIR / "last-report.json"
MAX_OUTPUT = 2_000_000
_lock = threading.Lock()
_active: subprocess.Popen[str] | None = None
_cancel_requested = False
_active_paths: tuple[str, ...] = ()
_active_started: float | None = None


class SentinelError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _save_report(report: dict[str, Any]) -> None:

    try:
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = REPORT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(REPORT_FILE)
        os.chmod(STATE_DIR, 0o700)
    except OSError:

        pass


def latest_report() -> str:
    try:
        report = json.loads(REPORT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "Ainda não há relatório local do Sentinel."
    return "Último relatório do Sentinel — {}\n{}".format(
        report.get("created_at", "data desconhecida"), report.get("summary", "sem resumo"))


def status() -> str:

    clamscan = shutil.which("clamscan")
    freshclam = shutil.which("freshclam")
    lines = ["Estado local do MARK Sentinel (consulta somente leitura):"]
    with _lock:
        active = _active
        active_paths = _active_paths
        active_started = _active_started
    if active is not None and active.poll() is None and active_started is not None:
        lines.append(f"• Verificação em andamento há {time.monotonic() - active_started:.0f}s; alvo(s): {', '.join(active_paths)}. Para interromper, peça 'cancele a verificação do Sentinel'.")
    else:
        lines.append("• Nenhuma verificação do Sentinel está em andamento.")
    if not clamscan:
        lines.append("• ClamAV: clamscan não encontrado.")
    else:
        try:
            out = subprocess.run([clamscan, "--version"], capture_output=True, text=True,
                                 timeout=5, check=False)
            lines.append("• ClamAV: " + ((out.stdout or out.stderr).strip() or "versão não informada"))
            lines.append("• Consulta de versão: " + ("OK" if out.returncode == 0 else f"falhou (código {out.returncode})"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            lines.append(f"• ClamAV: não foi possível consultar ({type(exc).__name__}).")
    lines.append("• freshclam: " + ("disponível; não foi iniciado" if freshclam else "não encontrado"))
    db = Path("/var/lib/clamav")
    databases = [p for p in (db / "main.cvd", db / "daily.cvd", db / "bytecode.cvd") if p.is_file()]
    if databases:
        latest = max(databases, key=lambda p: p.stat().st_mtime)
        lines.append(f"• Banco local: {len(databases)} assinatura(s); arquivo mais recente {latest.name}, modificado em {_now_from_epoch(latest.stat().st_mtime)}.")
    else:
        lines.append("• Banco local: não encontrei os arquivos padrão em /var/lib/clamav; o ClamAV pode usar outro diretório.")
    lines.append("Nenhuma varredura, atualização de assinaturas ou ativação de serviço foi feita.")
    return "\n".join(lines)


def _now_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="minutes")


def validate_path(raw: str) -> Path:
    value = raw.strip().strip("\"'")
    if not value or "\x00" in value:
        raise SentinelError("Informe um caminho de arquivo ou pasta válido.")
    expanded = Path(value).expanduser()
    try:

        candidate = expanded.absolute()
        if any(part.is_symlink() for part in (candidate, *candidate.parents)):
            raise SentinelError("O caminho informado atravessa um link simbólico; indique o destino real explicitamente.")
        resolved = expanded.resolve(strict=True)
        info = resolved.stat()
    except FileNotFoundError:
        raise SentinelError("Não encontrei esse caminho. Confira o nome e tente novamente.") from None
    except PermissionError:
        raise SentinelError("Sem permissão para acessar esse caminho.") from None
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise SentinelError("O Sentinel aceita somente arquivos regulares e pastas.")
    return resolved


def scan_path(raw_paths: list[str] | tuple[str, ...], timeout: int = 900) -> str:

    global _active, _cancel_requested, _active_paths, _active_started
    paths: list[Path] = []
    for raw in raw_paths:
        path = validate_path(raw)
        if path not in paths:
            paths.append(path)
    if not paths:
        raise SentinelError("Indique ao menos um arquivo ou pasta para verificar.")
    binary = shutil.which("clamscan")
    if not binary:
        return "Não foi possível iniciar: o clamscan não está instalado ou não está no PATH."


    argv = [binary, "--infected", "--no-summary"]
    if any(p.is_dir() for p in paths):
        argv.append("--recursive")
    argv.extend(str(p) for p in paths)
    with _lock:
        if _active is not None and _active.poll() is None:
            return "Já existe uma verificação do Sentinel em andamento. Peça para cancelar antes de iniciar outra."
        _cancel_requested = False
        output_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(argv, stdout=output_file, stderr=subprocess.STDOUT,
                                    text=True, start_new_session=True)
        except OSError as exc:
            output_file.close()
            return f"Falha ao iniciar o ClamAV: {type(exc).__name__}: {exc}"
        _active = proc
    started = time.monotonic()
    with _lock:
        _active_paths = tuple(str(path) for path in paths)
        _active_started = started

    def read_output() -> str:
        output_file.flush()
        output_file.seek(0)
        return output_file.read(MAX_OUTPUT + 1)

    try:
        try:
            proc.wait(timeout=max(1, min(int(timeout), 1800)))
        except subprocess.TimeoutExpired:
            _terminate(proc)
            proc.wait()
            return _scan_result("incomplete", "A verificação excedeu o limite de tempo e foi interrompida.",
                                paths, read_output(), time.monotonic() - started)
        with _lock:
            cancelled = _cancel_requested
        if cancelled:
            return _scan_result("cancelled", "A verificação foi cancelada; o resultado é parcial.", paths, read_output(), time.monotonic() - started)
        code = proc.returncode

        if code == 0:
            kind, note = "clean", "O ClamAV terminou sem detecções nos itens que conseguiu examinar."
        elif code == 1:
            kind, note = "infected", "O ClamAV reportou uma ou mais detecções. Nenhum arquivo foi alterado."
        else:
            kind, note = "error", f"O ClamAV terminou com erro (código {code}); não interprete como resultado limpo."
        return _scan_result(kind, note, paths, read_output(), time.monotonic() - started)
    finally:
        output_file.close()
        with _lock:
            if _active is proc:
                _active = None
                _active_paths = ()
                _active_started = None
            _cancel_requested = False


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass


def cancel_scan() -> str:
    global _cancel_requested
    with _lock:
        proc = _active
        if proc is None or proc.poll() is not None:
            return "Não há verificação do Sentinel em andamento para cancelar."
        _cancel_requested = True
    _terminate(proc)
    return "Cancelamento solicitado. A verificação será reportada como parcial; nenhum arquivo será alterado."


def _scan_result(kind: str, note: str, paths: list[Path], output: str, duration: float) -> str:
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + "\n[saída técnica truncada]"
    relevant = [line.strip() for line in output.splitlines() if line.strip()]

    findings = [line for line in relevant if " FOUND" in line or " ERROR" in line or " UNABLE TO" in line]
    shown = findings[:100] if findings else relevant[-12:]
    summary = f"{note}\nAlvos: {', '.join(str(p) for p in paths)}\nDuração: {duration:.1f}s"
    if shown:
        summary += "\nDetalhes do ClamAV:\n" + "\n".join(shown)
    report = {"created_at": _now(), "kind": kind, "summary": summary,
              "paths": [str(p) for p in paths], "duration_seconds": round(duration, 1),
              "findings": findings[:100]}
    _save_report(report)
    return summary


def investigate_file(raw: str, *, query_external: bool = False, cfg: dict | None = None) -> str:
    path = validate_path(raw)
    if not path.is_file():
        raise SentinelError("A investigação de arquivo exige um arquivo, não uma pasta.")
    st = path.stat()
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SentinelError(f"Não consegui ler o arquivo para calcular o hash ({type(exc).__name__}).") from None
    sha256 = digest.hexdigest()
    with path.open("rb") as handle:
        magic = handle.read(8)
    type_hint = ("ELF executável ou biblioteca" if magic.startswith(b"\x7fELF") else
                 "PDF" if magic.startswith(b"%PDF-") else
                 "arquivo ZIP/contêiner" if magic.startswith(b"PK\x03\x04") else
                 "executável PE/Windows" if magic.startswith(b"MZ") else
                 mimetypes.guess_type(path.name)[0] or "tipo não identificado por assinatura simples")
    details = [f"Investigação somente leitura: {path}", f"Tamanho: {st.st_size} bytes",
               f"Tipo indicativo: {type_hint} (não é uma análise estrutural completa)",
               f"Modificado: {_now_from_epoch(st.st_mtime)}", f"Permissões: {stat.filemode(st.st_mode)}",
               f"SHA-256: {sha256}"]
    try:
        import pwd
        owner = pwd.getpwuid(st.st_uid).pw_name
    except (ImportError, KeyError):
        owner = str(st.st_uid)
    details.append(f"Proprietário: {owner}")
    scan = scan_path([str(path)])
    details.append("\nClamAV:\n" + scan)
    if " FOUND" in scan:
        risk, basis = "Alto", "O ClamAV reportou uma detecção; preserve o nome exato acima para interpretação."
    elif "terminou sem detecções" in scan:
        risk, basis = "Informativo", "O ClamAV não reportou detecção no arquivo examinado; isso não garante segurança."
    else:
        risk, basis = "Inconclusivo", "A verificação não confirmou um resultado limpo nem uma detecção."
    details.append(f"\nClassificação: {risk}. {basis}")
    if query_external:
        section = (cfg or {}).get("sentinel", {})

        if not section.get("share_hash_with_virustotal", False):
            details.append("\nVirusTotal: consulta não enviada. Habilite explicitamente sentinel.share_hash_with_virustotal após revisar a política de privacidade; nenhum arquivo foi enviado.")
        elif not section.get("virustotal_api_key"):
            details.append("\nVirusTotal: não configurado (chave ausente); nenhum dado foi enviado.")
        else:
            details.append("\nVirusTotal:\n" + _virustotal_hash(sha256, str(section["virustotal_api_key"])))
    else:
        details.append("\nInteligência externa: não consultada. Nenhum hash ou arquivo foi enviado.")
    result = "\n".join(details)
    _save_report({"created_at": _now(), "kind": "file-investigation", "summary": result,
                  "sha256": sha256, "path": str(path)})
    return result


def _virustotal_hash(sha256: str, api_key: str) -> str:
    request = urllib.request.Request(
        "https://www.virustotal.com/api/v3/files/" + sha256,
        headers={"x-apikey": api_key, "accept": "application/json"}, method="GET")
    validate_url_scheme(request)
    try:
        with urllib.request.urlopen(request, timeout=12) as response:  # nosec: B310
            body = json.loads(response.read(512_000))
    except urllib.error.HTTPError as exc:
        return f"Consulta falhou (HTTP {exc.code}); chave não exibida."
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        return f"Consulta falhou ({type(exc).__name__}); nenhum arquivo foi enviado."
    attrs = body.get("data", {}).get("attributes", {})
    stats = attrs.get("last_analysis_stats", {})
    if not stats:
        return "A fonte não retornou estatísticas; resultado inconclusivo."
    malicious = int(stats.get("malicious", 0) or 0)
    suspicious = int(stats.get("suspicious", 0) or 0)
    harmless = int(stats.get("harmless", 0) or 0)
    undetected = int(stats.get("undetected", 0) or 0)
    checked = datetime.fromtimestamp(int(attrs.get("last_analysis_date", 0)), timezone.utc).isoformat() if attrs.get("last_analysis_date") else "data não fornecida"
    return f"Relatório por hash: malicioso={malicious}, suspeito={suspicious}, não detectado={undetected}, benigno={harmless}; análise registrada em {checked}. Resultado de terceiros, não prova definitiva."


def investigate_process(pid: int | None = None) -> str:

    rows: list[dict[str, Any]] = []
    proc_root = Path("/proc")
    selected = [str(pid)] if pid is not None else [p.name for p in proc_root.iterdir() if p.name.isdigit()]
    for name in selected[:3000]:
        if not name.isdigit():
            raise SentinelError("PID inválido.")
        base = proc_root / name
        try:
            status_data = (base / "status").read_text(encoding="utf-8", errors="replace")
            fields = dict(line.split(":", 1) for line in status_data.splitlines() if ":" in line)
            exe = os.readlink(base / "exe")
            cmd = (base / "comm").read_text(encoding="utf-8", errors="replace").strip()
            uid_text = fields.get("Uid", "").strip().split()
            uid = int(uid_text[0]) if uid_text else -1
            ppid = int(fields.get("PPid", "0").strip() or 0)
            suspicious = exe.startswith(("/tmp/", "/var/tmp/", "/dev/shm/")) or " (deleted)" in exe  # nosec: B108
            rows.append({"pid": int(name), "name": cmd[:128], "exe": exe[:512], "uid": uid,
                         "ppid": ppid, "suspicious": suspicious})
        except (OSError, ValueError, PermissionError):
            continue
    if pid is not None and not rows:
        return f"Não foi possível ler o processo PID {pid}; pode ter encerrado ou estar protegido."
    hits = [r for r in rows if r["suspicious"]]
    persistence = _persistence_entries()
    connections: list[str] = []
    ss = shutil.which("ss")
    if ss:
        try:
            net = subprocess.run([ss, "-H", "-tunap"], capture_output=True, text=True,
                                 timeout=5, check=False)
            connections = [line[:300] for line in net.stdout.splitlines()[:30]]
        except (OSError, subprocess.TimeoutExpired):
            connections = ["Consulta de conexões não disponível ou expirou."]
    else:
        connections = ["Ferramenta ss não instalada; conexões não foram correlacionadas."]
    summary = ["Investigação local sob demanda; nenhum processo ou serviço foi alterado.",
               f"Processos legíveis: {len(rows)}" + (f"; PID filtrado: {pid}" if pid else " (lista limitada aos primeiros 3.000)."),
               f"Indícios de localização incomum: {len(hits)}"]
    for r in (hits if pid is None else rows)[:25]:
        summary.append(f"• PID {r['pid']} {r['name']} — executável {r['exe']}; UID {r['uid']}; PPID {r['ppid']}.")
        if r["suspicious"]:
            summary.append("  Sinal para investigar: caminho temporário/removido; isso isoladamente não prova malware.")
    summary.append(f"Entradas de persistência observadas: {len(persistence)}; veja as primeiras até 40 abaixo.")
    summary.extend("• " + row for row in persistence[:40])
    summary.append("Conexões locais observáveis (ss; até 30 linhas):")
    summary.extend("• " + row for row in connections)
    if not hits:
        summary.append("Nenhum dos sinais simples de caminho temporário/removido foi encontrado; isso não comprova que o sistema está limpo.")
    result = "\n".join(summary)
    risk = "Médio" if hits and persistence else "Baixo" if hits else "Informativo"
    summary.append(f"Classificação heurística: {risk}. É triagem baseada em caminho e inventário; processos desconhecidos e conexões não são prova de malware.")
    result = "\n".join(summary)
    _save_report({"created_at": _now(), "kind": "process-investigation", "summary": result,
                  "process_count": len(rows), "unusual_path_count": len(hits), "persistence_count": len(persistence)})
    return result


def _persistence_entries() -> list[str]:
    paths = [HOME / ".config/autostart", HOME / ".config/systemd/user",
             HOME / ".config/environment.d", HOME / ".local/share/systemd/user",
             HOME / ".config/fish/conf.d", HOME / ".bashrc", HOME / ".profile",
             HOME / ".zshrc", HOME / ".config" / "cron"]
    entries: list[str] = []
    for root in paths:
        if root.is_file():
            try:
                for line in root.read_text(encoding="utf-8", errors="replace").splitlines():
                    if re.match(r"\s*(?:Exec|ExecStart|WantedBy|@(?:reboot|daily|hourly)|[^#\s].*(?:cron|curl|wget|nohup|systemctl|python|node|nc))", line, re.IGNORECASE):
                        entries.append(_redact_line(f"{root}: {line[:240]}"))
            except OSError:
                entries.append(f"{root}: não foi possível ler")
        elif root.is_dir():
            for item in list(root.iterdir())[:300]:
                if item.is_file() and item.suffix in {".desktop", ".service", ".timer", ".conf"}:
                    try:
                        content = item.read_text(encoding="utf-8", errors="replace").splitlines()
                    except OSError:
                        entries.append(f"{item}: não foi possível ler")
                        continue
                    for line in content:
                        if line.startswith(("Exec=", "ExecStart=", "OnCalendar=", "WantedBy=")):
                            entries.append(_redact_line(f"{item}: {line[:240]}"))

    for root in (Path("/etc/cron.d"), Path("/etc/cron.hourly"), Path("/etc/cron.daily"), Path("/etc/xdg/autostart")):
        try:
            entries.extend(f"{root / item.name}" for item in list(root.iterdir())[:200] if item.is_file())
        except OSError:
            pass
    crontab = shutil.which("crontab")
    if crontab:
        try:
            cron = subprocess.run([crontab, "-l"], capture_output=True, text=True, timeout=5, check=False)
            if cron.returncode == 0:
                for line in cron.stdout.splitlines():
                    if line.strip() and not line.lstrip().startswith("#"):
                        entries.append(_redact_line(f"crontab do usuário: {line[:240]}"))
            elif cron.returncode not in {1}:
                entries.append("crontab do usuário: não foi possível consultar")
        except (OSError, subprocess.TimeoutExpired):
            entries.append("crontab do usuário: consulta indisponível ou expirou")
    for binary, args in ((shutil.which("systemctl"), ["--user", "list-unit-files", "--no-legend", "--no-pager"]),
                         (shutil.which("systemctl"), ["list-unit-files", "--no-legend", "--no-pager"])):
        if not binary:
            break
        try:
            proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=5, check=False)
            for line in proc.stdout.splitlines()[:300]:
                unit = line.split()
                if unit and unit[0].endswith((".service", ".timer", ".socket")):
                    entries.append(_redact_line(f"systemd {args[0] if args[0] == '--user' else 'system'}: {line[:220]}"))
        except (OSError, subprocess.TimeoutExpired):
            entries.append("systemd: listagem não disponível ou expirou")
    return list(dict.fromkeys(entries))


def handle_chat(text: str, cfg: dict) -> str | None:

    normalized = re.sub(r"\s+", " ", text.casefold()).strip(" .!?\n")
    if re.search(r"\b(?:cancele|cancela|cancelar|interrompe|interromper)\b", normalized) and any(s in normalized for s in ("sentinel", "clamav", "verifica", "verificação", "varredura")):
        return cancel_scan()
    if any(s in normalized for s in ("não faça", "nao faca", "não verifique", "nao verifique", "não investigue", "nao investigue", "como faço", "como faco", "como funciona", "o que é", "o que e")):
        return None


    device = r"(?:pc|computador|notebook|laptop|sistema)"
    threat = r"(?:vírus|virus|malware|ameaça|ameaca|antivírus|antivirus|infectado|infectada|contaminado|contaminada)"
    action = r"(?:verifique|verifica|veja|cheque|checa|analise|analisa|procure|varra|escaneie|faça|faca|faz|passe|passa)"
    direct_question = re.search(rf"\b(?:meu|minha|este|esse|o)?\s*{device}\b.*\b(?:tem|está|esta|ta|tá|com)\b.*\b{threat}\b", normalized)
    reverse_question = re.search(rf"\b{threat}\b.*\b(?:no|neste|nesse|no meu)\s+{device}\b", normalized)
    explicit_scan = re.search(rf"\b{action}\b.*\b{device}\b.*\b{threat}\b|\b{action}\b.*\b{threat}\b.*\b{device}\b", normalized)
    if direct_question or reverse_question or explicit_scan:
        result = scan_path([str(HOME)])
        return "Escopo escolhido: sua pasta pessoal. Isso não percorre todo o sistema nem caminhos que seu usuário não consegue ler.\n\n" + result
    if normalized in {"mark sentinel status", "status do sentinel", "estado do clamav", "status do clamav", "verifique o clamav"}:
        return status()
    if any(s in normalized for s in ("mostra o relatório da última investigação", "mostre o relatório da última investigação", "último relatório do sentinel")):
        return latest_report()
    if any(s in normalized for s in ("procure possíveis rogue agents", "procure rogue agents", "investigue os processos suspeitos", "investigue esse processo suspeito", "procure agentes desconhecidos", "programa desconhecido está configurado", "verifique persistência suspeita", "audite os serviços que iniciam")):
        return investigate_process()
    m = re.search(r"\b(?:investigue|analise|analisa|verifique)\s+(?:o\s+)?processo\s+(?:pid\s*)?(\d+)\b", normalized)
    if m:
        return investigate_process(int(m.group(1)))
    service = re.search(r"\b(?:investigue|verifique|analise)\s+(?:o\s+)?serviço\s+([a-zA-Z0-9@_.:-]{1,160})", text, re.IGNORECASE)
    if service:
        return investigate_service(service.group(1))
    if "investigue esse serviço" in normalized or "serviço que não reconheço" in normalized:
        return "Qual é o nome exato da unidade (por exemplo, nome.service)? Vou consultar somente estado e origem; não vou parar nem alterar o serviço."
    if any(s in normalized for s in ("investigue esse executável", "investigue esse arquivo", "analise esse arquivo", "investiga esse arquivo", "consulte as bases de inteligência", "consulta as bases de inteligência")):
        candidate = _path_from_request(text)
        if not candidate:
            return "Qual é o caminho completo do arquivo que você quer investigar?"
        external = any(s in normalized for s in ("consulte as bases", "consulta as bases", "reputação", "reputacao", "inteligência externa", "inteligencia externa", "virustotal", "consulta as bases de inteligência", "consulte as bases de inteligência"))
        try:
            return investigate_file(candidate, query_external=external, cfg=cfg)
        except SentinelError as exc:
            return str(exc)
    if any(s in normalized for s in ("consulta a reputação desse hash", "consulte a reputação desse hash", "consultar bases de inteligência para esse arquivo", "descobrir se esse arquivo é conhecido como malicioso")):
        candidate = _path_from_request(text)
        if not candidate:
            return "Informe o caminho do arquivo para eu calcular o hash. A consulta só será enviada se estiver explicitamente habilitada na configuração de privacidade."
        try:
            return investigate_file(candidate, query_external=True, cfg=cfg)
        except SentinelError as exc:
            return str(exc)
    if any(s in normalized for s in ("verificação de vírus", "verificacao de virus", "verificação completa do meu diretório", "verificacao completa do meu diretorio", "verifica se tem vírus", "verifica se tem virus", "verifique vírus", "verifica vírus", "verifica virus", "varredura do clamav", "varredura da pasta", "varre a pasta", "analisa esse arquivo com o clamav")):
        candidate = _path_from_request(text)
        if not candidate:
            if "downloads" in normalized:
                candidate = str(HOME / "Downloads")
            elif "diretório pessoal" in normalized or "diretorio pessoal" in normalized or "pasta home" in normalized:
                candidate = str(HOME)
            else:
                return "Qual arquivo ou pasta devo verificar? Informe o caminho; uma varredura completa do diretório pessoal pode demorar."
        try:
            return scan_path([candidate])
        except SentinelError as exc:
            return str(exc)
    if normalized in {"explica esse alerta do clamav", "explique esse alerta do clamav", "o que significa essa detecção do clamav"}:
        return "Envie o nome exato da detecção e o trecho do relatório do ClamAV. O Sentinel não inventa significado quando não recebeu o alerta."
    return None


def _path_from_request(text: str) -> str | None:
    quoted = re.search(r"[\"']((?:~/|/)[^\"']+)[\"']", text)
    if quoted:
        return quoted.group(1)
    match = re.search(r"(?<!\w)(~/[^\s\"']+|/[^\s\"']+)", text)
    if match:
        return match.group(1).rstrip(".,!?;:")
    return None


def investigate_service(unit: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9@_.:-]{1,160}", unit):
        raise SentinelError("Nome de unidade inválido.")
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "systemctl não está disponível; nenhum serviço foi consultado."
    properties = "Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath"
    outputs = []
    for scope in ([], ["--user"]):
        try:
            result = subprocess.run([systemctl, *scope, "show", unit, f"--property={properties}", "--no-pager"],
                                    capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip():
            outputs.append(("usuário" if scope else "sistema", result.stdout.strip()))
    if not outputs:
        return f"Não consegui consultar a unidade {unit}; ela pode não existir ou estar inacessível. Nenhuma alteração foi feita."
    text = "\n".join(f"Escopo {scope}:\n{_redact_line(output)}" for scope, output in outputs)
    report = f"Consulta somente leitura de {unit}:\n{text}\n\nOs campos mostram estado e arquivo de origem; isso não determina se a unidade é maliciosa."
    _save_report({"created_at": _now(), "kind": "service-investigation", "summary": report, "unit": unit})
    return report


def _redact_line(value: str) -> str:

    value = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[credenciais]@", value)
    value = re.sub(r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)(\s*[:=]\s*)([^\s;]+)",
                   r"\1\2[oculto]", value)
    return value
