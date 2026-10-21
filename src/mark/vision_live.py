
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from .vision_source import camera_input, capture_error
from .vision_quality import FrameQuality, MAX_FRAME_BYTES, inspect_frame


class JpegStream:

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buffer.extend(chunk)
        frames = []
        while True:
            start = self.buffer.find(b'\xff\xd8')
            if start < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b'\xff\xd9', 2)
            if end < 0:
                if len(self.buffer) > MAX_FRAME_BYTES:
                    self.buffer.clear()
                    raise ValueError('Quadro da câmera excedeu o limite de 8 MB.')
                break
            if end + 2 > MAX_FRAME_BYTES:
                self.buffer.clear()
                raise ValueError('Quadro da câmera excedeu o limite de 8 MB.')
            frames.append(bytes(self.buffer[:end+2]))
            del self.buffer[:end+2]
        return frames


@dataclass(frozen=True)
class LiveFrame:
    jpeg: bytes
    captured_at: float
    sequence: int
    quality: FrameQuality


class LatestCamera:

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._latest = None
        self._error = None
        self._process = None
        self._reader = None
        self._stderr_reader = None
        self._stderr = bytearray()
        self._sequence = 0

    @property
    def error(self):
        with self._lock:
            return self._error

    def start(self):
        if self._process is not None:
            raise RuntimeError('A captura já está iniciada.')
        source, inputs = camera_input(self.cfg)
        command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', *inputs,
                   '-an', '-sn', '-dn', '-vf', 'fps=10', '-threads', '1', '-q:v', '2',
                   '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1']
        self._stop.clear()
        self._ready.clear()
        with self._lock:
            self._latest = None
            self._error = None
        self._stderr.clear()
        try:
            self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            raise RuntimeError('Não consegui abrir a captura. Confira o FFmpeg e as permissões da câmera.') from None
        self._reader = threading.Thread(target=self._read, args=(source,), daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_errors, daemon=True)
        self._stderr_reader.start()
        self._reader.start()
        return self

    def _read_errors(self):
        while not self._stop.is_set():
            chunk = self._process.stderr.read1(1024)
            if not chunk: break

            self._stderr.extend(chunk)
            del self._stderr[:-4096]

    def _read(self, source):
        parser = JpegStream()
        started = time.monotonic()
        try:
            while not self._stop.is_set():
                chunk = self._process.stdout.read1(65536)
                if not chunk: break
                for jpeg in parser.feed(chunk):
                    if self._stop.is_set(): return

                    if time.monotonic() - started < .4: continue
                    quality = inspect_frame(jpeg)
                    with self._lock:
                        if self._stop.is_set(): return
                        self._sequence += 1
                        self._latest = LiveFrame(jpeg, time.monotonic(), self._sequence, quality)
                    self._ready.set()
            if not self._stop.is_set():
                self._stderr_reader.join(timeout=.3)
                with self._lock:
                    self._error = capture_error(self._stderr.decode('utf-8','replace'),
                                                self._process.poll() or 1, source)
                    self._latest = None
                self._ready.set()
        except (OSError, ValueError):
            if not self._stop.is_set():
                with self._lock:
                    self._error = 'O fluxo da câmera foi interrompido ou contém imagens inválidas. Reconecte e inicie novamente.'
                    self._latest = None
                self._ready.set()

    def latest(self, max_age: float = 2.0) -> LiveFrame:
        with self._lock:
            if self._stop.is_set(): raise RuntimeError('Sem quadro recente: câmera desligada.')
            if self._error: raise RuntimeError(self._error)
            frame = self._latest
        if frame is None or time.monotonic() - frame.captured_at > max_age:
            raise RuntimeError('Sem quadro recente da câmera. Confira a conexão e tente novamente.')
        return frame

    def wait_frame(self, timeout: float = 10) -> LiveFrame:
        if not self._ready.wait(timeout):
            raise RuntimeError('A câmera não forneceu um quadro recente dentro do tempo limite.')
        return self.latest()

    def stop(self):

        self._stop.set()
        self._ready.set()
        with self._lock:
            self._latest = None
        process = self._process
        if process is not None and process.poll() is None:
            try: process.terminate()
            except ProcessLookupError: pass

    def close(self):
        self.stop()
        process = self._process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            for thread in (self._reader, self._stderr_reader):
                if thread is not None: thread.join(timeout=1)
            for pipe in (process.stdout, process.stderr):
                if pipe: pipe.close()
        self._process = None
        with self._lock:
            self._latest = None
            self._error = None
        self._stderr.clear()
        self._ready.clear()

    def __enter__(self): return self.start()
    def __exit__(self, *_): self.close()
