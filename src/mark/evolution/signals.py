
import hashlib
import re
from .state import canonical

ERRORS={'RuntimeError','TimeoutError','HTTPError','URLError','EmptyFinal','InvalidResponse','DatabaseError','QualityMismatch'}


def extract_signals(report,version):
    if not isinstance(report,dict):raise ValueError('Relatório precisa ser objeto')
    rows=report.get('cases',[])
    if not isinstance(rows,list) or len(rows)>1000:raise ValueError('Relatório excedeu limite')
    signals=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Caso inválido')
        metrics=row.get('model_metrics') or {}
        if not isinstance(metrics,dict):raise ValueError('Métricas inválidas')
        attempts=metrics.get('attempts',1)
        failed=row.get('status') in {'FAIL','ERROR'}
        if not failed and not (type(attempts) is int and attempts>1):continue
        quality=failed and metrics.get('success') is True and attempts==1 and not row.get('error_type')
        error=row.get('error_type','QualityMismatch' if quality else 'EmptyFinal' if attempts!=1 else 'InvalidResponse')
        error=error if error in ERRORS else 'InvalidResponse'
        case=row.get('case_id',row.get('id','UNKNOWN'))
        case=case if isinstance(case,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,40}',case) else 'UNKNOWN'
        value={'component':'evaluation' if quality else 'model_provider','error':error,'version':version,'case':case}


        identity_value={key:item for key,item in value.items() if key!='case'}
        identity=hashlib.sha256(canonical(identity_value).encode()).hexdigest()[:20]
        signals.append(value|{'id':identity,'evidence':{'failed':failed,'attempts':attempts if type(attempts)is int else None},
                              'impact':'reply_reliability','confidence':.7,'reproducible':'requires controlled replay',
                              'risk':2,'hypotheses':['Final message may be empty, truncated or malformed'],
                              'next_test':'Replay malformed/empty final content with bounded retries'})
    return signals


def propose(signal):
    if signal.get('component')=='evaluation':
        return {'id':'P-'+signal['id'],'signal_id':signal['id'],'risk':1,'experiment_kind':'evaluation_review',
                'problem':'Rubric mismatch despite successful generation','goal':'Determine whether error is factual or lexical scoring before changing software',
                'hypothesis':'Review source evidence and frozen rubric; do not tune benchmark criteria to pass',
                'possible_files':[],'tests':['independent rubric review'],'deployment':'NO_CODE_CHANGE_AUTHORIZED'}
    if signal.get('component')!='model_provider':raise ValueError('Sem planejador validado para este componente')
    return {'id':'P-'+signal['id'],'signal_id':signal['id'],'risk':2,'experiment_kind':'code',
            'problem':'Final reply extraction can fail before bounded recovery',
            'goal':'Handle missing/non-string final content without leaking thinking or losing valid final replies',
            'hypothesis':'Validate response shape before extracting content and token counts; retain current retry limits',
            'possible_files':['src/mark/cognition/providers.py','src/mark/cognition/model_reply.py'],
            'estimated_seconds':90,'tests':['independent malformed/final/thinking response replay','protected regression suite'],
            'rejection':['security regression','valid reply lost','timeouts increased','thinking disabled','protected file changed'],
            'rollback':'Discard isolated candidate; production never changed',
            'deployment':'OWNER_REVIEW_REQUIRED'}
