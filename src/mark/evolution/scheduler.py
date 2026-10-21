
from pathlib import Path
import copy
import fcntl
import os
import sys
import time
import sqlite3
import json
import urllib.request
from ..url_security import validate_url_scheme
from .code_worker import RepositoryBusy
from .sandbox import ExperimentCancelled
from .state import secure_directory
from ..cognition.storage import private_file


def render(repo,root,output,interval_seconds=3600,coordinator=None):
    if type(interval_seconds)is not int or not 60<=interval_seconds<=86400*30:raise ValueError('Intervalo inválido')
    repo=Path(repo).absolute();root=Path(root).absolute();output=secure_directory(output)
    coordinator=Path(coordinator or Path(__file__).resolve().parents[3]).absolute()
    from ..model_state import STATE_DIR
    if any('"' in str(p) or '\n' in str(p) or '%' in str(p) for p in (repo,root,coordinator,STATE_DIR,sys.executable)):raise ValueError('Caminho inválido para unidade')
    service=f'''[Unit]
Description=MARK evolution finite opt-in cycle
[Service]
Type=oneshot
ExecStart="{sys.executable}" -I "{coordinator}/scripts/evolution_cycle.py" --repo "{repo}" --state "{root}"
Restart=no
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths="{root}" "{STATE_DIR}"
IPAddressDeny=any
IPAddressAllow=localhost
MemoryMax=2G
TasksMax=32
CPUQuota=100%
TimeoutStartSec=900
TimeoutStopSec=35
'''
    timer=f'''[Unit]
Description=MARK evolution opt-in timer (disabled until owner review)
[Timer]
OnBootSec=5min
OnUnitInactiveSec={interval_seconds}
RandomizedDelaySec=60
Persistent=false
Unit=mark-evolution.service
[Install]
WantedBy=timers.target
'''
    for name,text in [('mark-evolution.service',service),('mark-evolution.timer',timer)]:
        path=output/name
        if path.exists():raise FileExistsError('Unidade já renderizada; não sobrescrever revisão')
        path.write_text(text);path.chmod(0o600)
    return {'rendered':str(output),'installed':False,'enabled':False,'owner_review_required':True}


def _interactive_session_active():
    from ..model_state import STATE_DIR,_pid_alive
    for marker in STATE_DIR.glob('session-*'):
        parts=marker.name.split('-')
        if len(parts)>2 and parts[1].isdigit() and _pid_alive(int(parts[1])):return True
    return False


def _generate_edits(engine,proposal):

    from ..config import load_config
    from ..cognition.providers import get_provider
    from ..model_state import ModelSession
    from .code_worker import generate_edits
    cfg,_profile=resolve_evolution_model(load_config())
    cfg['ollama'].update(think=False,timeout=min(90,max(10,int(cfg['ollama'].get('timeout',90)))))
    session=ModelSession(cfg)
    try:
        session.wake()
        return generate_edits(engine.repo,proposal,get_provider(cfg))
    finally:session.sleep()


def resolve_evolution_model(cfg, installed_models=None):

    source=copy.deepcopy(cfg)
    base=source.get('ollama',{}).get('url','http://127.0.0.1:11434').rstrip('/')
    if installed_models is None:
        request=urllib.request.Request(base+'/api/tags',headers={'Accept':'application/json'})
        validate_url_scheme(request)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:  # nosec: B310
                payload=json.load(response)
            installed_models={row.get('name') for row in payload.get('models',[]) if isinstance(row,dict)}
        except (OSError,ValueError,TypeError) as exc:
            raise RuntimeError('Não consegui listar os modelos já instalados no Ollama.') from None
    installed={name.removesuffix(':latest') for name in installed_models if isinstance(name,str)}
    configured=source.get('models',{})
    for profile in ('engineer','call','chat'):
        model=configured.get(profile)
        if not isinstance(model,str) or not model.strip():
            continue
        if model.removesuffix(':latest') in installed:
            source.setdefault('ollama',{})['model']=model
            source.setdefault('ollama',{})['think']=False
            return source,profile
    raise RuntimeError('Nenhum modelo configurado para a autoevolução está instalado. Nada foi baixado.')


