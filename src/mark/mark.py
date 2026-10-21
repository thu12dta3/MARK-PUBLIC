from __future__ import annotations
import argparse, logging, re, signal, subprocess, sys, tempfile
from pathlib import Path
from .audio import ContinuousMicrophone, pulse_devices, pulse_environment, transcribe_temporary
from .brain import answer, normalize
from .config import load_config
from .intent import control_command, sleep_command
from .logging_setup import setup_logging
from .memory import SharedMemory
from .model_state import ModelSession
from .selfcheck import run_checks
from .tts import speak

log = logging.getLogger("mark")
WAKE_WORDS = {"mark", "marc", "marque", "marck"}


def clean_text(text: str) -> str:
    noise_labels = {
        "ruido", "musica", "music", "noise", "aplausos", "risos", "laughing",
        "laughter", "inaudivel", "inaudible", "tosse", "coughing", "blank audio",
        "silencio", "silence",
    }

    def clean_annotation(match):
        annotation = match.group(1) or match.group(2)
        label = " ".join(normalize(annotation).split())
        return " " if label in noise_labels else match.group(0)

    text = re.sub(r"\[([^\]]+)\]|\(([^)]*)\)", clean_annotation, text).strip()
    return " ".join(text.split())


def contains_wake(text: str):
    words = normalize(text).split()
    for i, word in enumerate(words):
        if word in WAKE_WORDS: return True, " ".join(words[i+1:])
    return False, ""


def language(text: str):
    words = normalize(text).split()
    pt_markers = {
        "voce", "voces", "eu", "me", "minha", "minhas", "meu", "meus", "nos", "nossa",
        "nosso", "isso", "essa", "esse", "aquela", "aquele", "qual", "quais", "quando",
        "onde", "porque", "quanto", "quanta", "quantos", "quantas", "faca", "quero", "preciso",
        "ajuda", "obrigado", "obrigada", "ola", "bom", "boa", "dia", "noite", "por", "favor",
        "uma", "umas", "uns", "um", "de", "da", "das", "do", "dos", "para", "com", "sem",
        "em", "na", "nas", "no", "a", "as", "o", "os", "e", "que", "estou", "esta", "estao",
        "sou", "somos", "sao", "tem", "vai", "vamos", "hoje", "agora", "aqui", "muito", "mas",
    }
    en_markers = {
        "what", "where", "who", "why", "how", "which", "solve", "calculate", "search", "hello",
        "hi", "please", "tell", "the", "is", "are", "was", "were", "can", "could", "would",
        "should", "your", "you", "today", "weather", "explain", "show", "find", "current", "latest",
        "need", "help", "look", "and", "with", "from", "this", "that", "they", "we", "i", "good", "morning",
        "thanks", "thank", "have", "has", "will",
    }
    pt_score = sum(word in pt_markers for word in words)
    en_score = sum(word in en_markers for word in words)
    return "en" if en_score > pt_score else "pt"


def beep(cfg=None):
    try:
        kwargs = {"env": pulse_environment(cfg)} if cfg else {}
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-f", "lavfi", "sine=frequency=950:duration=0.10"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=2, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Sinal sonoro indisponível (%s); continuando sem beep.", type(exc).__name__)


