
import hashlib
import json
from pathlib import Path
from ..cognition.training import validate_paths
from ..cognition.storage import read_json
from .budget import tree_bytes,preflight
from .state import secure_directory
from .engine import immutable_json
from .promotion import approval,digest


def prepare(config,dataset_approval,baseline,base,splits,output,policy,holdout=None):
    cfg,rows,validated=validate_paths(Path(config),Path(dataset_approval),Path(baseline),Path(base),Path(splits))
    base_size=tree_bytes(base)
    reviewed=read_json(Path(dataset_approval))
    if reviewed.get('reviewed_by')!='owner':raise PermissionError('Dataset sem revisão humana')
    output=secure_directory(output)


    estimated=512*1024**2
    budget=preflight(output,policy,estimated_temporary=estimated)
    plan={'schema_version':1,'executor':'hf-qlora-v02','config':str(Path(config).absolute()),
          'config_hash':digest(Path(config).read_bytes()),'dataset_approval':str(Path(dataset_approval).absolute()),
          'dataset_hash':validated['dataset_sha256'],'baseline':str(Path(baseline).absolute()),
          'baseline_hash':digest(Path(baseline).read_bytes()),'base':str(Path(base).absolute()),
          'base_revision':validated['base_revision'],'base_preexisting_read_only_bytes':base_size,
          'splits':str(Path(splits).absolute()),'counts':validated['counts'],'budget':budget,
          'adapter_budget_bytes':policy.adapter_bytes,'vram_budget_gib':policy.vram_gib,
          'max_seconds':300,'automatic_activation':False,'training_started':False,
          'created_at':__import__('time').time(),'python':str(Path.home()/'.local/share/mark/training-env/bin/python'),
          'memory_mib':policy.memory_mib,'gpu_free_gib':None,
          'trainer_hash':digest((Path(__file__).resolve().parents[1]/'cognition/training.py').read_bytes()),
          'harness_hash':digest((Path(__file__).parent/'training_harness.py').read_bytes()),
          'approved_dataset':str(Path(read_json(Path(splits)/'report.json')['approved_path']).absolute()),
          'requires':'Fresh owner authorization tied to this plan; local CUDA/dependencies preflight; idle ModelSession'}
    holdout=Path(holdout) if holdout else None
    if holdout and holdout.is_file():
        plan['holdout']=str(holdout);plan['holdout_hash']=digest(holdout.read_bytes())
    key=immutable_json(output/'training-plan.json',plan)
    return {'plan':str(output/'training-plan.json'),'approval_hash':key,'training_started':False,
            'temporary_estimate_bytes':estimated,'base_preexisting_bytes':base_size}


def validate_activation(plan_path,receipt,gpu_free_gib,interactive_active=False):
    path=Path(plan_path);plan=read_json(path)
    approval(receipt,'training',digest(path.read_bytes()))
    if interactive_active:raise PermissionError('Uso interativo tem prioridade; treinamento adiado')
    if __import__('time').time()-plan['created_at']>86400:raise PermissionError('Plano expirou: refaça baseline/preflight')
    if gpu_free_gib is None or gpu_free_gib<max(5,plan['vram_budget_gib']):raise RuntimeError('VRAM insuficiente ou CUDA ausente')
    if digest(Path(plan['config']).read_bytes())!=plan['config_hash'] or digest(Path(plan['baseline']).read_bytes())!=plan['baseline_hash']:
        raise ValueError('Inputs alterados após revisão')
    validate_paths(Path(plan['config']),Path(plan['dataset_approval']),Path(plan['baseline']),Path(plan['base']),Path(plan['splits']))
    return plan


def sandbox_command(plan,repo,workspace,probe=False):

    import sys,os,subprocess
    venv=Path(plan['python']).parent.parent
    details=json.loads(subprocess.check_output([plan['python'],'-I','-c',
        'import sys,json;print(json.dumps({"base":sys.base_prefix}))'],timeout=10))
    python_base=Path(details['base'])
    args=['/usr/bin/systemd-run','--user','--quiet','--scope','--collect','-p',f'MemoryMax={plan["memory_mib"]}M',
          '-p','MemorySwapMax=0','-p','TasksMax=128','-p','CPUQuota=200%',
          '/usr/bin/bwrap','--unshare-all','--unshare-user','--disable-userns','--die-with-parent','--new-session','--cap-drop','ALL',
          '--clearenv','--setenv','HOME','/home/mark','--setenv','USER','mark','--setenv','LOGNAME','mark',
          '--setenv','TORCHINDUCTOR_CACHE_DIR','/tmp/torchinductor','--setenv','TORCH_HOME','/tmp/torch',  # nosec: B108
          '--setenv','PATH','/usr/bin:/bin',
          '--setenv','HF_HUB_OFFLINE','1','--setenv','TRANSFORMERS_OFFLINE','1','--setenv','HF_HUB_DISABLE_TELEMETRY','1',
          '--setenv','LANG','C.UTF-8','--ro-bind','/usr','/usr','--symlink','usr/bin','/bin',
          '--symlink','usr/lib','/lib','--symlink','usr/lib','/lib64','--proc','/proc','--dev','/dev',
          '--tmpfs','/tmp','--ro-bind','/sys','/sys','--tmpfs','/dev/shm',  # nosec: B108
          '--dir','/home/mark','--ro-bind',str(venv),str(venv),'--ro-bind',str(python_base),str(python_base),
          '--ro-bind',str(repo/'src'),'/trusted/src','--ro-bind',str(Path(__file__).parent/'training_harness.py'),'/runner/train.py',
          '--ro-bind',str(workspace/'inputs'),'/inputs','--ro-bind',str(workspace/'control'),'/control',
          '--bind',str(workspace/'output'),'/output']
    for source in [Path(plan['base']),Path(plan['splits']),Path(plan['config']),Path(plan['dataset_approval']),Path(plan['baseline']),Path(plan['approved_dataset'])]:
        if source.is_symlink():raise PermissionError('Input de treinamento symlink')
        args+=['--ro-bind',str(source),str(source)]
    if plan.get('holdout'):
        holdout=Path(plan['holdout'])
        if holdout.is_symlink() or digest(holdout.read_bytes())!=plan['holdout_hash']:raise ValueError('Holdout mudou após revisão')
        args+=['--ro-bind',str(holdout),str(holdout)]
    for name in ('nvidia0','nvidiactl','nvidia-uvm'):
        device=Path('/dev')/name
        if not device.exists():raise RuntimeError('Dispositivo CUDA ausente')
        args+=['--dev-bind',str(device),str(device)]
    args+=['--chdir','/tmp',plan['python'],'-I','/runner/train.py']  # nosec: B108
    if probe:args.append('--probe')
    return args


