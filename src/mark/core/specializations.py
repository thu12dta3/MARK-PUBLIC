

from dataclasses import dataclass
import re

_KEY = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")


@dataclass(frozen=True, slots=True)
class Specialization:
    key: str
    label: str
    purpose: str
    guidance: str
    domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not _KEY.fullmatch(self.key):
            raise ValueError("Chave de especialização inválida")
        if not isinstance(self.label, str) or not self.label.strip() or len(self.label) > 64:
            raise ValueError("Nome de especialização inválido")
        if not isinstance(self.purpose, str) or not self.purpose.strip() or len(self.purpose) > 240:
            raise ValueError("Finalidade de especialização inválida")
        if not isinstance(self.guidance, str) or not self.guidance.strip() or len(self.guidance) > 1000:
            raise ValueError("Orientação de especialização inválida")
        if len(self.domains) > 16 or any(not isinstance(d, str) or not _KEY.fullmatch(d.lower()) for d in self.domains):
            raise ValueError("Domínios de especialização inválidos")


class SpecializationRegistry:
    def __init__(self, entries=()):
        self._items: dict[str, Specialization] = {}
        for entry in entries:
            self.register(entry)

    def register(self, entry: Specialization) -> None:
        if not isinstance(entry, Specialization):
            raise TypeError("entry precisa ser Specialization")
        if entry.key in self._items:
            raise ValueError("Especialização duplicada")
        self._items[entry.key] = entry

    def resolve(self, key: str) -> Specialization:
        try:
            return self._items[key]
        except (KeyError, TypeError):
            raise ValueError("Especialização não registrada") from None

    def list(self) -> tuple[Specialization, ...]:
        return tuple(self._items.values())


def default_specializations() -> SpecializationRegistry:
    return SpecializationRegistry((
        Specialization("general", "Geral", "Conversa e assistência ampla.",
                       "Responda com clareza e adapte o nível de detalhe ao pedido."),
        Specialization("engineer", "Engenheiro", "Análise e criação técnica.",
                       "Explicite hipóteses, interfaces, riscos e evidências de validação.",
                       ("computing", "electronics", "automotive")),
        Specialization("professor", "Professor", "Explicações e aprendizagem.",
                       "Explique em etapas, confira conceitos e diferencie fato de hipótese."),
    ))
