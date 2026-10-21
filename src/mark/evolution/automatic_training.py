
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import signal
import threading
from .state import secure_directory
from .engine import immutable_json
from . import model_lab
from .efficiency import EnergyMeter,compare_efficiency
from ..cognition.storage import read_json


def fingerprint(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise PermissionError('Input não regular')
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def validate_contract(path):
    contract=read_json(Path(path))
    if contract.get('schema_version')!=1 or contract.get('authorized_by')!='owner' or contract.get('scope')!='automatic_training_approved_dataset':raise PermissionError('Treino automático sem autorização')
    if type(contract.get('max_rounds'))is not int or not 1<=contract['max_rounds']<=3:raise ValueError('Rodadas inválidas')
    if contract.get('minimum_interval_seconds',0)<86400:raise ValueError('Intervalo insuficiente')
    if not 60<=contract.get('max_seconds',0)<=900:raise ValueError('Tempo inválido')
    if not 5<=contract.get('vram_gib',0)<=7.5:raise ValueError('VRAM inválida')
    for path,checksum in contract['input_hashes'].items():
        if fingerprint(path)!=checksum:raise ValueError('Input autorizado alterado')
    required=[contract[k] for k in ('dataset_approval','baseline','holdout')]+contract['configs']
    required.append(str(Path(contract['base'])/'snapshot-manifest.json'))
    required += [str(Path(contract['splits'])/name) for name in ('train.jsonl','validation.jsonl','test.jsonl','report.json')]
    review=read_json(Path(contract['dataset_approval']));approved=read_json(Path(contract['splits'])/'report.json')['approved_path'];required.append(approved)
    if any(p not in contract['input_hashes'] for p in required):raise PermissionError('Input sem hash na autorização')
    if review.get('reviewed_by')!='owner' or not review.get('reviewed') or fingerprint(approved)!=review.get('approved_sha256'):raise PermissionError('Dataset sem revisão humana')
    if len(contract['configs'])!=contract['max_rounds']:raise ValueError('Receitas fora do escopo')
    return contract


def run_round(state,repo,interactive,runner=None):
    path=state.get('automatic_training_contract')
    if not path:return None
    contract=validate_contract(path)
    completed=state.get('automatic_training_rounds',[])
    finished={r.get('recipe',r['round']) for r in completed if r.get('training_started',r['status'] in {'TRAINED_CANDIDATE','CANCELLED'}) or sum(x.get('recipe',x['round'])==r.get('recipe',r['round']) for x in completed)>=2}
    remaining=[i for i in range(1,contract['max_rounds']+1) if i not in finished]
    if not remaining:return {'status':'TRAINING_SEARCH_COMPLETE','rounds':len(finished)}
    if completed and not state.get('automatic_training_retry_now',False) and time.time()-completed[-1]['time']<contract['minimum_interval_seconds']:
        state.set_progress('WAITING','DEFERRED',wait_reason='training_interval');return {'status':'DEFERRED','reason':'training_interval'}
    if interactive():
        state.set_progress('WAITING','DEFERRED',wait_reason='interactive');return {'status':'DEFERRED','reason':'interactive'}
    root=secure_directory(state.root/'automatic-training');number=remaining[0];state.set('automatic_training_retry_now',False)
    workspace=secure_directory(root/('round-'+str(number)+'-'+str(time.time_ns())))
    policy=replace(state.policy,memory_mib=8192,vram_gib=contract['vram_gib'])
    state.set_progress('OPTIMIZING')
    prepared=model_lab.prepare(contract['configs'][number-1],contract['dataset_approval'],contract['baseline'],contract['base'],contract['splits'],workspace/'plan',policy,contract['holdout'])
    plan_path=Path(prepared['plan']);plan=read_json(plan_path)


    plan['max_seconds']=contract['max_seconds'];plan['standing_authorization_hash']=fingerprint(path)
    from ..cognition.storage import write_private_json
    write_private_json(plan_path,plan)
    receipt=workspace/'training-authorization.json'
    immutable_json(receipt,{'approved_by':'owner','operation':'training','target_hash':fingerprint(plan_path),'approved':True})
    state.event('automatic_training_dispatch',{'round':number,'contract_hash':fingerprint(path),'plan_hash':fingerprint(plan_path)})
    state.set_progress('TRAINING')
    def progress(observed):
        if observed.get('state')=='EVALUATING':state.set_progress('EVALUATING')
        step=observed.get('step',0);samples=observed.get('samples_processed',0)
        if type(step)is int and step>0:
            state.record_activity('training_step',phase='TRAINING',summary={'completed_steps':step,'samples':samples})
    cancelled=threading.Event();previous_handler=None
    if threading.current_thread() is threading.main_thread():
        previous_handler=signal.signal(signal.SIGTERM,lambda *_:cancelled.set())
    try:
        with EnergyMeter() as meter:
            result=(runner or model_lab.run_isolated)(plan_path,receipt,repo,workspace/'lab',policy,
                cancel=lambda:cancelled.is_set() or state.get('paused',False) or state.get('cancel',False),interactive=interactive,progress=progress)
        energy=meter.result();holdout_path=workspace/'lab/output/holdout-ab.json'
        holdout=read_json(holdout_path) if holdout_path.is_file() else {}
        comparison=compare_efficiency(holdout.get('metrics',{}).get('original',{}),holdout.get('metrics',{}).get('adapter',{}),holdout.get('regression',True))
        summary={'round':number,'time':time.time(),'status':result['status'],'decision':comparison['decision'],'reason':comparison['reason'],
                 'training_energy':energy,'comparison':comparison,'lab_result':result,'applied_to_mark':False}
        bm=holdout.get('metrics',{}).get('original',{});am=holdout.get('metrics',{}).get('adapter',{})
        savings=(bm.get('energy_joules',0)/bm.get('total',1)-am.get('energy_joules',0)/am.get('total',1)) if type(bm.get('energy_joules'))in (int,float) and type(am.get('energy_joules'))in (int,float) else 0
        summary['estimated_energy_payback_requests']=energy['energy_joules']/savings if energy['energy_joules'] is not None and savings>0 else None
        if comparison['decision']=='REVIEW_REQUIRED':state.set('automatic_training_best_candidate',str(workspace/'automatic-result.json'))
        immutable_json(workspace/'automatic-result.json',summary)
        state.set('automatic_training_rounds',[*completed,{'round':number,'recipe':number,'training_started':bool(result.get('training_started')),'time':summary['time'],'status':result['status'],'decision':comparison['decision'],'report':str(workspace/'automatic-result.json')}])
        after=holdout.get('metrics',{}).get('adapter',{});before=holdout.get('metrics',{}).get('original',{})
        phase_summary={'decision':comparison['decision'],'quality_before':before.get('passed'),'quality_after':after.get('passed'),
                       'energy_joules':after.get('energy_joules'),'vram_peak_gib':after.get('vram_peak_gib'),'latency_seconds':after.get('latency_seconds')}
        status='CANCELLED' if result['status']=='CANCELLED' else 'DONE' if result['status']=='TRAINED_CANDIDATE' else 'FAILED'
        state.set_progress('COMPLETE',status,summary=phase_summary)
        return {'status':status,'training':summary}
    except Exception as exc:

        state.set('automatic_training_rounds',[*completed,{'round':number,'recipe':number,'training_started':False,'time':time.time(),'status':'FAILED','error_type':type(exc).__name__,'report':str(workspace)}])
        raise
    finally:
        if previous_handler is not None:signal.signal(signal.SIGTERM,previous_handler)
