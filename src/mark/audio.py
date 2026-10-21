from __future__ import annotations
import logging, math, os, select, subprocess, time, wave
from array import array
from pathlib import Path

log = logging.getLogger("mark.audio")


def pulse_devices() -> dict:

    result = {"sources": [], "sinks": [], "default_source": "", "default_sink": "", "error": ""}
    try:
        for key, noun in (("sources", "sources"), ("sinks", "sinks")):
            output = subprocess.run(["pactl", "list", "short", noun], check=True,
                                    capture_output=True, text=True, timeout=3).stdout
            for line in output.splitlines():
                fields = line.split("\t")
                if len(fields) >= 2 and fields[1].strip():
                    result[key].append(fields[1].strip())
        for key, noun in (("default_source", "source"), ("default_sink", "sink")):
            output = subprocess.run(["pactl", "get-default-" + noun], check=True,
                                    capture_output=True, text=True, timeout=3).stdout
            result[key] = output.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = f"Não foi possível consultar o áudio do sistema ({type(exc).__name__})."
    return result


def pulse_environment(cfg: dict) -> dict:

    env = os.environ.copy()
    sink = str(cfg.get("audio", {}).get("sink", "")).strip()
    if sink and sink.lower() != "default":
        env["PULSE_SINK"] = sink
    else:
        env.pop("PULSE_SINK", None)
    return env


