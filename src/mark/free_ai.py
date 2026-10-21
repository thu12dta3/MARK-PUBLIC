
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import urllib.error
import urllib.request

_STATE_PATH = Path.home() / ".local/state/mark/free-models.sqlite3"
_LOCK = threading.RLock()
_UNTIL: dict[str, float] = {}

@dataclass(frozen=True)
class Endpoint:
    name: str
    model: str
    url: str
    key_env: str
    label: str
    category: str
    daily_cap: int
    account_env: str
    account_id_env: str = ""



ENDPOINTS = (
    Endpoint("groq-reason", "openai/gpt-oss-120b",
             "https://api.groq.com/openai/v1/chat/completions",
             "GROQ_API_KEY", "Groq / GPT-OSS 120B", "reason", 30, "MARK_GROQ_FREE_ACCOUNT"),
    Endpoint("groq-code", "qwen/qwen3.8-27b",
             "https://api.groq.com/openai/v1/chat/completions",
             "GROQ_API_KEY", "Groq / Qwen3.8 27B", "code", 30, "MARK_GROQ_FREE_ACCOUNT"),
    Endpoint("groq-fast", "openai/gpt-oss-20b",
             "https://api.groq.com/openai/v1/chat/completions",
             "GROQ_API_KEY", "Groq / GPT-OSS 20B", "general", 30, "MARK_GROQ_FREE_ACCOUNT"),
    Endpoint("openrouter-free", "openrouter/free",
             "https://openrouter.ai/api/v1/chat/completions",
             "OPENROUTER_API_KEY", "OpenRouter / Free Router", "reason", 15, "MARK_OPENROUTER_FREE_ACCOUNT"),
    Endpoint("cerebras-free", "gpt-oss-120b",
             "https://api.cerebras.ai/v1/chat/completions",
             "CEREBRAS_API_KEY", "Cerebras / GPT-OSS 120B", "reason", 15, "MARK_CEREBRAS_FREE_ACCOUNT"),
    Endpoint("cloudflare-free", "@cf/meta/llama-3.1-8b-instruct",
             "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions",
             "CLOUDFLARE_API_TOKEN", "Cloudflare / Llama 8B", "general", 10,
             "MARK_CLOUDFLARE_FREE_ACCOUNT", "CLOUDFLARE_ACCOUNT_ID"),
    Endpoint("gemini-explicit", "",
             "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
             "GEMINI_API_KEY", "Google Gemini", "general", 10, "MARK_GEMINI_FREE_ACCOUNT"),
)
BY_NAME = {e.name: e for e in ENDPOINTS}
SECRET = re.compile(
    r"(?:-----BEGIN .*?PRIVATE KEY-----|"
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,})\b|"
    r"\b(?:password|senha|token|secret|api[_ -]?key|credential|bearer|"
    r"cpf|rg|imei|serial|private[_ -]?key)\s*[:= ]\s*\S+|"
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
    r"\bhttps?://\S+|(?:/home/|/mnt/|/etc/|[A-Za-z]:\\Users\\))", re.I,
)
PERSONAL = re.compile(
    r"\b(?:meu|minha|meus|minhas|nossos|nossas|my|mine|our|ours|"
    r"conversa|hist[oó]rico|mem[oó]ria|agenda|lembrete|"
    r"localiza[cç][aã]o|endere[cç]o|telefone|diagn[oó]stico|"
    r"reposit[oó]rio|documentos privados|private files)\b", re.I,
)
PUBLIC_START = re.compile(
    r"^(?:explique|compare|qual|quais|como funciona|por que|porque|"
    r"o que [eé]|o que significa|explain|compare|what|why|how does|"
    r"how do|write a|escreva uma|descreva|defina)\b", re.I,
)
CODE = re.compile(r"\b(python|kotlin|javascript|java|rust|linux|algoritmo|"
                  r"programa[cç][aã]o|code|coding|debug|function|fun[cç][aã]o)\b", re.I)
REASON = re.compile(r"\b(compare|diferen[cç]a|analis[ea]|racioc[ií]nio|"
                    r"problema|complex|reason|why|por que|como funciona)\b", re.I)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

OPENER = urllib.request.build_opener(NoRedirect())

def public_question(text: str) -> bool:

    if not isinstance(text, str) or not 12 <= len(text) <= 2000:
        return False
    if SECRET.search(text) or PERSONAL.search(text):
        return False
    if "\x00" in text or (chr(96) * 3) in text or "\n" in text:
        return False
    return bool(PUBLIC_START.search(text.strip()))

def task_kind(text: str) -> str:
    if CODE.search(text): return "code"
    if REASON.search(text): return "reason"
    return "general"

def _ready(endpoint: Endpoint) -> bool:
    provider = {"GEMINI_API_KEY": "gemini", "GROQ_API_KEY": "groq",
                "OPENROUTER_API_KEY": "openrouter"}.get(endpoint.key_env)
    if provider:
        from .provider_settings import get_api_key, provider_enabled, selected_model
        secret = get_api_key(provider)
        locally_enabled = provider_enabled(provider)
        selected = selected_model(provider)
    else:
        secret = os.environ.get(endpoint.key_env, "")
        locally_enabled = False
        selected = None
    if not secret:
        return False
    if os.environ.get(endpoint.account_env) != "1" and not locally_enabled:
        return False
    if endpoint.key_env == "GEMINI_API_KEY" and not selected:
        return False
    if endpoint.account_id_env:
        account = os.environ.get(endpoint.account_id_env, "")
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", account):
            return False
    return UNTIL(endpoint.name) <= time.monotonic()

