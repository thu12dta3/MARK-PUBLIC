import logging, os, subprocess, tempfile
from pathlib import Path
from .audio import pulse_environment

log = logging.getLogger("mark.tts")


def speak(text: str, language: str, cfg: dict) -> None:
    voice = cfg["tts"]["voice_en"] if language == "en" else cfg["tts"]["voice_pt"]
    tmp_dir = Path(cfg["paths"]["tmp"])
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, output_name = tempfile.mkstemp(prefix="mark-answer-", suffix=".wav", dir=tmp_dir)
    output = Path(output_name)

    os.close(fd)
    try:
        subprocess.run([cfg["tts"]["python"], "-m", "piper", "-m", voice, "-f", str(output), "--", text],
                       check=True, capture_output=True, text=True,
                       timeout=int(cfg["tts"].get("synth_timeout", 60)))
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(output)],
                       check=True, timeout=int(cfg["tts"].get("playback_timeout", 180)),
                       env=pulse_environment(cfg))
    except Exception as exc:

        log.error("Piper falhou (%s); tentando a voz alternativa.", type(exc).__name__)
        try:
            subprocess.run(["espeak-ng", "-v", "en-us" if language == "en" else "pt-br", text],
                           check=False, timeout=int(cfg["tts"].get("fallback_timeout", 60)),
                           env=pulse_environment(cfg))
        except (OSError, subprocess.SubprocessError) as fallback_exc:
            log.warning("Piper e a voz alternativa estão indisponíveis (%s).", type(fallback_exc).__name__)
    finally:
        output.unlink(missing_ok=True)


def speech_wav(text: str, language: str, cfg: dict) -> bytes:

    voice = cfg["tts"]["voice_en"] if language == "en" else cfg["tts"]["voice_pt"]
    tmp_dir = Path(cfg["paths"]["tmp"])
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="mark-mobile-voice-", suffix=".wav", dir=tmp_dir)
    output = Path(name)
    os.close(fd)
    try:
        try:
            subprocess.run([cfg["tts"]["python"], "-m", "piper", "-m", voice,
                            "-f", str(output), "--", text], check=True,
                           capture_output=True, timeout=int(cfg["tts"].get("synth_timeout", 60)))
            data = output.read_bytes()
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Piper móvel indisponível (%s); usando voz alternativa.", type(exc).__name__)
            result = subprocess.run(["espeak-ng", "-v", "en-us" if language == "en" else "pt-br",
                                     "--stdout", text], check=True, capture_output=True,
                                    timeout=int(cfg["tts"].get("fallback_timeout", 60)))
            data = result.stdout
        if not data.startswith(b"RIFF"):
            raise RuntimeError("A síntese de voz não retornou WAV válido.")
        return data
    finally:
        output.unlink(missing_ok=True)
