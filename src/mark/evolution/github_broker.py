
import json
from pathlib import Path
import re
import subprocess
from ..github import _gh
from .promotion import approval,digest
from ..cognition.storage import read_json

REPO='thu12dta3/MARK'


def existing_pr(branch):
    if not re.fullmatch(r'evolution/EXP-\d{4}-[a-z0-9-]{1,50}',branch):raise ValueError('Branch de evolução inválido')
    result=json.loads(_gh(['pr','list','--repo',REPO,'--state','open','--head',branch,'--json','url'],timeout=20))
    if len(result)>1:raise RuntimeError('PRs duplicados; revisão administrativa requerida')
    return result[0]['url'] if result else None


def publish_prepared(repo,branch,report_path,receipt):
    report_path=Path(report_path);approval(receipt,'publish_pr',digest(report_path.read_bytes()))
    report=read_json(report_path)
    if report.get('status')!='REVIEW_REQUIRED':raise PermissionError('Experimento rejeitado não pode virar PR de promoção')
    found=existing_pr(branch)
    if found:return {'url':found,'created':False}
    from .code_worker import clean_revision,git
    clean_revision(repo)
    if git(['branch','--show-current'],repo)!=branch:raise ValueError('Checkout não está no branch aprovado')
    remote=git(['remote','get-url','origin'],repo)
    if remote not in {'https://github.com/thu12dta3/MARK.git','git@github.com:thu12dta3/MARK.git'}:raise PermissionError('Remote fora do escopo')

    names=git(['diff','--name-only',report['revision'],'HEAD'],repo).splitlines()
    if sorted(names)!=sorted(report['candidate']['files']):raise PermissionError('Diff diverge dos arquivos aprovados')
    for name in names:
        if digest((Path(repo)/name).read_bytes())!=digest((report_path.parent/'candidate'/name).read_bytes()):
            raise ValueError('Conteúdo do branch diverge do candidato medido')
    subprocess.run(['git','-C',str(repo),'push','origin','HEAD:refs/heads/'+branch],check=True,timeout=60,capture_output=True)
    body=report_path.parent/'pr-body.txt'
    body.write_text('Experimento '+report['experiment_id']+'\n\nHipótese: '+report['proposal']['hypothesis']+
                    '\nDecisão: revisão obrigatória; sem deploy/merge automático.\n'+json.dumps(report['comparison'],ensure_ascii=False))
    body.chmod(0o600)
    url=_gh(['pr','create','--repo',REPO,'--base','main','--head',branch,'--draft','--title',
             report['experiment_id']+' · recuperação de resposta final','--body-file',str(body)],timeout=60).strip()
    return {'url':url,'created':True,'draft':True,'merge':False}