class ContinuousMicrophone:

    def __init__(self, cfg: dict):
        self.cfg = cfg["audio"]
        self.process = None
        self.rate = int(self.cfg["sample_rate"])
        self.frame_ms = int(self.cfg["frame_ms"])
        self.frame_samples = self.rate * self.frame_ms // 1000
        self.frame_bytes = self.frame_samples * 2
        self.threshold = int(self.cfg["threshold"])
        self.source = str(self.cfg.get("source", "default")).strip() or "default"
        self._pending_audio = bytearray()
        self._stream_ended = False

    def start(self) -> None:
        self._stream_ended = False
        source = self.source
        if source == "default":


            try:
                chosen = subprocess.run(["pactl", "get-default-source"], check=True,
                                        capture_output=True, text=True, timeout=3).stdout.strip()
                if chosen:
                    source = chosen
            except (OSError, subprocess.SubprocessError):
                pass
        self.process = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "pulse", "-i", source,
             "-ac", "1", "-ar", str(self.rate), "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        try:
            self._calibrate()
        except BaseException:
            self.stop()
            raise

    def _calibrate(self) -> None:
        time.sleep(.3)
        calibration_seconds = max(0.0, float(self.cfg["calibration_seconds"]))
        calibration_frames = int(calibration_seconds * 1000 / self.frame_ms)
        calibration_deadline = time.monotonic() + calibration_seconds
        values = []
        for _ in range(calibration_frames):
            remaining = calibration_deadline - time.monotonic()
            if remaining <= 0:
                break
            frame = self.read_frame(timeout=remaining)
            if frame:
                values.append(self.rms(frame))
            elif self._stream_ended:
                break
        if self._stream_ended or self.process.poll() is not None:
            raise RuntimeError("ffmpeg encerrou a captura do microfone; confira a fonte PulseAudio padrão e o acesso ao dispositivo")
        if calibration_seconds > 0 and not values:
            raise RuntimeError("O microfone não enviou áudio durante a calibração; confira a fonte PulseAudio padrão e a conexão do dispositivo.")
        if values:
            values.sort()
            self.threshold = max(self.threshold, int(values[len(values)//2] * 2.7))
        log.info("Microfone ativo (limiar %d)", self.threshold)

    def stop(self) -> None:
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process.stdout:
            self.process.stdout.close()
        self.process = None
        self._pending_audio.clear()
        self._stream_ended = False

    def discard_pending_audio(self, quiet_period: float = 0.12) -> int:

        discarded = len(self._pending_audio)
        self._pending_audio.clear()
        if not self.process or not self.process.stdout:
            return discarded

        stream = self.process.stdout
        deadline = time.monotonic() + max(0.0, quiet_period)
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                readable, _, _ = select.select([stream], [], [], min(remaining, 0.02))
                if not readable:
                    if time.monotonic() >= deadline:
                        break
                    continue
                chunk = stream.read(65536)
            except (OSError, ValueError) as exc:
                log.debug("Não foi possível esvaziar o buffer do microfone: %s", exc)
                self._stream_ended = True
                break
            if not chunk:
                self._stream_ended = True
                break
            discarded += len(chunk)
        if discarded:
            log.debug("Áudio antigo descartado após a resposta (%d bytes)", discarded)
        return discarded

    def read_frame(self, timeout: float | None = None):

        if not self.process or not self.process.stdout:
            return None

        stream = self.process.stdout
        deadline = time.monotonic() + max(0.0, timeout) if timeout is not None else None
        while len(self._pending_audio) < self.frame_bytes:
            if deadline is not None:
                remaining = max(0.0, deadline - time.monotonic())
                readable, _, _ = select.select([stream], [], [], remaining)
                if not readable:
                    return None
            chunk = stream.read(self.frame_bytes - len(self._pending_audio))
            if not chunk:

                self._pending_audio.clear()
                self._stream_ended = True
                return None
            self._pending_audio.extend(chunk)

        frame = bytes(self._pending_audio[:self.frame_bytes])
        del self._pending_audio[:self.frame_bytes]
        return frame

    @staticmethod
    def rms(data: bytes) -> float:
        if not data: return 0
        samples = array("h"); samples.frombytes(data)
        return math.sqrt(sum(x*x for x in samples) / len(samples)) if samples else 0

    def wait_for_utterance(self, timeout=None):
        start = time.monotonic(); pre = []; speech = []; started = False
        voiced = silent = 0
        need_voiced = max(2, int(float(self.cfg["min_speech_seconds"]) * 1000 / self.frame_ms))
        need_silence = int(float(self.cfg["end_silence_seconds"]) * 1000 / self.frame_ms)
        maximum = int(float(self.cfg["max_utterance_seconds"]) * 1000 / self.frame_ms)
        while True:
            remaining = None if timeout is None else timeout - (time.monotonic() - start)
            if remaining is not None and remaining <= 0: return None
            data = self.read_frame(timeout=remaining)
            if not data:
                if self._stream_ended:
                    raise RuntimeError("A captura do microfone terminou; confira se o dispositivo continua conectado.")
                return None
            voice = self.rms(data) >= self.threshold
            if not started:
                pre.append(data); pre = pre[-8:]
                voiced = voiced + 1 if voice else 0
                if voiced >= need_voiced:
                    started = True; speech.extend(pre); pre.clear()
            else:
                speech.append(data); silent = 0 if voice else silent + 1
                if silent >= need_silence or len(speech) >= maximum: break
        return b"".join(speech)


def save_wav(path: str | Path, pcm: bytes, sample_rate: int = 16000) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        with wave.open(stream, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(sample_rate); wav.writeframes(pcm)


def transcribe(path: str | Path, model: str, cfg: dict, language="auto") -> str:
    result = subprocess.run([cfg["whisper"]["binary"], "-m", model, "-f", str(path), "-l", language, "-np", "-nt"],
                            check=True, capture_output=True, text=True,
                            timeout=int(cfg["whisper"].get("timeout", 90)))
    return result.stdout.strip()


def transcribe_temporary(pcm: bytes, path: str | Path, model: str, cfg: dict,
                         language="auto", sample_rate: int = 16000) -> str:

    wav_path = Path(path)
    try:
        save_wav(wav_path, pcm, sample_rate)
        return transcribe(wav_path, model, cfg, language)
    finally:
        wav_path.unlink(missing_ok=True)