def UNTIL(name: str) -> float:
    return _UNTIL.get(name, 0)

def available() -> list[str]:
    return [ep.name for ep in ENDPOINTS if _ready(ep)]

def route_candidates(text: str) -> list[Endpoint]:
    choices = {
        "code": ["groq-code", "groq-reason", "cerebras-free",
                 "openrouter-free", "groq-fast", "cloudflare-free"],
        "reason": ["groq-reason", "gemini-explicit", "cerebras-free", "openrouter-free",
                   "groq-code", "groq-fast", "cloudflare-free"],
        "general": ["groq-fast", "gemini-explicit", "cloudflare-free", "groq-reason",
                    "openrouter-free", "cerebras-free", "groq-code"],
    }
    return [ep for name in choices[task_kind(text)]
            if _ready(ep := BY_NAME[name])]

def _claim_daily_slot(name: str, cap: int, cfg_cap: int) -> bool:

    limit = min(cap, cfg_cap)
    with _LOCK:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _STATE_PATH.parent.is_symlink():
            return False
        if _STATE_PATH.exists() and (_STATE_PATH.is_symlink()
                                     or _STATE_PATH.stat().st_mode & 0o077):
            return False
        db = sqlite3.connect(_STATE_PATH, timeout=5)
        try:
            os.chmod(_STATE_PATH, stat.S_IRUSR | stat.S_IWUSR)
            db.execute("CREATE TABLE IF NOT EXISTS daily "
                       "(day TEXT NOT NULL, endpoint TEXT NOT NULL, "
                       "used INTEGER NOT NULL, PRIMARY KEY(day,endpoint))")
            day = datetime.now(timezone.utc).date().isoformat()
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT used FROM daily WHERE day=? AND endpoint=?",
                             (day, name)).fetchone()
            if row and row[0] >= limit:
                db.rollback()
                return False
            db.execute("INSERT INTO daily VALUES(?,?,1) ON CONFLICT(day,endpoint) "
                       "DO UPDATE SET used=used+1", (day, name))
            db.execute("DELETE FROM daily WHERE day < date('now', '-14 day')")
            db.commit()
            return True
        finally:
            db.close()

def _response_text(result: object) -> str:
    if not isinstance(result, dict): return ""
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str): return content.strip()[:12000]
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content
                         if isinstance(x, dict) and isinstance(x.get("text"), str)).strip()[:12000]
    return ""

def _call(ep: Endpoint, question: str, timeout: int) -> str:
    url = ep.url
    if ep.account_id_env:
        url = url.replace("{account_id}", os.environ[ep.account_id_env])
    provider = {"GEMINI_API_KEY": "gemini", "GROQ_API_KEY": "groq",
                "OPENROUTER_API_KEY": "openrouter"}.get(ep.key_env)
    if provider:
        from .provider_settings import get_api_key, selected_model
        api_key = get_api_key(provider)
        model = selected_model(provider) or ep.model
    else:
        api_key = os.environ[ep.key_env]
        model = ep.model
    if not api_key or not model:
        raise RuntimeError("Provider not configured")
    payload = {
        "model": model, "stream": False, "temperature": 0.2,
        "max_tokens": 640,
        "messages": [
            {"role": "system", "content": "Answer this standalone public question accurately. "
             "You have no access to private memory, files or devices. "
             "Do not call tools; state uncertainty when needed."},
            {"role": "user", "content": question},
        ],
    }
    req = urllib.request.Request(
        url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        {"Authorization": "Bearer " + api_key,
         "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with OPENER.open(req, timeout=timeout) as resp:
        if resp.status != 200: raise RuntimeError("Provider response not OK")
        body = resp.read(512_001)
    if len(body) > 512_000: raise ValueError("Provider response too large")
    return _response_text(json.loads(body.decode("utf-8")))

def try_public_answer(text: str, cfg: dict, *, origin: str = "",
                      images: object = None, attachment_context: str = "") -> str | None:





    settings = cfg.get("free_ai", {})
    if not isinstance(settings, dict):
        return None
    if settings.get("enabled") is not True:
        try:
            from .provider_settings import provider_enabled
            if not any(provider_enabled(name) for name in ("gemini", "groq", "openrouter")):
                return None
        except Exception:
            return None
    if origin != "pc" or images or attachment_context: return None
    mode = settings.get("mode", "explicit")
    explicit = isinstance(text, str) and text.lower().startswith("@free ")
    if mode == "explicit" and not explicit: return None
    if mode not in {"explicit", "auto_public"}: return None
    question = text[6:].strip() if explicit else text
    if not public_question(question): return None

    if not explicit and (len(question) < 55 or task_kind(question) == "general"):
        return None
    try:
        timeout = min(30, max(3, int(settings.get("timeout", 15))))
        attempts = min(3, max(1, int(settings.get("max_attempts", 2))))
        cap = min(50, max(1, int(settings.get("daily_per_endpoint", 10))))
    except (TypeError, ValueError): return None
    for ep in route_candidates(question)[:attempts]:
        try:
            if not _claim_daily_slot(ep.name, ep.daily_cap, cap): continue
        except (OSError, sqlite3.Error, ValueError):

            return None
        try:
            reply = _call(ep, question, timeout)
            if reply:
                return f"[Consulta pública via {ep.label}; sem memória pessoal]\n{reply}"
        except urllib.error.HTTPError as exc:

            _UNTIL[ep.name] = time.monotonic() + (86400 if exc.code in (401, 402, 403) else 3600)
        except (OSError, ValueError, RuntimeError):
            _UNTIL[ep.name] = time.monotonic() + 120
    return None
