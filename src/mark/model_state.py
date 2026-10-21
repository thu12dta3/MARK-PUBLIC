
from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
import hashlib
import urllib.request
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("mark.model_state")
STATE_DIR = Path.home() / ".local/state/mark/model-sessions"


def _model_command(cfg: dict, keep_alive: int) -> None:
    from .cognition.providers import get_provider
    get_provider(cfg).set_keep_alive(keep_alive)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def _locked_sessions(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(directory / ".lock", flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        for marker in directory.glob("session-*-*"):
            try:
                pid = int(marker.name.split("-")[1])
            except (IndexError, ValueError):
                continue
            if not _pid_alive(pid):
                marker.unlink(missing_ok=True)
        yield directory
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class ModelSession:


    def __init__(self, cfg: dict, directory: Path = STATE_DIR):
        self.cfg = cfg
        self.directory = directory
        self.marker: Path | None = None

    @property
    def model(self) -> str:
        return str(self.cfg["ollama"]["model"])

    @property
    def model_key(self) -> str:
        return hashlib.sha256(self.model.encode("utf-8")).hexdigest()[:12]

    @property
    def awake(self) -> bool:
        return self.marker is not None

    def wake(self) -> None:
        if self.awake:
            return
        with _locked_sessions(self.directory):
            marker = self.directory / f"session-{os.getpid()}-{self.model_key}-{secrets.token_hex(8)}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            os.close(os.open(marker, flags, 0o600))
            try:
                _model_command(self.cfg, -1)
            except Exception:
                marker.unlink(missing_ok=True)
                raise
            self.marker = marker
            self.cfg["ollama"]["keep_alive"] = -1

    def sleep(self) -> None:
        with _locked_sessions(self.directory):
            if self.marker is not None:
                self.marker.unlink(missing_ok=True)
            self.marker = None
            self.cfg["ollama"]["keep_alive"] = 0


            active = list(self.directory.glob(f"session-*-{self.model_key}-*"))
            legacy = list(self.directory.glob("session-*-*"))
            legacy = [item for item in legacy if len(item.name.split("-")) == 3]
            if not active and not legacy:
                try:
                    _model_command(self.cfg, 0)
                except Exception as exc:
                    log.warning("Não foi possível descarregar o Qwen (%s).", type(exc).__name__)
