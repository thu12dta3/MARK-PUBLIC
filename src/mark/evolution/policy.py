
from dataclasses import dataclass, asdict
import hashlib
import json
import math
from pathlib import PurePosixPath

MiB = 1024**2
GiB = 1024**3
CODE_PATHS = frozenset({'src/mark/cognition/providers.py', 'src/mark/cognition/model_reply.py'})
ADMIN_ACTIONS = frozenset({'deploy', 'rollback', 'training', 'install_timer', 'publish_pr'})
BLOCKED_ACTIONS = frozenset({'shell', 'force_push', 'disable_security', 'change_policy', 'delete_history',
                            'delete_backups', 'firmware', 'read_credentials', 'expose_network'})

@dataclass(frozen=True)
class Policy:
    schema_version: int = 1
    enabled: bool = True
    allow_code_experiments: bool = True
    allow_model_experiments: bool = False
    schedule_enabled: bool = False
    scheduled_experiments: bool = False
    max_experiments: int = 1
    timeout_seconds: int = 90
    memory_mib: int = 2048
    cpu_seconds: int = 60
    max_attempts: int = 2
    min_interval_seconds: int = 3600
    backoff_seconds: int = 7200
    skills_bytes: int = 100*MiB
    metadata_bytes: int = 100*MiB
    adapter_bytes: int = GiB
    temporary_bytes: int = 3*GiB
    reserve_free_bytes: int = 5*GiB
    max_reports: int = 1000
    vram_gib: float = 4.5

    def __post_init__(self):
        for field in ('enabled','allow_code_experiments','allow_model_experiments','schedule_enabled','scheduled_experiments'):
            if type(getattr(self,field)) is not bool: raise ValueError('Política boolean inválida')
        bounds={'max_experiments':(1,3),'timeout_seconds':(1,600),'memory_mib':(128,8192),
                'cpu_seconds':(1,300),'max_attempts':(1,3),'min_interval_seconds':(60,86400*30),
                'backoff_seconds':(60,86400*30),'max_reports':(1,10000),
                'skills_bytes':(MiB,100*MiB),'metadata_bytes':(MiB,100*MiB),
                'adapter_bytes':(MiB,4*GiB),'temporary_bytes':(MiB,16*GiB),
                'reserve_free_bytes':(MiB,100*GiB)}
        for key,(low,high) in bounds.items():
            value=getattr(self,key)
            if type(value) is not int or not low<=value<=high: raise ValueError('Orçamento inválido: '+key)
        if self.schema_version!=1 or type(self.vram_gib) not in (float,int) or not math.isfinite(self.vram_gib) or not 0<self.vram_gib<=7.5:
            raise ValueError('Versão/VRAM da política inválida')

    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True).encode()).hexdigest()

    def authorize(self,action,owner_approved=False):
        if not self.enabled: raise PermissionError('Aprendizagem desabilitada')
        if action in BLOCKED_ACTIONS: raise PermissionError('Operação bloqueada por política')
        if action in ADMIN_ACTIONS:
            if owner_approved is not True: raise PermissionError('Revisão específica do proprietário requerida')
            return
        if action not in {'scan','propose','code_experiment','learn_skill','model_experiment'}:
            raise PermissionError('Operação não registrada')
        if action=='code_experiment' and not self.allow_code_experiments: raise PermissionError('Experimentos de código desativados')
        if action=='model_experiment' and not self.allow_model_experiments: raise PermissionError('Experimentos de modelo desativados')


def candidate_path(value):
    if not isinstance(value,str) or value not in CODE_PATHS:
        raise PermissionError('Arquivo protegido ou fora da lista de candidatos v0.1')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts: raise PermissionError('Caminho inválido')
    return path
