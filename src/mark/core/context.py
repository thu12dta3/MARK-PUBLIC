

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
from uuid import uuid4


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_KEY = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_SOURCE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class CoreContext:







    request_id: str = field(default_factory=lambda: uuid4().hex)
    session_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    specialization: str = "general"
    source: str = "unknown"

    def __post_init__(self) -> None:
        for name in ("request_id", "session_id", "project_id", "task_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not _ID.fullmatch(value)):
                raise ValueError(f"{name} inválido")
        if not isinstance(self.specialization, str) or not _KEY.fullmatch(self.specialization):
            raise ValueError("specialization inválida")
        if not isinstance(self.source, str) or not _SOURCE.fullmatch(self.source):
            raise ValueError("source inválida")

    @classmethod
    def create(
        cls,
        *,
        session_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        specialization: str = "general",
        source: str = "unknown",
    ) -> "CoreContext":
        return cls(
            session_id=session_id,
            project_id=project_id,
            task_id=task_id,
            specialization=specialization,
            source=source,
        )


_active_context: ContextVar[CoreContext | None] = ContextVar("mark_core_context", default=None)


def current_context() -> CoreContext:

    return _active_context.get() or CoreContext.create()


@contextmanager
def bind_context(context: CoreContext):
    if not isinstance(context, CoreContext):
        raise TypeError("context precisa ser CoreContext")
    token = _active_context.set(context)
    try:
        yield context
    finally:
        _active_context.reset(token)