def cycle(engine,generate_edits_fn=None,interactive_fn=None):

    path=private_file(engine.state.root/'.cycle.lock')
    fd=os.open(path,os.O_RDWR|getattr(os,'O_NOFOLLOW',0))
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'ALREADY_RUNNING'}
        return _cycle(engine,generate_edits_fn,interactive_fn)
    finally:os.close(fd)


def _recover_stage(state,phase,operation):

    for attempt in range(3):
        if state.get('paused',False) or state.get('cancel',False):
            raise ExperimentCancelled('Parada solicitada')
        try:return operation()
        except (TimeoutError,ConnectionError,BlockingIOError,sqlite3.OperationalError) as exc:
            if isinstance(exc,sqlite3.OperationalError) and not any(word in str(exc).lower() for word in ('locked','busy')):raise
            if attempt==2:raise
            state.record_activity('recovery',phase=phase,status='RUNNING',summary={'attempt':attempt+1,'error_type':type(exc).__name__})

            time.sleep(.2*(attempt+1))


def _cycle(engine,generate_edits_fn=None,interactive_fn=None):
    state=engine.state;status=state.status()
    if status['paused'] or not status['enabled']:return {'status':'PAUSED'}
    if not state.policy.schedule_enabled:return {'status':'DISABLED_OPT_IN_REQUIRED'}
    now=time.time();last=state.get('last_cycle',0)
    backoff=state.policy.backoff_seconds if state.get('cycle_failed',False) else state.policy.min_interval_seconds
    if now-last<backoff:
        state.set_progress('WAITING','DEFERRED',wait_reason='interval')
        return {'status':'BACKOFF'}
    state.set('last_cycle',now)
    try:
        if state.get('automatic_training_contract'):
            from .automatic_training import run_round
            training=run_round(state,engine.repo,interactive_fn or _interactive_session_active)
            if training and training.get('status')!='TRAINING_SEARCH_COMPLETE' and training.get('reason')!='training_interval':
                state.set('cycle_failed',training.get('status')=='FAILED')
                state.event('automatic_training_cycle',{'status':training.get('status')})
                return training
        state.set_progress('OBSERVING');scan=_recover_stage(state,'OBSERVING',engine.scan)
        state.finish_phase(summary={'observed':sum(r.get('observed',0) for r in scan.get('imported',[]))})
        state.set_progress('PLANNING');plans=_recover_stage(state,'PLANNING',engine.proposals)
        state.finish_phase(summary={'opportunities':len(plans)})


        experiments=[]
        generate_edits_fn=generate_edits_fn or (lambda proposal:_generate_edits(engine,proposal))
        interactive_fn=interactive_fn or _interactive_session_active
        eligible=[]
        for proposal in plans:
            if proposal.get('experiment_kind')!='code':continue
            previous=state.db.execute('SELECT status,attempt_count,updated FROM scheduled_experiments WHERE proposal_id=?',(proposal['id'],)).fetchone()
            if previous is None or (previous[0] in {'GENERATION_FAILED','FAILED','CANCELLED','REJECTED'}
                                    and previous[1]<state.policy.max_attempts
                                    and now-previous[2]>=state.policy.backoff_seconds):eligible.append(proposal)
        if state.policy.scheduled_experiments and eligible:
            proposal=eligible[0]
            if interactive_fn():
                state.set_progress('PLANNING','DEFERRED')
                state.event('scheduled_experiment_deferred',{'proposal':proposal['id'],'reason':'interactive_model_session'})
            else:
                previous=state.db.execute('SELECT status,attempt_count,updated,job_id FROM scheduled_experiments WHERE proposal_id=?',(proposal['id'],)).fetchone()
                count=(previous[1]+1) if previous else 1
                if previous and previous[0]=='REJECTED' and previous[3]:
                    prior=next((r for r in engine.reports() if r.get('job_id')==previous[3]),None)
                    if prior:
                        feedback={'decision':'REJECTED','before_passed':prior.get('before',{}).get('passed'),
                                  'after_passed':prior.get('after',{}).get('passed'),'total':prior.get('before',{}).get('total')}
                        path=state.root/'candidates'/previous[3]/'candidate.patch'
                        if path.is_file() and not path.is_symlink() and path.stat().st_size<=20000:feedback['previous_patch']=path.read_text()
                        proposal=proposal|{'previous_attempt':feedback}
                with state.db:state.db.execute('''INSERT INTO scheduled_experiments(proposal_id,status,error_type,job_id,attempt_count,updated)
                    VALUES (?,? ,NULL,NULL,?,?) ON CONFLICT(proposal_id) DO UPDATE SET status=excluded.status,
                    error_type=NULL,attempt_count=excluded.attempt_count,updated=excluded.updated''',
                                               (proposal['id'],'GENERATING',count,now))
                try:
                    state.set_progress('GENERATING')
                    edits=generate_edits_fn(proposal)
                    state.finish_phase(summary={'edits':len(edits) if isinstance(edits,list) else 0})
                    state.set_progress('BASELINE')
                    report=engine.experiment(proposal['id'],edits)
                    with state.db:state.db.execute('UPDATE scheduled_experiments SET status=?,job_id=?,updated=? WHERE proposal_id=?',
                                                   (report['status'],report.get('job_id'),now,proposal['id']))
                    experiments.append(report)
                except Exception as exc:
                    interrupted=state.get('paused',False) or state.get('cancel',False) or state.get('disabled',False)
                    phase=state.get('progress',{}).get('phase','GENERATING')
                    failed_status='CANCELLED' if interrupted else 'GENERATION_FAILED' if phase=='GENERATING' else 'FAILED'
                    state.set_progress(phase,'CANCELLED' if interrupted else 'FAILED',summary={'error_type':type(exc).__name__})
                    with state.db:state.db.execute('UPDATE scheduled_experiments SET status=?,error_type=?,updated=? WHERE proposal_id=?',
                                                   (failed_status,type(exc).__name__,now,proposal['id']))
                    state.event('scheduled_experiment_failure',{'proposal':proposal['id'],'error_type':type(exc).__name__})
                    experiments.append({'proposal_id':proposal['id'],'status':failed_status,'error_type':type(exc).__name__})
        failed=any(r['status'] in {'FAILED','GENERATION_FAILED'} for r in experiments)
        cancelled=any(r['status']=='CANCELLED' for r in experiments)
        if state.progress().get('status') not in {'DEFERRED','FAILED','CANCELLED','DONE'}:
            state.set_progress('COMPLETE','FAILED' if failed else 'DONE',summary={'opportunities':len(plans),'experiments':len(experiments)})
        state.set('cycle_failed',failed);state.event('scheduled_cycle',{'proposals':len(plans),'experiments':len(experiments)})
        return {'status':'CANCELLED' if cancelled else 'FAILED' if failed else 'DEFERRED' if state.progress().get('status')=='DEFERRED' else 'PROPOSED',
                'scan':scan,'proposals':plans,'experiments_executed':len(experiments),'experiments':experiments}
    except RepositoryBusy:
        state.finish_phase('DEFERRED',{'error_type':'RepositoryBusy'})
        state.set_progress('WAITING','DEFERRED',wait_reason='repository')
        state.set('cycle_failed',False)
        state.event('scheduled_cycle_deferred',{'reason':'repository'})
        return {'status':'DEFERRED','reason':'repository'}
    except Exception as exc:
        interrupted=state.get('paused',False) or state.get('cancel',False) or state.get('disabled',False)
        state.set_progress(state.get('progress',{}).get('phase','OBSERVING'),'CANCELLED' if interrupted else 'FAILED',summary={'error_type':type(exc).__name__})
        state.set('cycle_failed',not interrupted);state.event('scheduled_failure',{'error_type':type(exc).__name__})
        return {'status':'CANCELLED' if interrupted else 'FAILED','error_type':type(exc).__name__}
