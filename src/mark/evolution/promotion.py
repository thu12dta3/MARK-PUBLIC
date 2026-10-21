
import hashlib
import json
from pathlib import Path
import shutil
from .state import secure_directory
from .policy import candidate_path
from .engine import immutable_json
from ..cognition.storage import read_json


def digest(data):return hashlib.sha256(data).hexdigest()


def approval(receipt,operation,target_hash):
    if receipt is None:raise PermissionError('Autorização específica do proprietário requerida')
    data=read_json(Path(receipt))
    if data!={'approved_by':'owner','operation':operation,'target_hash':target_hash,'approved':True}:
        raise PermissionError('Aprovação específica ausente ou não corresponde ao resultado revisado')
    return True


def prepare_release(report_path,runtime,output,state):
    expected=next((row for row in state.db.execute("SELECT body FROM jobs WHERE status='REVIEW_REQUIRED'") if json.loads(row[0]).get("report")==str(Path(report_path))),None)
    if expected is None or json.loads(expected[0])["hash"]!=digest(Path(report_path).read_bytes()):raise PermissionError("Relatório não está no histórico confiável de avaliação")
    report=read_json(Path(report_path));runtime=Path(runtime);output=secure_directory(output)
    if report.get('status')!='REVIEW_REQUIRED' or report.get('comparison',{}).get('promotion_authorized') is not False:
        raise PermissionError('Resultado não elegível para revisão de release')
    candidate=Path(report_path).parent/'candidate'
    from .code_worker import tree_digest
    if tree_digest(candidate)!=report['candidate']['digest']:raise ValueError('Código mudou depois da avaliação')
    entries=[]
    for name in report['candidate']['files']:
        name=str(candidate_path(name));target=runtime/name;new=(candidate/name).read_bytes()
        if target.is_symlink():raise PermissionError('Destino symlink')
        old=target.read_bytes() if target.exists() else None
        archived=output/'backup'/name;archived.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if old is not None:
            if archived.exists():raise FileExistsError('Backup já existe')
            archived.write_bytes(old);archived.chmod(0o600)
        prepared=output/'candidate'/name;prepared.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if prepared.exists():raise FileExistsError('Release já preparado')
        prepared.write_bytes(new);prepared.chmod(0o600)
        entries.append({'path':name,'before':digest(old) if old is not None else None,'after':digest(new)})
    manifest={'schema_version':1,'runtime':str(runtime.absolute()),'report_hash':digest(Path(report_path).read_bytes()),
              'candidate_digest':report['candidate']['digest'],'entries':entries,'deployed':False}
    key=immutable_json(output/'manifest.json',manifest)
    return {'release':str(output),'approval_hash':key,'review_required':True,'production_changed':False}


def apply_release(release,receipt,operation='deploy',runtime_stopped=False):
    release=Path(release);manifest_path=release/'manifest.json';manifest=read_json(manifest_path)
    approval(receipt,operation,digest(manifest_path.read_bytes()))
    if runtime_stopped is not True:raise PermissionError('Pare o runtime por procedimento administrativo antes de trocar código')
    if Path(manifest['runtime']).absolute()==Path.home()/'MARK':
        import subprocess
        for unit in ('mark-mobile.service','mark-voice.service'):
            if subprocess.run(['systemctl','--user','is-active','--quiet',unit],timeout=5).returncode==0:raise PermissionError('Serviço operacional ainda ativo; promoção bloqueada')
    if operation not in {'deploy','rollback'}:raise ValueError('Operação administrativa inválida')

    root=Path(manifest['runtime'])
    for entry in manifest['entries']:
        name=str(candidate_path(entry['path']));target=root/name
        for parent in [root,*target.relative_to(root).parents]:
            if (parent if parent.is_absolute() else root/parent).is_symlink():raise PermissionError('Destino com symlink')
        if target.is_symlink():raise PermissionError('Destino symlink')
        expected=entry['before'] if operation=='deploy' else entry['after']
        actual=digest(target.read_bytes()) if target.exists() else None
        if actual!=expected:raise ValueError('Mudança local no runtime: restauração não pode sobrescrevê-la')
        source=release/('candidate' if operation=='deploy' else 'backup')/name
        source_hash=entry['after'] if operation=='deploy' else entry['before']
        if source_hash is not None and (source.is_symlink() or digest(source.read_bytes())!=source_hash):raise ValueError('Artefato/backup corrompido')
    for entry in manifest['entries']:
        name=entry['path'];target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
        source=release/('candidate' if operation=='deploy' else 'backup')/name
        source_hash=entry['after'] if operation=='deploy' else entry['before']
        if source_hash is None:target.unlink()
        else:
            import os,tempfile
            fd,temp=tempfile.mkstemp(prefix='.mark-release-',dir=target.parent)
            with os.fdopen(fd,'wb') as stream:stream.write(source.read_bytes())
            os.chmod(temp,0o644);os.replace(temp,target)
    immutable_json(release/(operation+'-receipt.json'),{'operation':operation,'manifest_hash':digest(manifest_path.read_bytes())})
    return {'operation':operation,'completed':True,'backups_retained':True,'restart_services':'manual administrative procedure'}
