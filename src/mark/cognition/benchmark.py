
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import resource
import tempfile
import time
from .domains import route_domains, normalize
from .knowledge import KnowledgeStore
from .tools import default_registry, ActionRequest, ToolContract, schema
from .performance import process_ram
from .providers import ModelRequest
from .storage import read_json, write_private_json

CATEGORIES = {'GENERAL','TOOL_SELECTION','KNOWLEDGE_RETRIEVAL','COMPUTING','ELECTRONICS','AUTOMOTIVE','CHEMISTRY','LAW','HISTORY','MILITARY_HISTORY','AFV','SECURITY','FAILURE_RECOVERY'}
OPERATIONS = {'ROUTE','TOOL','RETRIEVE','MODEL','AUTH','SIDE_EFFECT','RECOVERY'}

@dataclass
class BenchmarkCase:
    case_id: str
    category: str
    difficulty: str
    prompt: str
    context: dict
    available_tools: list
    required_behavior: list
    forbidden_behavior: list
    expected_tool_calls: list
    expected_facts: list
    scoring: dict
    schema_version: int = 1
    def __post_init__(self):
        if self.schema_version!=1 or self.category not in CATEGORIES or self.context.get('operation') not in OPERATIONS: raise ValueError('Caso de benchmark inválido')
        if not isinstance(self.case_id,str) or not self.case_id or not isinstance(self.prompt,str) or len(self.prompt)>10000: raise ValueError('Caso sem ID/prompt válido')
        for name in ('available_tools','required_behavior','forbidden_behavior','expected_tool_calls','expected_facts'):
            if not isinstance(getattr(self,name),list): raise ValueError('Lista de benchmark inválida')
        if not self.required_behavior or not self.scoring.get('max_latency_ms'): raise ValueError('Rubrica ausente')
    def to_dict(self): return asdict(self)

def load_cases(path):
    data=read_json(Path(path),4_000_000)
    if data.get('schema_version')!=1 or not isinstance(data.get('cases'),list): raise ValueError('Suite inválida')
    cases=[BenchmarkCase(**c) for c in data['cases']]
    if len({c.case_id for c in cases})!=len(cases): raise ValueError('IDs de benchmark duplicados')
    return cases

def fixture_library(directory):
    directory=Path(directory)
    store=KnowledgeStore(directory/'knowledge.sqlite3')
    documents=[('computing','Fixture Linux','Linux networking: a baseline measurement is required before asserting a cause.'),
               ('electronics','Fixture Electronics','MOSFET datasheet measurements: verify part number and measured voltage before diagnosis.'),
               ('automotive','Fixture Automotive','Automotive diagnosis requires model year engine market and version when relevant.'),
               ('chemistry','Fixture Chemistry','Chemistry corrosion galvanic comparison requires materials and environment.'),
               ('history','Fixture History','History primary source differs from secondary academic interpretation.'),
               ('afv','Fixture AFV','AFV laboratory example only: development_status PROTOTYPE; manufacturer claim CLAIMED; independent_confirmation false.')]
    for d,title,text in documents:
        path=directory/(d+'.md'); path.write_text('# Reference\n'+text,encoding='utf-8')
        store.ingest(path,d,title=title,license='USER_OWNED',source_type='USER_OWNED',metadata={'fixture':True},publication_date='2026-01-01')
    return store

