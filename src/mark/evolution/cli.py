
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import subprocess
from dataclasses import replace
from .state import State,DEFAULT_ROOT
from .engine import Engine
from .policy import Policy
from ..cognition.storage import read_json

ROOT=Path(__file__).resolve().parents[3]


def manage_schedule(state,enabled,units=None,runner=None):
    units=Path(units or Path.home()/'.config/systemd/user')
    required=[units/'mark-evolution.service',units/'mark-evolution.timer']
    if any(not p.is_file() or p.is_symlink() for p in required):raise ValueError('Unidades do timer não estão instaladas')
    operation=['enable','--now'] if enabled else ['disable','--now']
    previous=state.policy
    policy=replace(state.policy,schedule_enabled=bool(enabled),scheduled_experiments=bool(enabled))
    state.set('policy',asdict(policy));state.policy=policy
    try:
        (runner or subprocess.run)(['/usr/bin/systemctl','--user',*operation,'mark-evolution.timer'],check=True,timeout=30,
                                  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    except Exception:
        state.set('policy',asdict(previous));state.policy=previous
        raise
    state.event('owner_schedule_control',{'enabled':bool(enabled)})
    return {'enabled':bool(enabled),'schedule_enabled':policy.schedule_enabled,
            'scheduled_experiments':policy.scheduled_experiments,'status':state.status()}


def control_run(state,action,units=None,runner=None):

    if action not in {'start','stop'}:raise ValueError('Controle inválido')
    run=runner or subprocess.run
    previous=state.policy
    settings={key:state.get(key,False) for key in ('paused','disabled','cancel')}
    state.record_activity('control',action=action)
    try:
        if action=='start':
            state.control('resume');state.set('cancel',False)
            manage_schedule(state,True,units,run)
            run(['/usr/bin/systemctl','--user','start','--no-block','mark-evolution.service'],check=True,
                timeout=30,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        else:
            state.control('pause');state.control('cancel')
            manage_schedule(state,False,units,run)
            run(['/usr/bin/systemctl','--user','stop','--no-block','mark-evolution.service'],check=True,
                timeout=30,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    except Exception as exc:
        if action=='start':
            try:manage_schedule(state,previous.schedule_enabled,units,run)
            except Exception:pass
            state.set('policy',asdict(previous));state.policy=previous
            for key,value in settings.items():state.set(key,value)
        state.record_activity('control',action=action,status='FAILED',summary={'error_type':type(exc).__name__})
        raise
    return {'action':action,'status':state.status()}


def parser():
    p=argparse.ArgumentParser(prog='MARK evolve')
    p.add_argument('--state',type=Path,default=DEFAULT_ROOT);p.add_argument('--repo',type=Path,default=ROOT)
    p.add_argument('--policy-file',type=Path,help='Política administrativa validada; usada somente ao criar estado novo')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('status','backlog','propose','history','policy','pause','resume','cancel','disable','cycle'):sub.add_parser(name)
    scan=sub.add_parser('scan');scan.add_argument('--report',type=Path)
    exp=sub.add_parser('experiment');exp.add_argument('proposal');exp.add_argument('--edits',type=Path);exp.add_argument('--generate',action='store_true');exp.add_argument('--repeat',type=int,default=2)
    watch=sub.add_parser('watch');watch.add_argument('file',type=Path);watch.add_argument('--consent',choices=['owner'],required=True)
    report=sub.add_parser('report');report.add_argument('--job')
    skill=sub.add_parser('skill');s=skill.add_subparsers(dest='action',required=True)
    s.add_parser('stats');query=s.add_parser('search');query.add_argument('query')
    add=s.add_parser('add');add.add_argument('file',type=Path);add.add_argument('--consent',choices=['owner'],required=True)
    schedule=sub.add_parser('schedule');mode=schedule.add_mutually_exclusive_group();mode.add_argument('--enable',action='store_true');mode.add_argument('--disable',action='store_true')
    schedule.add_argument('--output',type=Path);schedule.add_argument('--interval',type=int,default=3600);schedule.add_argument('--coordinator',type=Path,default=ROOT)
    release=sub.add_parser('release');release.add_argument('report',type=Path);release.add_argument('--runtime',type=Path,required=True);release.add_argument('--output',type=Path,required=True)
    for name in ('promote','rollback'):
        deploy=sub.add_parser(name);deploy.add_argument('release',type=Path);deploy.add_argument('--approval',type=Path,required=True);deploy.add_argument('--runtime-stopped',action='store_true')
    lab=sub.add_parser('training-plan')
    for name in ('config','dataset-approval','baseline','base','splits','output'):lab.add_argument('--'+name,type=Path,required=True)
    lab.add_argument('--holdout',type=Path)
    trainer=sub.add_parser('train');trainer.add_argument('--plan',type=Path,required=True);trainer.add_argument('--approval',type=Path);trainer.add_argument('--output',type=Path,required=True);trainer.add_argument('--probe',action='store_true')
    github=sub.add_parser('publish');github.add_argument('--checkout',type=Path,required=True);github.add_argument('--branch',required=True);github.add_argument('--report',type=Path,required=True);github.add_argument('--approval',type=Path,required=True)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        policy=Policy(**read_json(args.policy_file)) if args.policy_file else None
        with State(args.state,policy) as state:
            engine=Engine(args.repo,state)
            if args.command=='status':result=state.status()
            elif args.command in {'pause','resume','cancel','disable'}:state.control(args.command);result=state.status()
            elif args.command=='policy':result=asdict(state.policy)|{'hash':state.policy.digest(),'blocked_actions':'fixed in trusted coordinator; model cannot edit'}
            elif args.command=='scan':result=engine.scan(args.report)
            elif args.command=='backlog':result=state.backlog()
            elif args.command=='propose':result=engine.proposals()
            elif args.command=='watch':
                if args.file.suffix not in {'.json','.jsonl'} or args.file.is_symlink():raise ValueError('Fonte técnica inválida')
                values=state.get('technical_sources',[])
                path=str(args.file.absolute())
                if path not in values:
                    if len(values)>=16:raise ValueError('Limite de fontes técnicas')
                    values.append(path);state.set('technical_sources',values)
                state.event('owner_technical_source',{'sources':len(values)})
                result={'registered':path,'schedule_enabled':state.policy.schedule_enabled}
            elif args.command=='experiment':
                if args.generate:
                    from ..config import DEFAULTS
                    from ..cognition.providers import get_provider
                    from ..model_state import ModelSession,STATE_DIR,_pid_alive
                    from .code_worker import generate_edits
                    import copy
                    if any(_pid_alive(int(p.name.split('-')[1])) for p in STATE_DIR.glob('session-*') if p.name.split('-')[1].isdigit()):raise PermissionError('Sessão interativa ativa: geração adiada')
                    row=state.db.execute('SELECT body FROM proposals WHERE id=?',(args.proposal,)).fetchone()
                    if not row:raise ValueError('Proposta ausente')
                    cfg=copy.deepcopy(DEFAULTS);cfg['ollama'].update(model=cfg['models']['call'],think=False,timeout=90)
                    session=ModelSession(cfg)
                    try:session.wake();edits=generate_edits(args.repo,json.loads(row[0]),get_provider(cfg))
                    finally:session.sleep()
                elif args.edits:edits=read_json(args.edits)['edits']
                else:raise ValueError('Forneça --edits ou --generate')
                result=engine.experiment(args.proposal,edits,args.repeat)
            elif args.command=='report':result=[r for r in engine.reports() if not args.job or r.get('job_id')==args.job]
            elif args.command=='history':
                state.verify_audit();result=[json.loads(row[0]) for row in state.db.execute('SELECT body FROM audit ORDER BY seq')]
            elif args.command=='skill':
                from .skills import SkillStore
                state.active();state.policy.authorize('learn_skill')
                with SkillStore(state.root/'skills.sqlite3',state.policy.skills_bytes) as skills:
                    if args.action=='add':result=skills.add(**read_json(args.file),consent=args.consent=='owner');state.event('owner_skill',result)
                    elif args.action=='search':result=skills.retrieve(args.query)
                    else:result=skills.stats()
            elif args.command=='schedule':
                if args.enable or args.disable:
                    result=manage_schedule(state,args.enable)
                else:
                    if args.output is None:raise ValueError('Forneça --output, --enable ou --disable')
                    from .scheduler import render
                    result=render(args.repo,args.state,args.output,args.interval,args.coordinator)
            elif args.command=='cycle':
                from .scheduler import cycle
                result=cycle(engine)
            elif args.command=='release':
                from .promotion import prepare_release
                result=prepare_release(args.report,args.runtime,args.output,state)
            elif args.command in {'promote','rollback'}:
                from .promotion import apply_release
                state.policy.authorize('deploy' if args.command=='promote' else 'rollback',owner_approved=True)
                result=apply_release(args.release,args.approval,'deploy' if args.command=='promote' else 'rollback',args.runtime_stopped)
                state.event('owner_release',result)
            elif args.command=='training-plan':
                from .model_lab import prepare
                result=prepare(args.config,args.dataset_approval,args.baseline,args.base,args.splits,args.output,state.policy,args.holdout)
            elif args.command=='train':
                state.active()
                from .model_lab import run_isolated
                from ..model_state import STATE_DIR,_pid_alive
                def interactive():
                    return any(_pid_alive(int(p.name.split('-')[1])) for p in STATE_DIR.glob('session-*') if p.name.split('-')[1].isdigit())
                result=run_isolated(args.plan,args.approval,args.repo,args.output,state.policy,lambda:state.get('cancel',False) or state.get('paused',False),interactive,args.probe)
                state.event('training_lab_result',{k:v for k,v in result.items() if k!='output'})
            elif args.command=='publish':
                from .github_broker import publish_prepared
                result=publish_prepared(args.checkout,args.branch,args.report,args.approval)
            print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
            return 1 if args.command=='cycle' and result.get('status')=='FAILED' else 0
    except (OSError,ValueError,RuntimeError,PermissionError) as exc:
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__,'reason':str(exc)[:500]},ensure_ascii=False),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
