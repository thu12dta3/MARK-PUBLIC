
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from enum import Enum
import json
import re
from urllib.parse import urlsplit
from .domains import Domain, domain

SCHEMA_VERSION = 1
class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"
class Freshness(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    HISTORICAL = "HISTORICAL"
    UNKNOWN = "UNKNOWN"
class SourceType(str, Enum):
    OFFICIAL = "OFFICIAL"
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    ACADEMIC = "ACADEMIC"
    MANUFACTURER = "MANUFACTURER"
    POPULAR = "POPULAR"
    UNVERIFIED = "UNVERIFIED"
    USER_OWNED = "USER_OWNED"
class DevelopmentStatus(str, Enum):
    OPERATIONAL = "OPERATIONAL"
    SERIAL_PRODUCTION = "SERIAL_PRODUCTION"
    LOW_RATE_PRODUCTION = "LOW_RATE_PRODUCTION"
    PRE_PRODUCTION = "PRE_PRODUCTION"
    PROTOTYPE = "PROTOTYPE"
    TECHNOLOGY_DEMONSTRATOR = "TECHNOLOGY_DEMONSTRATOR"
    MOCKUP = "MOCKUP"
    CONCEPT = "CONCEPT"
    ANNOUNCED = "ANNOUNCED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
class ClaimStatus(str, Enum):
    CLAIMED = "CLAIMED"
    CONFIRMED = "CONFIRMED"
    INDEPENDENTLY_CONFIRMED = "INDEPENDENTLY_CONFIRMED"
    DISPUTED = "DISPUTED"
    UNKNOWN = "UNKNOWN"
class SourceTier(str, Enum):
    TIER_A = "TIER_A"
    TIER_B = "TIER_B"
    TIER_C = "TIER_C"
    TIER_D = "TIER_D"
    UNKNOWN = "UNKNOWN"

LICENSES = {"USER_OWNED", "PUBLIC_DOMAIN", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "MIT", "Apache-2.0", "EXPLICIT_PERMISSION", "REFERENCE_ONLY_PERMISSION"}
TRAINING_LICENSES = LICENSES - {"REFERENCE_ONLY_PERMISSION"}

def utc_now(): return datetime.now(timezone.utc).isoformat()
def checked_text(value, name, limit=1000, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{name} ausente ou inválido")
    return value

def checked_date(value):
    if value is not None:
        if not isinstance(value, str): raise ValueError("Data inválida")
        date.fromisoformat(value)
    return value

def checked_metadata(value):
    if not isinstance(value, dict): raise ValueError("Metadata deve ser objeto")
    if len(json.dumps(value, allow_nan=False)) > 32_000: raise ValueError("Metadata excede o limite")
    return value

def version_check(value):
    if type(value) is not int or value != SCHEMA_VERSION: raise ValueError("Versão de schema não suportada")

@dataclass
class SourceRecord:
    source_id: str
    domain: Domain
    title: str
    license: str
    checksum: str
    author: str = "UNKNOWN"
    publisher: str = "UNKNOWN"
    source_type: SourceType = SourceType.UNVERIFIED
    url: str = ""
    local_path: str = ""
    language: str = "und"
    publication_date: str | None = None
    retrieved_at: str = field(default_factory=utc_now)
    reliability: Confidence = Confidence.UNKNOWN
    metadata: dict = field(default_factory=dict)
    version: int = 1
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        version_check(self.schema_version)
        self.domain = domain(self.domain)
        self.source_type = SourceType(self.source_type)
        self.reliability = Confidence(self.reliability)
        for key in ("source_id", "title", "author", "publisher", "language", "retrieved_at"):
            checked_text(getattr(self, key), key)
        for key in ("url", "local_path"): checked_text(getattr(self, key), key, 4000, False)
        for url in (self.url, self.metadata.get('source_url', '') if isinstance(self.metadata,dict) else ''):
            if url:
                parsed=urlsplit(url)
                if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password: raise ValueError('URL de fonte inválida')
        if self.license not in LICENSES: raise ValueError("Licença/proveniência explícita necessária")
        if not isinstance(self.checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", self.checksum): raise ValueError("Checksum SHA-256 inválido")
        if type(self.version) is not int or self.version < 1: raise ValueError("Versão da fonte inválida")
        checked_date(self.publication_date)
        datetime.fromisoformat(self.retrieved_at)
        checked_metadata(self.metadata)
        if 'claim_status' in self.metadata: ClaimStatus(self.metadata['claim_status'])
        if 'source_tier' in self.metadata:
            tier=SourceTier(self.metadata['source_tier'])
            if tier==SourceTier.TIER_D and self.reliability not in (Confidence.LOW,Confidence.UNKNOWN): raise ValueError('TIER_D não recebe confiança factual elevada')
        for key in ('last_verified', 'valid_until', 'version_date'):
            if key in self.metadata: checked_date(self.metadata[key])
        if self.source_type == SourceType.UNVERIFIED and self.reliability == Confidence.HIGH:
            raise ValueError('Fonte não verificada não recebe confiança HIGH')
        if self.domain == Domain.LAW:
            for key in ("country", "jurisdiction", "authority", "document", "article", "publication_date", "effective_date", "version_date", "source_url", "source_type"):
                if key not in self.metadata: raise ValueError("Metadata legal ausente: " + key)
            for key in ("publication_date", "effective_date", "version_date"): checked_date(self.metadata[key])

    def to_dict(self): return asdict(self)

@dataclass
class ChunkRecord:
    chunk_id: str
    source_id: str
    domain: Domain
    text: str
    section: str = ""
    page: int | None = None
    metadata: dict = field(default_factory=dict)
    embedding_model: str = "mark-hash"
    embedding_version: str = "1"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        version_check(self.schema_version)
        self.domain = domain(self.domain)
        for key in ("chunk_id", "source_id", "embedding_model", "embedding_version"): checked_text(getattr(self, key), key)
        checked_text(self.text, "text", 4000)
        checked_text(self.section, "section", 1000, False)
        if self.page is not None and (type(self.page) is not int or self.page < 1): raise ValueError("Página inválida")
        checked_metadata(self.metadata)
    def to_dict(self): return asdict(self)

VEHICLE_CLASSES = {"tank", "MBT", "light tank", "tankette", "armored car", "APC", "IFV", "MRAP", "tank destroyer", "self-propelled artillery", "self-propelled AA", "recon vehicle", "engineering vehicle", "recovery vehicle", "bridge layer", "command vehicle", "mortar carrier", "prototype", "demonstrator", "UNKNOWN"}

@dataclass
class AFVRecord:
    canonical_name: str
    aliases: list = field(default_factory=list)
    country: str = "UNKNOWN"
    manufacturer: str = "UNKNOWN"
    design_bureau: str = "UNKNOWN"
    vehicle_class: str = "UNKNOWN"
    development_status: DevelopmentStatus = DevelopmentStatus.UNKNOWN
    first_public_appearance: str | None = None
    prototype_year: int | None = None
    production_start: int | None = None
    production_end: int | None = None
    crew: int | None = None
    combat_weight: dict | None = None
    dimensions: dict = field(default_factory=dict)
    engine: str = "UNKNOWN"
    engine_power: dict | None = None
    power_to_weight: dict | None = None
    transmission: str = "UNKNOWN"
    suspension: str = "UNKNOWN"
    maximum_speed: dict | None = None
    range: dict | None = None
    armor: dict = field(default_factory=dict)
    protection_systems: list = field(default_factory=list)
    main_armament: str = "UNKNOWN"
    secondary_armament: list = field(default_factory=list)
    sensors: list = field(default_factory=list)
    fire_control: str = "UNKNOWN"
    thermal_systems: list = field(default_factory=list)
    radar: str = "UNKNOWN"
    variants: list = field(default_factory=list)
    operators: list = field(default_factory=list)
    prototype_lineage: list = field(default_factory=list)
    production_lineage: list = field(default_factory=list)
    known_quantity_built: int | None = None
    sources: list = field(default_factory=list)
    last_verified: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        version_check(self.schema_version)
        checked_text(self.canonical_name, "canonical_name")
        self.development_status = DevelopmentStatus(self.development_status)
        self.confidence = Confidence(self.confidence)
        if self.vehicle_class not in VEHICLE_CLASSES: raise ValueError("Classe de veículo inválida")
        checked_date(self.last_verified)
        for key in ('aliases','protection_systems','secondary_armament','sensors','thermal_systems','variants','operators','prototype_lineage','production_lineage','sources'):
            items=getattr(self,key)
            if not isinstance(items,list) or len(items)>1000 or any(not isinstance(item,str) or len(item)>1000 for item in items): raise ValueError('Lista inválida: '+key)
        for key in ('country','manufacturer','design_bureau','engine','transmission','suspension','main_armament','fire_control','radar'):
            checked_text(getattr(self,key),key)
        for key in ('dimensions','armor'): checked_metadata(getattr(self,key))
        if not isinstance(self.sources, list): raise ValueError("Fontes inválidas")
        if self.confidence != Confidence.UNKNOWN and not self.sources: raise ValueError("Confiança exige fonte")
        for name in ("combat_weight", "engine_power", "power_to_weight", "maximum_speed", "range"):
            claim = getattr(self, name)
            if claim is None: continue
            if not isinstance(claim, dict) or not {"value", "unit", "status", "source_id", "independent_confirmation"} <= claim.keys():
                raise ValueError("Claim sem origem/status: " + name)
            status = ClaimStatus(claim["status"])
            if type(claim["independent_confirmation"]) is not bool: raise ValueError("Confirmação deve ser boolean")
            if status == ClaimStatus.INDEPENDENTLY_CONFIRMED and not claim["independent_confirmation"]:
                raise ValueError("Claim não possui confirmação independente")
            if claim["source_id"] not in self.sources: raise ValueError("Claim aponta fonte não vinculada")
        for name in ("prototype_year", "production_start", "production_end", "crew", "known_quantity_built"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0): raise ValueError(name + " inválido")
    def to_dict(self): return asdict(self)
