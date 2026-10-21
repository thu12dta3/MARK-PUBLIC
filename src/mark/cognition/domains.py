
from dataclasses import dataclass
from enum import Enum
import re
import unicodedata

class Domain(str, Enum):
    COMPUTING = "COMPUTING"
    ELECTRONICS = "ELECTRONICS"
    AUTOMOTIVE = "AUTOMOTIVE"
    CHEMISTRY = "CHEMISTRY"
    LAW = "LAW"
    HISTORY = "HISTORY"
    MILITARY_HISTORY = "MILITARY_HISTORY"
    WEAPONS_KNOWLEDGE = "WEAPONS_KNOWLEDGE"
    AFV_ARMOR = "AFV_ARMOR"
    HEALTH = "HEALTH"

ALIASES = {"AFV": "AFV_ARMOR", "ARMOR": "AFV_ARMOR", "WEAPONS": "WEAPONS_KNOWLEDGE"}

def domain(value: str | Domain) -> Domain:
    if isinstance(value, Domain): return value
    normalized = str(value).upper().replace("-", "_")
    return Domain(ALIASES.get(normalized, normalized))

def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))

@dataclass(frozen=True)
class DomainDefinition:
    name: Domain
    terms: tuple[str, ...]
    temporal: bool = False

REGISTRY = {
    Domain.COMPUTING: DomainDefinition(Domain.COMPUTING, ("linux", "windows", "android", "wear os", "kotlin", "python", "java", "git", "github", "kernel", "driver", "rede", "network", "container", "virtualizacao", "software", "rfc", "pc", "ticwatch"), True),
    Domain.ELECTRONICS: DomainDefinition(Domain.ELECTRONICS, ("circuito", "circuit", "mosfet", "transistor", "capacitor", "resistor", "datasheet", "arduino", "sensor", "sensores", "microcontrolador", "eletronica", "electronics", "fire control", "controle de tiro")),
    Domain.AUTOMOTIVE: DomainDefinition(Domain.AUTOMOTIVE, ("carro", "car", "automotivo", "automotive", "motor", "engine", "transmissao", "ecu", "obd", "obd-ii", "combustivel", "lubrificacao")),
    Domain.CHEMISTRY: DomainDefinition(Domain.CHEMISTRY, ("quimica", "chemistry", "molar", "mol", "estequiometria", "stoichiometry", "corrosao", "corrosion", "galvanica", "acido", "redox", "equilibrio", "termodinamica", "h2o", "nacl", "h2so4", "gas ideal")),
    Domain.LAW: DomainDefinition(Domain.LAW, ("lei", "law", "legal", "jurisdicao", "jurisdiction", "artigo", "legislacao", "regulamento", "jurisprudencia"), True),
    Domain.HISTORY: DomainDefinition(Domain.HISTORY, ("historia", "history", "historico", "revolucao", "imperio", "fonte primaria", "primary source")),
    Domain.MILITARY_HISTORY: DomainDefinition(Domain.MILITARY_HISTORY, ("historia militar", "military history", "guerra", "war", "doutrina", "doctrine", "conflito", "wwi", "wwii")),
    Domain.WEAPONS_KNOWLEDGE: DomainDefinition(Domain.WEAPONS_KNOWLEDGE, ("arma", "armas", "weapon", "weapons", "firearm", "armamento", "fuzil")),
    Domain.AFV_ARMOR: DomainDefinition(Domain.AFV_ARMOR, ("blindado", "blindados", "afv", "armor", "mbt", "tank", "tanque", "ifv", "apc", "mrap", "prototipo", "mockup", "demonstrador"), True),
    Domain.HEALTH: DomainDefinition(Domain.HEALTH, ("saude", "health", "medico", "medical"), True),
}

@dataclass(frozen=True)
class RouteDecision:
    domains: tuple[Domain, ...]
    confidence: float
    reason: str
    intent: str = "GENERAL_CHAT"
    thinking_mode: str = "NORMAL"

def route_domains(text: str) -> RouteDecision:
    if not isinstance(text, str) or len(text) > 100_000: raise ValueError("Consulta inválida")
    n = normalize(text)
    matches = {d: [term for term in record.terms if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", n)]
               for d, record in REGISTRY.items()}
    selected = tuple(d for d, terms in matches.items() if terms)
    if not selected:
        return RouteDecision((), 0.0, "Sem indícios locais suficientes; encaminhar ao núcleo")
    evidence = "; ".join(d.value + ": " + ", ".join(matches[d][:3]) for d in selected)
    return RouteDecision(selected, min(0.9, 0.55 + 0.1 * sum(len(matches[d]) for d in selected)), evidence,
                         intent=selected[0].value)
