
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path
from .config import camera_url
from .url_security import validate_url_scheme


def ollama_status(cfg: dict, timeout: float = 3.0) -> tuple[bool, str]:

    base_url = cfg["ollama"]["url"].rstrip("/")
    request = urllib.request.Request(base_url + "/api/tags")
    validate_url_scheme(request)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec: B310
            models = json.load(response).get("models", [])
    except Exception:
        return False, "Ollama não respondeu; inicie o serviço e tente novamente."

    required = cfg["ollama"]["model"]
    available = {str(item.get("name", "")) for item in models}
    if required not in available:
        return False, f"Ollama responde, mas o modelo configurado ({required}) não está baixado."
    return True, f"Ollama e modelo {required}: OK."


def camera_status(cfg: dict) -> tuple[bool, str]:

    from .vision_source import camera_input
    try:
        source, _ = camera_input(cfg)
    except (TypeError, ValueError, RuntimeError) as exc:
        return False, f"Configuração da câmera inválida: {exc}"
    if source == 'usb':
        return True, "Câmera USB selecionada; conexão física não testada."
    return True, "Endereço RTSP/RTSPS da câmera válido; conexão física não testada."


def run_checks(cfg: dict) -> int:

    issues: list[str] = []
    for command in ("ffmpeg", "ffplay", cfg["whisper"]["binary"]):
        if shutil.which(command):
            print(f"OK: {command}")
        else:
            issues.append(f"Falta o comando {command} no PATH.")

    for key in ("wake_model", "command_model"):
        model = Path(cfg["whisper"][key])
        if model.is_file():
            print(f"OK: modelo Whisper {key}")
        else:
            issues.append(f"Falta o modelo Whisper: {model}")

    piper_python = Path(cfg["tts"]["python"])
    voices = (Path(cfg["tts"]["voice_pt"]), Path(cfg["tts"]["voice_en"]))
    if not piper_python.is_file():
        issues.append(f"Falta o Python do Piper: {piper_python}")
    else:
        try:
            subprocess.run(
                [str(piper_python), "-c", "import piper"],
                check=True,
                capture_output=True,
                timeout=5,
            )
            print("OK: módulo Piper")
        except (subprocess.SubprocessError, OSError):
            issues.append("O ambiente Python configurado não consegue importar Piper.")
    for voice in voices:
        if voice.is_file():
            print(f"OK: voz {voice.name}")
        else:
            issues.append(f"Falta a voz Piper: {voice}")

    camera_ready, camera_message = camera_status(cfg)
    if camera_ready:
        print(f"OK: {camera_message}")
    else:
        issues.append(camera_message)

    online, message = ollama_status(cfg)
    if online:
        print(f"OK: {message}")
    else:
        issues.append(message)

    for issue in issues:
        print(f"FALTA: {issue}")
    if issues:
        print("MARK ainda não está totalmente pronto para responder a todos os pedidos.")
        return 1
    print("MARK pronto para uso. Câmera e microfone devem ser testados separadamente.")
    return 0
