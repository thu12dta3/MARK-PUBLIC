
from dataclasses import dataclass, asdict
from pathlib import Path
import re
import os
import json
import stat
from .storage import write_private_json, read_json
from .schemas import utc_now
from .privacy import sanitize

DEFAULT_DIR = Path.home()/'.local/state/mark/cognition/experiments'

@dataclass
class Experiment:
    experiment_id: str
    base_model: str
    model_size: str
    dataset: str
    dataset_version: str
    training_method: str
    quantization: str
    learning_rate: float | None
    epochs: int | None
    batch_size: int | None
    gradient_accumulation: int | None
    hardware: dict
    duration: float | None
    VRAM_peak: float | None
    RAM_peak: float | None
    tokens_per_second: float | None
    MARKBench_score: float | None
    security_score: float | None
    notes: str
    result: str
    schema_version: int = 1
    def __post_init__(self):
        if not re.fullmatch(r'EXP-\d{4}',self.experiment_id): raise ValueError('Use EXP-XXXX')
        if self.schema_version!=1 or self.result not in {'PASS','FAIL','PARTIAL','PLANNED','UNVERIFIED'}: raise ValueError('Experimento inválido')
        if sanitize(self.notes)!=self.notes: raise ValueError('Notas contêm possível dado privado')
    def to_dict(self): return asdict(self)

class ExperimentStore:
    def __init__(self,directory=DEFAULT_DIR): self.directory=Path(directory)
    def add(self, experiment: Experiment):
        path=self.directory/(experiment.experiment_id+'.json')
        if path.exists(): raise ValueError('Experimento já existe; IDs são imutáveis')
        data=experiment.to_dict(); data['created_at']=utc_now()
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        directory_info=self.directory.lstat()
        if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid!=os.getuid(): raise PermissionError('Pasta de experimentos inválida')
        self.directory.chmod(0o700)
        payload=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        try: fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        except FileExistsError: raise ValueError('Experimento já existe; IDs são imutáveis') from None
        with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(payload)
        return data
    def list(self):
        if not self.directory.exists(): return []
        return [read_json(path) for path in sorted(self.directory.glob('EXP-*.json'))]

def validate_training_config(config, dataset_report, baseline_report):
    errors=[]
    if config.get('method') not in {'LORA','QLORA','DISTILLATION'}: errors.append('Método inválido')
    if not (0 < config.get('base_model_parameters',0) <= 4_500_000_000): errors.append('Modelo fora do orçamento inicial')
    if not (0 < config.get('vram_budget_gb',0) <= 8): errors.append('Orçamento VRAM inválido')
    if type(config.get('batch_size')) is not int or config['batch_size'] not in (1,2): errors.append('Batch local deve ser 1 ou 2')
    if type(config.get('gradient_accumulation')) is not int or config['gradient_accumulation']<1: errors.append('Gradient accumulation inválido')
    if config.get('full_parameter_training') is not False: errors.append('Treino full-parameter não permitido nesta fase')
    if dataset_report.get('status')!='PASS' or dataset_report.get('reviewed') is not True: errors.append('Dataset revisado ainda não aprovado')
    if baseline_report.get('status')!='PASS' or not baseline_report.get('model_calls') or baseline_report.get('security_gate') is not True: errors.append('Baseline real ainda não aprovado')
    return {'status':'FAIL' if errors else 'PASS','errors':errors,'execution':('READY_FOR_PREFLIGHT' if config.get('executor')=='hf-qlora-v02' and not errors else 'NOT_IMPLEMENTED')}
