"""Filtro Anti-Replay e Janela de Sequências do MARK Link."""

from __future__ import annotations

import time
from typing import Final

CLOCK_SKEW_SECONDS: Final[int] = 60
MAX_SEEN_NONCES: Final[int] = 256


class ReplayFilter:
    """Filtro de proteção contra replay com controle de nonce e janela deslizante."""

    def __init__(self, window_size: int = 64) -> None:
        self.window_size = window_size
        self.seen_nonces: set[str] = set()
        self.highest_seq: int = 0
        self.sliding_window: int = 0

    @staticmethod
    def check_timestamp(timestamp_secs: int) -> bool:
        now = int(time.time())
        if timestamp_secs > now + CLOCK_SKEW_SECONDS:
            return False
        if now > timestamp_secs and (now - timestamp_secs) > CLOCK_SKEW_SECONDS:
            return False
        return True

    def register_nonce(self, nonce: str) -> bool:
        if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
            return False
        if nonce in self.seen_nonces:
            return False
        if len(self.seen_nonces) >= MAX_SEEN_NONCES:
            self.seen_nonces.clear()
        self.seen_nonces.add(nonce)
        return True

    def check_sequence(self, seq: int) -> bool:
        """Verifica se o número de sequência é válido e não é replay, sem comitar."""
        if seq <= 0:
            return False
        if seq > self.highest_seq:
            return True
        diff = self.highest_seq - seq
        if diff >= self.window_size:
            return False
        mask = 1 << diff
        return (self.sliding_window & mask) == 0

    def commit_sequence(self, seq: int) -> None:
        """Comita o número de sequência após autenticação criptográfica bem-sucedida."""
        if seq > self.highest_seq:
            diff = seq - self.highest_seq
            if diff < self.window_size:
                self.sliding_window <<= diff
                self.sliding_window |= 1
            else:
                self.sliding_window = 1
            self.highest_seq = seq
        else:
            diff = self.highest_seq - seq
            if diff < self.window_size:
                self.sliding_window |= 1 << diff

    def validate_sequence(self, seq: int) -> bool:
        """Verifica e comita o número de sequência em operação única atômica."""
        if not self.check_sequence(seq):
            return False
        self.commit_sequence(seq)
        return True