def main():
    if len(sys.argv)>1 and sys.argv[1]=="evolve":
        from .evolution.cli import main as evolution_main
        return evolution_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in {"knowledge", "dataset", "benchmark", "experiment", "tool", "learning", "training"}:
        from .cognition.cli import main as cognition_main
        return cognition_main(sys.argv[1:])
    parser = argparse.ArgumentParser(prog="MARK", description="Assistente multimodal local")
    parser.add_argument("--check", action="store_true", help="verifica configuração e dependências sem abrir o microfone")
    parser.add_argument("--audio-devices", action="store_true", help="lista dispositivos de áudio sem abrir o microfone")
    parser.add_argument("--audio-source", help="usa esta fonte de microfone nesta execução")
    parser.add_argument("--audio-sink", help="usa esta saída de áudio nesta execução")
    parser.add_argument("--serve-mobile", action="store_true", help="inicia API local para apps móveis sem abrir o microfone")
    parser.add_argument("--mobile-token", action="store_true", help="mostra a chave privada da API móvel")
    parser.add_argument("--mobile-pair", metavar="HTTPS_URL", help="abre pareamento por cinco minutos e mostra QR")
    parser.add_argument("--mobile-devices", action="store_true", help="lista aparelhos móveis")
    parser.add_argument("--mobile-approve", metavar="DEVICE_ID", help="aprova um aparelho pendente")
    parser.add_argument("--mobile-revoke", metavar="DEVICE_ID", help="revoga ou recusa um aparelho")
    parser.add_argument("--mobile-lockdown", choices=["on", "off"], help="bloqueia/libera acessos móveis")
    parser.add_argument("--mobile-port", type=int, default=8765, help="porta local da API móvel (padrão: 8765)")
    args = parser.parse_args()
    setup_logging()
    cfg = load_config()
    if args.mobile_pair or args.mobile_devices or args.mobile_approve or args.mobile_revoke or args.mobile_lockdown:
        from .device_auth import DeviceRegistry
        import json
        with_device = DeviceRegistry()
        try:
            if args.mobile_lockdown:
                with_device.set_lockdown(args.mobile_lockdown == "on")
                print("Lockdown " + ("ativado" if args.mobile_lockdown == "on" else "desativado"))
            elif args.mobile_approve:
                print("Aprovado" if with_device.update_device(args.mobile_approve, "authorized") else "Aparelho pendente não encontrado")
            elif args.mobile_revoke:
                print("Revogado" if with_device.update_device(args.mobile_revoke, "revoked") else "Aparelho autorizado não encontrado")
            elif args.mobile_devices:
                for device_id, name, status in with_device.list_devices():
                    print(f"{status:10} {name:30} {device_id}")
            else:
                url = args.mobile_pair.rstrip("/")
                from urllib.parse import urlsplit
                import socket, ssl, hashlib
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                        or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                        or parsed.port == 80):
                    parser.error("use o endereço HTTPS do Tailscale Serve (porta 80 HTTP proibida)")
                tls_ctx = ssl.create_default_context()
                tls_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
                with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=8) as tcp:
                    with tls_ctx.wrap_socket(tcp, server_hostname=parsed.hostname) as tls:
                        if tls.version() != "TLSv1.3":
                            parser.error("o servidor de pareamento precisa negociar TLS 1.3")
                        pin = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
                code = with_device.open_enrollment()
                data = json.dumps({"url": url, "code": code, "pin": pin}, separators=(",", ":"))
                print("Pareamento aberto por cinco minutos. Aprove o aparelho no PC após a solicitação.")
                print(data)
                try:
                    subprocess.run(["qrencode", "-t", "ANSIUTF8", data], check=False, timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    print("QR indisponível; use o código acima no aplicativo.")
        finally:
            with_device.close()
        return 0
    if args.mobile_token:
        from .mobile_api import mobile_token
        print(mobile_token())
        return 0
    if args.serve_mobile:
        from .mobile_api import serve_mobile
        try:
            serve_mobile(cfg, args.mobile_port)
        except (OSError, ValueError) as exc:
            log.error("A API móvel não iniciou (%s).", type(exc).__name__)
            return 1
        except KeyboardInterrupt:
            log.info("API móvel encerrada.")
        return 0
    if args.audio_devices:
        devices = pulse_devices()
        if devices["error"]:
            print(devices["error"])
            return 1
        print(f"Entrada padrão: {devices['default_source'] or '(indisponível)'}")
        print(f"Saída padrão: {devices['default_sink'] or '(indisponível)'}")
        print("\nEntradas:")
        for device in devices["sources"]:
            print(f"  {device}")
        print("\nSaídas:")
        for device in devices["sinks"]:
            print(f"  {device}")
        return 0
    if args.audio_source:
        cfg["audio"]["source"] = args.audio_source
    if args.audio_sink:
        cfg["audio"]["sink"] = args.audio_sink
    if args.check:
        return run_checks(cfg)

    cfg["ollama"]["model"] = cfg.get("models", {}).get("call", cfg["ollama"]["model"])
    cfg["ollama"]["think"] = bool(cfg.get("thinking", {}).get("call", False))
    cfg["interaction_mode"] = "call"
    memory = SharedMemory(int(cfg["memory"]["max_messages"]))
    session = ModelSession(cfg)
    tmp = Path(cfg["paths"]["tmp"]); tmp.mkdir(parents=True, exist_ok=True)
    mic = ContinuousMicrophone(cfg)
    audio_tmp = tempfile.TemporaryDirectory(prefix="mark-audio-", dir=tmp)
    audio_dir = Path(audio_tmp.name)
    log.info("MARK pronto. Diga MARK para chamar; Ctrl+C para encerrar.")
    previous_term = signal.getsignal(signal.SIGTERM)

    def stop_service(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_service)
    try:
        try:
            mic.start()
        except (OSError, RuntimeError) as exc:
            log.error("Não foi possível iniciar a escuta do MARK: %s", exc)
            return 1
        while True:
            try:
                audio = mic.wait_for_utterance()
            except (OSError, RuntimeError) as exc:
                log.error("A captura do microfone foi interrompida: %s", exc)
                return 1
            if not audio: continue
            if session.awake:
                command_wav = audio_dir / "mark-command.wav"
                try: command = transcribe_temporary(audio, command_wav, cfg["whisper"]["command_model"], cfg, sample_rate=mic.rate)
                except Exception as exc: log.error("Whisper falhou: %s", exc); continue
            else:
                wake = audio_dir / "mark-wake.wav"
                try: wake_text = transcribe_temporary(audio, wake, cfg["whisper"]["wake_model"], cfg, sample_rate=mic.rate)
                except Exception as exc: log.warning("Falha ao verificar wake word: %s", exc); continue
                woke, command = contains_wake(wake_text)
                if not woke: continue
                beep(cfg); log.info("MARK acordou; ouvindo sem repetir a palavra de ativação.")
                try:
                    session.wake()
                except Exception as exc:
                    log.error("Não foi possível carregar o Qwen (%s).", type(exc).__name__)
                    speak("Não consegui carregar o Qwen agora. Tente me chamar novamente.", "pt", cfg)
                    mic.discard_pending_audio()
                    continue
                if not command:
                    try:
                        audio = mic.wait_for_utterance(timeout=10)
                    except (OSError, RuntimeError) as exc:
                        log.error("A captura do microfone foi interrompida: %s", exc)
                        return 1
                    if not audio: log.info("Aguardando sua próxima frase."); continue
                    command_wav = audio_dir / "mark-command.wav"
                    try: command = transcribe_temporary(audio, command_wav, cfg["whisper"]["command_model"], cfg, sample_rate=mic.rate)
                    except Exception as exc: log.error("Whisper falhou: %s", exc); continue
            command = clean_text(command)
            if not command: continue
            if sleep_command(command, cfg):
                speak("Vou descansar. É só me chamar quando precisar.", language(command), cfg)
                session.sleep()
                mic.discard_pending_audio()
                log.info("MARK voltou à escuta leve.")
                continue
            action = control_command(command)
            if action == "clear_memory":
                memory.clear(); reply = "Memória recente limpa."
            elif action == "shutdown":
                speak("Até mais.", language(command), cfg); break
            else:
                log.debug("Você: %s", command)
                try: reply = answer(command, cfg, memory, origin="pc")
                except Exception as exc:


                    log.error("Falha ao responder (%s).", type(exc).__name__)
                    reply = "Não consegui concluir isso agora. Tente novamente em instantes."
                memory.add(command, reply, source="pc-voice")
            log.debug("MARK: %s", reply)
            speak(reply, language(command), cfg)
            mic.discard_pending_audio()
    except KeyboardInterrupt:
        log.info("MARK offline.")
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        try:
            mic.stop()
        finally:
            try:
                session.sleep()
            finally:
                audio_tmp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
