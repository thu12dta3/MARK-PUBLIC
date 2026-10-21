
from dataclasses import dataclass, field, asdict
import hashlib
import json
import re
from pathlib import Path
from .domains import domain, normalize
from .schemas import checked_text, TRAINING_LICENSES, version_check
from .privacy import sanitize, SENSITIVE_KEYS

@dataclass
class DatasetSample:
    sample_id: str
    dataset_version: str
    domain: str
    difficulty: str
    user_input: str
    context: str
    available_tools: list
    expected_plan: list
    expected_tool_calls: list
    expected_answer: str
    constraints: list
    failure_modes: list
    source: dict
    review_status: str
    schema_version: int = 1
    def __post_init__(self):
        version_check(self.schema_version); self.domain=domain(self.domain).value
        for name in ('sample_id','dataset_version','difficulty','user_input','expected_answer'):
            checked_text(getattr(self,name),name,20000)
        checked_text(self.context,'context',20000,False)
        for name in ('available_tools','expected_plan','expected_tool_calls','constraints','failure_modes'):
            if not isinstance(getattr(self,name),list) or len(getattr(self,name))>100: raise ValueError('Lista inválida: '+name)
        if self.review_status not in {'DRAFT','PENDING','APPROVED','REJECTED'}: raise ValueError('Revisão inválida')
        if not isinstance(self.source,dict) or self.source.get('license') not in TRAINING_LICENSES or not self.source.get('provenance'):
            raise ValueError('Proveniência/licença de treino ausente ou não permitida')
        if sanitize(asdict(self)) != asdict(self):
            raise ValueError('Possível dado privado: sanitizar e revisar antes')
    def to_dict(self): return asdict(self)

def fingerprint(sample):
    text=sample.user_input if isinstance(sample,DatasetSample) else sample['user_input']
    canonical=' '.join(re.findall(r'\w+',normalize(text)))
    return hashlib.sha256(canonical.encode()).hexdigest()

def load_samples(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size>4_000_000: raise ValueError('Dataset inválido/grande demais')
    rows=[]
    for line in p.read_text(encoding='utf-8').splitlines():
        if not line.strip(): continue
        d=json.loads(line)
        if not isinstance(d,dict) or set(d)&{'chain_of_thought','hidden_reasoning','expected_reasoning'}: raise ValueError('Raciocínio oculto não permitido')
        rows.append(DatasetSample(**d))
    if not rows: raise ValueError('Dataset vazio')
    return rows

def validate_samples(samples, require_approved=False):
    ids=set(); content=set(); errors=[]
    for item in samples:
        sample=item if isinstance(item,DatasetSample) else DatasetSample(**item)
        if sample.sample_id in ids: errors.append('sample_id duplicado: '+sample.sample_id)
        if fingerprint(sample) in content: errors.append('Conteúdo duplicado: '+sample.sample_id)
        if require_approved and sample.review_status!='APPROVED': errors.append('Exemplo não aprovado: '+sample.sample_id)
        ids.add(sample.sample_id); content.add(fingerprint(sample))
    return {'status':'FAIL' if errors or not samples else 'PASS','samples':len(samples),'errors':errors,'schema_version':1}

def deduplicate(samples):
    result=[]; seen=set(); dropped=[]
    for sample in samples:
        key=fingerprint(sample)
        if key in seen: dropped.append(sample.sample_id)
        else: result.append(sample); seen.add(key)
    return result, dropped

def check_overlap(splits):
    observed={}; ids={}; errors=[]
    for split,samples in splits.items():
        for sample in samples:
            key=fingerprint(sample)
            if key in observed: errors.append(f'Overlap de conteúdo: {observed[key]} / {split}')
            if sample.sample_id in ids: errors.append(f'Overlap de sample_id: {ids[sample.sample_id]} / {split}')
            observed[key]=split; ids[sample.sample_id]=split
    return errors

def split_dataset(samples, seed='MARK-v1'):
    validation=validate_samples(samples,require_approved=True)
    if validation['status']!='PASS': raise ValueError('; '.join(validation['errors']))
    if len(samples)<3: raise ValueError('Ao menos três exemplos revisados para três splits')
    ordered=sorted(samples,key=lambda s:hashlib.sha256((seed+fingerprint(s)).encode()).hexdigest())
    reserve=max(1,len(samples)//10)
    splits={'test':ordered[:reserve],'validation':ordered[reserve:2*reserve],'train':ordered[2*reserve:]}
    if check_overlap(splits): raise ValueError('Contaminação detectada')
    return splits

def save_splits(splits, directory):
    from .storage import private_file
    p=Path(directory); p.mkdir(mode=0o700,parents=True,exist_ok=True)
    for name,items in splits.items():
        target=private_file(p/(name+'.jsonl'))
        target.write_text(''.join(json.dumps(s.to_dict(),ensure_ascii=False)+'\n' for s in items),encoding='utf-8')
    return {'status':'PASS','counts':{name:len(items) for name,items in splits.items()},'overlap':check_overlap(splits)}
