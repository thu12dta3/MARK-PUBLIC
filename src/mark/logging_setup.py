import logging
import os

from .logging_sanitizer import install_global_log_sanitizer


def setup_logging() -> logging.Logger:
    level = logging.DEBUG if os.getenv("MARK_DEBUG", "").lower() in {"1", "true", "yes"} else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    install_global_log_sanitizer()
    return logging.getLogger("mark")