def run_isolated(plan_path,receipt,repo,output,policy,cancel=lambda:False,interactive=lambda:False,probe=False,progress=None):
    import os,subprocess,time,signal

    plan=read_json(Path(plan_path))
    if interactive():raise PermissionError('Sessão interativa ativa: laboratório adiado')
    workspace=secure_directory(output)
    if not probe:
        import subprocess
        gpu=subprocess.check_output([plan['python'],'-I','-c','import torch,json;print(json.dumps({"free":torch.cuda.mem_get_info()[0]/1024**3 if torch.cuda.is_available() else None}))'],timeout=30)
        validate_activation(plan_path,receipt,json.loads(gpu)['free'],interactive())
    if digest((Path(__file__).parent/'training_harness.py').read_bytes())!=plan.get('harness_hash'):raise ValueError('Harness mudou depois da revisão')
    if digest((Path(repo)/'src/mark/cognition/training.py').read_bytes())!=plan['trainer_hash']:raise ValueError('Treinador mudou depois da revisão')
    preflight(workspace,policy,plan['budget']['temporary_estimated_bytes'])
    for name in ('inputs','control','output'):secure_directory(workspace/name)
    immutable_json(workspace/'inputs/plan.json',plan)
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}',
         'DBUS_SESSION_BUS_ADDRESS':f'unix:path=/run/user/{os.getuid()}/bus'}
    out=workspace/'training-output.txt'
    fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    start=time.monotonic();cancelled=False;last_progress=None
    with os.fdopen(fd,'wb') as log:
        process=subprocess.Popen(sandbox_command(plan,Path(repo),workspace,probe),stdout=log,stderr=log,env=env,start_new_session=True)
        try:
            while process.poll() is None:
                status_file=workspace/'output/status.json'
                if progress and status_file.is_file():
                    try:
                        observed=read_json(status_file);key=(observed.get('state'),observed.get('step',0))
                    except (ValueError,OSError):key=None
                    if key and key!=last_progress:
                        progress(observed);last_progress=key
                elapsed=time.monotonic()-start
                if cancel() or interactive() or elapsed>plan['max_seconds'] or tree_bytes(workspace)>policy.temporary_bytes:
                    (workspace/'control/cancel').touch(mode=0o600);cancelled=True
                if elapsed>plan['max_seconds']+30:raise TimeoutError('Treinador não encerrou dentro da margem de checkpoint')
                time.sleep(.2)
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=10)
    status_path=workspace/'output/status.json'
    status=read_json(status_path) if status_path.exists() else {'state':'NO_CHECKPOINT_CREATED'}
    result={'status':'PROBE' if probe and process.returncode==0 else 'CANCELLED' if cancelled else status['state'],
            'returncode':process.returncode,'duration_seconds':time.monotonic()-start,'training_started':not probe and bool(status.get('training_started')),
            'output_bytes':tree_bytes(workspace),'checkpoint_state':status.get('state'),
            'vram_peak_gib':status.get('vram_peak_gib'),'automatic_activation':False}
    if probe and process.returncode==0:
        lines=out.read_text(errors='replace').splitlines()
        measurements=[json.loads(line) for line in lines if line.startswith('{')]
        if not measurements or any(measurements[-1].get(key) is not True for key in ('cuda_available','cuda_operation','gpu_energy_available')) or not all(measurements[-1].get('dependencies',{}).values()):
            result['status']='PROBE_FAILED';result['cuda_available']=False
        else:result['cuda_available']=True
    if status.get('state')=='TRAINED_CANDIDATE':
        before=status.get('before',{});after=status.get('after',{})
        result['adapter_loss_gate']=bool(before) and all(after.get(k,float('inf'))<=v for k,v in before.items())
        result['decision']='REJECTED' if not result['adapter_loss_gate'] else 'INDEPENDENT_HOLDOUT_AND_OWNER_REVIEW_REQUIRED'
    immutable_json(workspace/'lab-result.json',result)
    return result