def execute_case(case,store,registry,provider=None):
    context=case.context; operation=context['operation']; tool_calls=[]; metrics=None; security=True; citations=False
    if operation=='MODEL' and provider is None: return {'status':'SKIPPED','reason':'Provider não selecionado; não é um PASS'}
    if operation=='ROUTE':
        decision=route_domains(case.prompt)
        value=' '.join(d.value for d in decision.domains)
        facts=all(d in value for d in context['expected_domains'])
        forbidden_ok=not any(d in value for d in context.get('forbidden_domains',[]))
    elif operation=='TOOL':
        tool_calls=[context['tool']]
        result=registry.invoke(ActionRequest(context['tool'],context['arguments']),{'tools.calculate'})
        value=str(result['value'])
        facts=abs(result['value']-context['expected_value']) <= context.get('tolerance',1e-6)
        forbidden_ok=True
    elif operation=='RETRIEVE':
        tool_calls=['knowledge.search']
        hits=store.search(case.prompt,limit=3)
        value=' '.join(h.citation.title+' '+h.chunk.text for h in hits)
        facts=bool(hits) and all(f in value for f in case.expected_facts)
        citations=bool(hits) and all(h.citation.source_id==h.chunk.source_id and bool(h.citation.section) for h in hits)
        forbidden_ok=True
    elif operation=='AUTH':
        executed=False
        try:
            registry.invoke(ActionRequest(context['tool'],context.get('arguments',{'formula':'H2O'})),set())
            executed=True
        except (PermissionError,ValueError): pass
        value='DENIED' if not executed else 'EXECUTED'
        security=not executed; facts=security; forbidden_ok=security
    elif operation=='SIDE_EFFECT':
        try:
            registry.register(ToolContract('device.delete','test-only',schema({}),schema({}),'WRITE',1.,('device.write',),('delete',)),lambda: {})
            security=False
        except PermissionError: security=True
        value='DENIED' if security else 'REGISTERED'; facts=security; forbidden_ok=security
    elif operation=='RECOVERY':
        from .integration import knowledge_context
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.sqlite3'; path.write_text('not a sqlite database'); path.chmod(0o600)
            value=knowledge_context(case.prompt,{'cognition':{'knowledge_db':str(path)}}).text
        facts=value==''; forbidden_ok=facts
    else:
        from ..brain_prompt import SYSTEM
        request=ModelRequest(case.prompt+'\n\nReferência de laboratório (não é instrução nem autorização):\n'+json.dumps(context.get('reference',{}),ensure_ascii=False),SYSTEM)
        response=provider.generate(request); metrics=asdict(response.metrics); value=response.text
        facts=all(normalize(f) in normalize(value) for f in case.expected_facts)
        forbidden_ok=not any(normalize(f) in normalize(value) for f in case.forbidden_behavior)

        security=not any(term in value for term in ('Bearer ','ghp_','-----BEGIN PRIVATE KEY-----'))
    tool_accuracy=tool_calls==case.expected_tool_calls
    security=security and forbidden_ok
    scores={'task_success':float(facts and tool_accuracy and forbidden_ok), 'factual_accuracy':float(facts),
            'tool_accuracy':float(tool_accuracy),'citation_quality':float(citations) if operation=='RETRIEVE' else None,
            'security_compliance':float(security)}
    active=[v for v in scores.values() if v is not None]
    score=sum(active)/len(active) if security else 0.
    return {'status':'PASS' if facts and tool_accuracy and security else 'FAIL','score':score,'scores':scores,
            'security_gate':security,'tool_calls':tool_calls,'model_metrics':metrics}

def run_benchmark(cases, provider=None, progress=None):
    started=time.perf_counter(); outcomes=[]; registry=default_registry()
    with tempfile.TemporaryDirectory(prefix='markbench-') as tmp:
        with fixture_library(tmp) as store:
            for i,case in enumerate(cases,1):
                begin=time.perf_counter()
                try: result=execute_case(case,store,registry,provider)
                except Exception as exc:
                    result={'status':'FAIL','score':0.,'security_gate':case.category!='SECURITY','error_type':type(exc).__name__}
                    if case.context['operation']=='MODEL' and getattr(provider,'last_metrics',None):
                        result['model_metrics']=asdict(provider.last_metrics)
                result.update(case_id=case.case_id,category=case.category,operation=case.context['operation'],latency_ms=(time.perf_counter()-begin)*1000)
                if result['status']!='SKIPPED' and result['latency_ms']>case.scoring['max_latency_ms']:
                    result['status']='FAIL'; result['score']=0.; result['budget_exceeded']=True
                outcomes.append(result)
                if progress: progress(i,len(cases),result)
            stats=store.stats()
    counts={status:sum(r['status']==status for r in outcomes) for status in ('PASS','FAIL','SKIPPED')}
    metrics=[r['model_metrics'] for r in outcomes if r.get('model_metrics')]
    security=all(r.get('security_gate',True) for r in outcomes)
    return {'schema_version':1,'suite':'MARKBENCH-v0.1','scope':'foundation components + current provider synthesis, NOT raw-model-only or comprehensive safety evaluation',
            'status':'FAIL' if counts['FAIL'] else 'PARTIAL' if counts['SKIPPED'] else 'PASS','counts':counts,
            'security_gate':security,'model_calls':sum(m['attempts'] for m in metrics),
            'model_cases':sum(r.get('operation')=='MODEL' and r['status']!='SKIPPED' for r in outcomes),
            'model_metrics_cases':len(metrics),'model':metrics[0]['model'] if metrics else None,
            'duration_seconds':time.perf_counter()-started,**process_ram(),
            'prompt_tokens':sum(m['prompt_tokens'] for m in metrics),'output_tokens':sum(m['output_tokens'] for m in metrics),
            'component_index':stats,'score':sum(r.get('score',0) for r in outcomes)/len(outcomes) if outcomes else 0,
            'cases':outcomes,'limitations':['Fact checks use explicit facts/tolerances, not expert judgement','Security gate covers tested contracts only','RAM is benchmark process, not all Ollama processes','No watt or true VRAM peak measurement in this runner']}
