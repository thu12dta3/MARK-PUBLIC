
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import resource
import time
import uuid
from ..cognition.experiments import Experiment,ExperimentStore
from ..cognition.storage import read_json
from .state import State,canonical,exclusive,secure_directory
from .signals import extract_signals,propose
from .sandbox import Sandbox,ExperimentCancelled
from .code_worker import clean_revision,source_snapshot,prepare_edits,readable_diff,tree_digest
from .evaluator import evaluate,compare,reply_cases
from .budget import preflight,tree_bytes


def immutable_json(path,data):
    raw=(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


class Engine:
    def __init__(self,repo,state):self.repo=Path(repo);self.state=state
    def scan(self,report=None):
        self.state.active();self.state.policy.authorize('scan')
        version=clean_revision(self.repo)
        if report is not None:
            path=Path(report)

            if path.suffix not in {'.json','.jsonl'}:raise ValueError('Somente relatórios técnicos JSON/JSONL')
            if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024**2:raise ValueError('Fonte técnica inválida')
            raw=path.read_bytes();checksum=hashlib.sha256(raw).hexdigest()
            if path.suffix=='.jsonl':
                lines=[line for line in raw.splitlines() if line.strip()]
                if len(lines)>20000:raise ValueError('Telemetria excedeu orçamento')
                observed=duplicates=0
                for line in lines:
                    try:item=json.loads(line.decode('utf-8'))
                    except (ValueError,UnicodeDecodeError):raise ValueError('Linha técnica inválida') from None
                    if not isinstance(item,dict):raise ValueError('Linha técnica inválida')
                    line_id=hashlib.sha256(line).hexdigest()
                    if self.state.db.execute('SELECT 1 FROM imports WHERE id=?',(line_id,)).fetchone():
                        duplicates+=1;continue
                    case_id='metric-'+str(item.get('event_id') or line_id[:20])[:32]
                    rows=extract_signals({'cases':[{'id':case_id,'status':'PASS' if item.get('success') is True else 'FAIL',
                                                    'model_metrics':item}]},version)
                    for row in rows:self.state.observe(row)
                    with self.state.db:self.state.db.execute('INSERT INTO imports VALUES (?,?)',(line_id,time.time()))
                    observed+=len(rows)
                return {'observed':observed,'duplicate':observed==0,'duplicates':duplicates,'version':version,
                        'scope':'new technical metric records only'}
            if self.state.db.execute('SELECT 1 FROM imports WHERE id=?',(checksum,)).fetchone():
                return {'observed':0,'duplicate':True,'version':version}
            data=read_json(path)
            rows=extract_signals(data,version)
            for row in rows:self.state.observe(row)
            with self.state.db:self.state.db.execute('INSERT INTO imports VALUES (?,?)',(checksum,time.time()))
            return {'observed':len(rows),'version':version,'scope':'explicit technical report only'}
        sources=self.state.get('technical_sources',[])
        imported=[self.scan(path) for path in sources[:16]]
        return {'version':version,'git_clean':True,'sandbox':Sandbox(self.state.policy).probe(self.repo),
                'budget':preflight(self.state.root,self.state.policy),'backlog':len(self.state.backlog()),'imported':imported}
    def proposals(self):
        self.state.active();self.state.policy.authorize('propose');rows=[]
        for signal in self.state.backlog():
            proposal=propose(signal);rows.append(proposal)
            with self.state.db:self.state.db.execute('INSERT OR IGNORE INTO proposals VALUES (?,?,?)',
                                                     (proposal['id'],signal['id'],canonical(proposal)))
        return rows
    def experiment(self,proposal_id,edits,repeat=2):
        self.state.policy.authorize('code_experiment')
        with exclusive(self.state):
            revision=clean_revision(self.repo)
            row=self.state.db.execute('SELECT body FROM proposals WHERE id=?',(proposal_id,)).fetchone()
            if not row:raise ValueError('Proposta inexistente')
            proposal=json.loads(row[0])
            if proposal.get('experiment_kind')!='code':raise PermissionError('Proposta de revisão não autoriza alteração de código')
            job_id='JOB-'+uuid.uuid4().hex[:12]
            if self.state.db.execute('SELECT count(*) FROM jobs').fetchone()[0]>=self.state.policy.max_reports:raise RuntimeError('Limite de relatórios: revisão de retenção requerida')
            budget=preflight(self.state.root,self.state.policy)
            job=secure_directory(self.state.root/'candidates'/job_id)
            with self.state.db:self.state.db.execute('INSERT INTO jobs VALUES (?,?,?)',(job_id,'RUNNING',canonical({'pid':os.getpid(),'revision':revision})))
            started=time.monotonic();sandbox=Sandbox(self.state.policy)
            def cancelled():
                return self.state.get('cancel',False) or self.state.get('paused',False) or self.state.get('disabled',False) or time.monotonic()-started>self.state.policy.timeout_seconds
            report={'job_id':job_id,'proposal':proposal,'revision':revision,'policy_hash':self.state.policy.digest(),'budget':budget,'applied_to_production':False}
            try:
                baseline=job/'baseline';candidate=job/'candidate'
                self.state.set_progress('BASELINE',job_id=job_id)
                source_snapshot(self.repo,baseline,revision);source_snapshot(self.repo,candidate,revision)
                sandbox.probe(baseline)
                cases=reply_cases();before=evaluate(baseline,sandbox,cases,cancelled,repeat)
                self.state.finish_phase(summary={k:before[k] for k in ('passed','total','security_gate') if k in before})
                self.state.set_progress('CANDIDATE',job_id=job_id)
                patch=prepare_edits(baseline,candidate,edits)
                diff=readable_diff(baseline,candidate,patch['files'])
                fd=os.open(job/'candidate.patch',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as stream:stream.write(diff)
                after=evaluate(candidate,sandbox,cases,cancelled,repeat)
                self.state.finish_phase(summary={k:after[k] for k in ('passed','total','security_gate') if k in after})
                self.state.set_progress('EVALUATING',job_id=job_id)
                decision=compare(before,after)
                self.state.finish_phase(summary={'decision':decision['decision'],'gain':decision.get('delta_passed',0)})
                report.update({'before':before,'after':after,'comparison':decision,'candidate':patch,
                               'status':decision['decision'],'duration_seconds':time.monotonic()-started})
            except BaseException as exc:
                report.update({'status':'CANCELLED' if isinstance(exc,(ExperimentCancelled,KeyboardInterrupt)) else 'FAILED',
                               'error_type':type(exc).__name__,'duration_seconds':time.monotonic()-started})
                if isinstance(exc,(SystemExit,KeyboardInterrupt)):report['interrupted']=True
            report['storage_bytes']=tree_bytes(job)
            report['coordinator_ram_peak_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

            store=ExperimentStore(self.state.root/'experiments')
            number=max([int(item['experiment_id'].split('-')[1]) for item in store.list()] or [0])+1
            experiment_id=f'EXP-{number:04d}';report['experiment_id']=experiment_id
            experiment=Experiment(experiment_id,'NO_MODEL','NONE','Controlled response replay','evolution-0.1','CODE_EXPERIMENT','NONE',
                None,None,None,None,{'isolation':'bubblewrap+cgroup','gpu_used':False},report['duration_seconds'],None,
                report['coordinator_ram_peak_kib']/1024,None,None,
                float(report.get('after',{}).get('security_gate',False)),
                'Independent controlled response replay; no live model quality or production deployment.',
                'PASS' if report['status']=='REVIEW_REQUIRED' else 'FAIL')
            store.add(experiment)
            digest=immutable_json(job/'report.json',report)
            with self.state.db:
                self.state.db.execute('UPDATE jobs SET status=?,body=? WHERE id=?',(report['status'],canonical({'experiment_id':experiment_id,'report':str(job/'report.json'),'hash':digest}),job_id))
            self.state.event('experiment_finished',{'job':job_id,'experiment':experiment_id,'status':report['status'],'report_hash':digest})
            summary={k:report.get('after',{}).get(k) for k in ('passed','total','security_gate')}
            summary.update(decision=report['status'],gain=report.get('comparison',{}).get('delta_passed',0))
            if report.get('error_type'):summary['error_type']=report['error_type']
            self.state.set_progress('COMPLETE','CANCELLED' if report['status']=='CANCELLED' else 'FAILED' if report['status']=='FAILED' else 'DONE',job_id,summary)
            return report
    def reports(self):
        rows=[]
        for body, in self.state.db.execute('SELECT body FROM jobs ORDER BY rowid'):
            data=json.loads(body)
            if 'report' not in data:
                rows.append({'state':'INTERRUPTED_OR_RUNNING','pid':data.get('pid')});continue
            path=Path(data['report'])
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=data['hash']:raise ValueError('Relatório corrompido')
            rows.append(read_json(path))
        return rows
