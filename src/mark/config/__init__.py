from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

HOME = Path.home()
ROOT = Path(__file__).resolve().parents[3]
CONFIG_FILE = Path(os.environ.get("MARK_CONFIG", HOME / ".config/mark/config.json"))

HERETIC_GENERAL_MODEL = "haervwe/qwen3-vl-4b-heretic:latest"
HERETIC_ENGINEER_MODEL = "R4C3R/qwen2.5-coder-7b-instruct-heretic:q4_k_m"

DEFAULTS = {
    "ollama": {"url": "http://127.0.0.1:11434", "model": HERETIC_GENERAL_MODEL, "timeout": 180,
               "num_predict": 2048, "num_ctx": 8192, "temperature": 0.7,
               "top_p": 0.8, "top_k": 20, "repeat_penalty": 1.0, "keep_alive": 0},
    "models": {"chat": HERETIC_GENERAL_MODEL, "call": HERETIC_GENERAL_MODEL,
               "engineer": HERETIC_ENGINEER_MODEL},
    "thinking": {"chat": False, "call": False},
    "camera": {"source": "auto", "device": "", "size": "1280x720", "fps": 10, "url": "", "scheme": "rtsps", "tls_verify": False, "timeout": 20},
    "audio": {
        "sample_rate": 16000, "frame_ms": 30, "threshold": 500,
        "min_speech_seconds": 0.18, "end_silence_seconds": 0.75,
        "max_utterance_seconds": 12, "calibration_seconds": 2,
        "source": "default", "sink": "",
    },
    "whisper": {
        "binary": "whisper-cli",
        "timeout": 90,
        "wake_model": str(HOME / ".local/share/mark/ggml-base.bin"),
        "command_model": str(HOME / ".local/share/mark/ggml-small.bin"),
    },
    "tts": {
        "python": str(HOME / ".local/share/mark/piper-env/bin/python"),
        "voice_pt": str(HOME / ".local/share/mark/voices/pt_BR-faber-medium.onnx"),
        "voice_en": str(HOME / ".local/share/mark/voices/en_US-lessac-medium.onnx"),
        "synth_timeout": 60, "playback_timeout": 180, "fallback_timeout": 60,
    },
    "cognition": {"enabled": True, "model_provider": "ollama", "telemetry": False,
                  "knowledge_db": str(HOME / ".local/state/mark/cognition/knowledge.sqlite3")},

    "free_ai": {"enabled": False, "mode": "explicit", "timeout": 15,
                "max_attempts": 2, "daily_per_endpoint": 10},

    "honcho": {"enabled": False, "share_conversations": False,
               "base_url": "https://api.honcho.dev", "workspace_id": "mark",
               "owner_peer_id": "owner", "assistant_peer_id": "mark",
               "session_id": "shared", "timeout_seconds": 4},

    "sentinel": {"share_hash_with_virustotal": False, "virustotal_api_key": ""},
    "web": {"enabled": True, "timeout": 5}, "plugins": {}, "memory": {"max_messages": 6},
    "paths": {"tmp": str(ROOT / "tmp"), "logs": str(ROOT / "logs")},
}


def load_config() -> dict:

    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_FILE.is_file():
        with CONFIG_FILE.open(encoding="utf-8") as f:
            local = json.load(f)
        for section, values in local.items():
            if isinstance(values, dict) and section in cfg:
                cfg[section].update(values)
    if os.getenv("MARK_KNOWLEDGE_DB"):
        cfg["cognition"]["knowledge_db"] = os.environ["MARK_KNOWLEDGE_DB"]
    if os.getenv("MARK_CAMERA_URL"):
        cfg["camera"]["url"] = os.environ["MARK_CAMERA_URL"]
    if os.getenv("MARK_OLLAMA_URL"):
        cfg["ollama"]["url"] = os.environ["MARK_OLLAMA_URL"]
    if os.getenv("MARK_WEB"):
        cfg["web"]["enabled"] = os.environ["MARK_WEB"].lower() not in {"0", "false", "no"}
    if os.getenv("MARK_AUDIO_SOURCE"):
        cfg["audio"]["source"] = os.environ["MARK_AUDIO_SOURCE"]
    if os.getenv("MARK_AUDIO_SINK"):
        cfg["audio"]["sink"] = os.environ["MARK_AUDIO_SINK"]
    if os.getenv("MARK_VT_API_KEY"):
        cfg["sentinel"]["virustotal_api_key"] = os.environ["MARK_VT_API_KEY"]
    validate_heretic_profiles(cfg)
    return cfg


def validate_heretic_profiles(cfg: dict) -> None:

    models = cfg.get("models")
    fallback = cfg.get("ollama", {}).get("model")
    if not isinstance(models, dict) or not models:
        raise ValueError("O MARK precisa de perfis locais Heretic configurados.")
    names = [fallback, *models.values()]
    if any(not isinstance(name, str) or "heretic" not in name.casefold() for name in names):
        raise ValueError("Todos os perfis locais do MARK devem usar modelos Heretic.")


def camera_url(cfg: dict) -> str:

    cam = cfg["camera"]
    if cam.get("url"):
        url = str(cam["url"])
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise ValueError("A URL da câmera tem uma porta inválida; use uma porta entre 1 e 65535.") from None
        if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
            raise ValueError("A URL da câmera deve usar RTSP/RTSPS e informar um host válido.")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("A porta RTSP/RTSPS da câmera deve ser um número entre 1 e 65535.")
        return url
    host = cam.get("host", "")
    if not host:
        return ""
    scheme = str(cam.get("scheme", "rtsps")).lower()
    if scheme not in {"rtsp", "rtsps"}:
        raise ValueError("O protocolo da câmera deve ser RTSP ou RTSPS.")
    user = quote(str(cam.get("username", "")), safe="")
    password = quote(str(cam.get("password", "")), safe="")
    auth = f"{user}:{password}@" if user or password else ""
    default_port = 554 if scheme == "rtsp" else 8554
    try:
        port = int(cam.get("port", default_port))
    except (TypeError, ValueError):
        raise ValueError("A porta RTSP/RTSPS da câmera deve ser um número entre 1 e 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("A porta RTSP/RTSPS da câmera deve ser um número entre 1 e 65535.")
    path = str(cam.get("path", "/live"))
    if not path.startswith("/"):
        path = "/" + path
    return f"{scheme}://{auth}{host}:{port}{path}"


from .llm_config import (
    AgentProfileConfig,
    LLMConfig,
    LLMKeyVault,
    ProviderProfile,
    get_llm_config,
)

__all__ = [
    "AgentProfileConfig",
    "CONFIG_FILE",
    "DEFAULTS",
    "HERETIC_ENGINEER_MODEL",
    "HERETIC_GENERAL_MODEL",
    "HOME",
    "LLMConfig",
    "LLMKeyVault",
    "ProviderProfile",
    "ROOT",
    "camera_url",
    "get_llm_config",
    "load_config",
    "validate_heretic_profiles",
]
