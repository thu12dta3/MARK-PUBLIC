"""Bridge Python para mark_core (módulo nativo em Rust compilado com PyO3)."""

try:
    from mark_core.mark_core import *  # type: ignore[import-not-found]
except ImportError:
    pass
