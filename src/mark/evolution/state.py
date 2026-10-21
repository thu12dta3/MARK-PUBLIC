
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid
import math
import re
from ..cognition.storage import private_file
from .policy import Policy
from .activity import PROGRESS_STAGES,phase_report_text

DEFAULT_ROOT = Path.home()/'.local/state/mark/evolution'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def secure_directory(path):
    path=Path(path).absolute()
    for part in [*reversed(path.parents),path]:
        if part.exists() and part.is_symlink(): raise PermissionError('Pasta com symlink recusada')
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid(): raise PermissionError('Pasta privada inválida')
    path.chmod(0o700)
    return path


class State:
    def __init__(self,root=DEFAULT_ROOT,policy=None):
        self.root=secure_directory(root)
        self.policy=policy or Policy()
        self.path=private_file(self.root/'engine.sqlite3')
        self.db=sqlite3.connect(self.path,timeout=5)
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA max_page_count='+str(max(1,(self.policy.metadata_bytes-self.phase_bytes())//4096)))
            version=self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1):raise ValueError('Banco requer migração')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY,body TEXT NOT NULL,count INTEGER NOT NULL,status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,signal_id TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scheduled_experiments(proposal_id TEXT PRIMARY KEY,status TEXT NOT NULL,
                    error_type TEXT,job_id TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,body TEXT NOT NULL,previous TEXT NOT NULL,digest TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit immutable'); END;
                PRAGMA user_version=1;
            ''')
            if self.get('policy') is None:
                self.set('policy',asdict(self.policy));self.event('policy_initialized',{'hash':self.policy.digest()})
            else:self.policy=Policy(**self.get('policy'))
            columns={row[1] for row in self.db.execute('PRAGMA table_info(scheduled_experiments)')}
            if 'attempt_count' not in columns:
                self.db.execute('ALTER TABLE scheduled_experiments ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0')
            self.db.execute('PRAGMA max_page_count='+str(max(1,(self.policy.metadata_bytes-self.phase_bytes())//4096)))
            self.verify_audit()
        except BaseException:self.db.close();raise

    def close(self):self.db.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    def get(self,key,default=None):
        row=self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set(self,key,value):
        if len(canonical(value).encode())>16000:raise ValueError('Registro grande demais')
        with self.db:self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,canonical(value)))
    def event(self,kind,data):
        payload=canonical({'time':time.time(),'kind':kind,'data':data})
        if len(payload.encode())>16000:raise ValueError('Evento grande demais')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            previous=self.db.execute('SELECT digest FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
            previous=previous[0] if previous else '0'*64
            digest=hashlib.sha256((previous+payload).encode()).hexdigest()
            self.db.execute('INSERT INTO audit(body,previous,digest) VALUES (?,?,?)',(payload,previous,digest))
        return digest
    def verify_audit(self):
        previous='0'*64
        for body,prev,digest in self.db.execute('SELECT body,previous,digest FROM audit ORDER BY seq'):
            if previous!=prev or hashlib.sha256((prev+body).encode()).hexdigest()!=digest:
                raise ValueError('Histórico técnico corrompido')
            previous=digest
        return previous
    def active(self):
        if not self.policy.enabled or self.get('paused',False):raise PermissionError('Evolução pausada ou desabilitada')
    def control(self,action):
        if action not in {'pause','resume','cancel','disable'}:raise ValueError('Controle inválido')
        if action!='cancel':self.set('paused',action in {'pause','disable'})
        if action=='cancel':self.set('cancel',True)
        if action=='disable':self.set('disabled',True)
        if action=='resume':self.set('disabled',False)
        self.event('owner_control',{'action':action})
    def backlog(self):
        return [json.loads(body)|{'occurrences':count,'status':status} for body,count,status in self.db.execute('SELECT body,count,status FROM signals ORDER BY id')]
    def progress(self):
        value=self.get('progress',{})
        if value.get('status')=='RUNNING':
            from ..model_state import _pid_alive
            if not _pid_alive(value.get('pid',0)):
                value=value|{'status':'INTERRUPTED'}
        return value
    def record_activity(self,kind,**values):
        if kind not in {'phase_started','phase_finished','control','waiting','recovery','training_step'}:raise ValueError('Atividade inválida')
        if 'phase' in values and values['phase'] not in PROGRESS_STAGES:raise ValueError('Etapa inválida')
        if 'status' in values and values['status'] not in {'RUNNING','DONE','FAILED','DEFERRED','CANCELLED'}:raise ValueError('Resultado inválido')
        if 'action' in values and values['action'] not in {'start','stop'}:raise ValueError('Controle inválido')
        if 'reason' in values and values['reason'] not in {'interval','interactive','repository','training_interval'}:raise ValueError('Espera inválida')
        if 'duration_seconds' in values and (type(values['duration_seconds'])not in (float,int) or not math.isfinite(values['duration_seconds']) or not 0<=values['duration_seconds']<=10**9):raise ValueError('Duração inválida')
        record={'id':uuid.uuid4().hex,'time':time.time(),'kind':kind}
        for key in ('phase','status','action','reason','duration_seconds'):
            if key in values:record[key]=values[key]
        summary={}
        for key,value in values.get('summary',{}).items():
            if key in {'observed','opportunities','edits','experiments','passed','total','gain','attempt','completed_steps','samples'} and type(value)is int and abs(value)<=10**9:summary[key]=value
            elif key in {'energy_joules','vram_peak_gib','latency_seconds','quality_before','quality_after'} and type(value)in (int,float) and math.isfinite(value) and 0<=value<=10**9:summary[key]=value
            elif key=='security_gate' and type(value)is bool:summary[key]=value
            elif key=='decision' and isinstance(value,str) and value in {'REVIEW_REQUIRED','REJECTED','FAILED','CANCELLED'}:summary[key]=value
            elif key=='error_type' and isinstance(value,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',value):summary[key]=value
        if summary:record['summary']=summary
        if kind=='phase_finished':
            folder=secure_directory(self.root/'phase-reports')
            text=(phase_report_text(record)+'\n').encode()
            used=self.phase_bytes();available=self.policy.metadata_bytes-used-len(text)
            if used+len(text)>self.policy.metadata_bytes//4 or self.path.stat().st_size>available:raise RuntimeError('Limite de relatórios de etapa: revisão de retenção requerida')
            self.db.execute('PRAGMA max_page_count='+str(max(1,available//4096)))
            filename=record['id']+'.txt'
            fd=os.open(folder/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(text)
            record['report_file']=filename
        self.event('evolution_activity',record)
        self.set('activity',[*self.get('activity',[])[-23:],record])
        return record
    def phase_bytes(self):
        folder=self.root/'phase-reports'
        if folder.is_symlink():raise PermissionError('Pasta de relatórios com symlink recusada')
        return sum(p.stat().st_size for p in folder.glob('*.txt') if p.is_file() and not p.is_symlink())
    def phase_reports(self,limit=30):
        if type(limit)is not int or not 1<=limit<=200:raise ValueError('Limite inválido')
        return [json.loads(row[0])['data'] for row in self.db.execute("SELECT body FROM audit WHERE json_extract(body,'$.kind')='evolution_activity' AND json_extract(body,'$.data.kind')='phase_finished' ORDER BY seq DESC LIMIT ?",(limit,))]
    def finish_phase(self,status='DONE',summary=None):
        progress=self.get('progress',{})
        if not progress or progress.get('phase_completed') or progress.get('phase')=='WAITING':return
        started=progress['started_at'] if progress['phase']=='COMPLETE' else progress.get('phase_started_at',progress['started_at'])
        duration=max(0,time.time()-started)
        self.record_activity('phase_finished',phase=progress['phase'],status=status,duration_seconds=duration,summary=summary or {})
        progress['phase_completed']=True;self.set('progress',progress)
    def set_progress(self,phase,status='RUNNING',job_id=None,summary=None,wait_reason=None):
        if phase not in PROGRESS_STAGES or status not in {'RUNNING','DONE','FAILED','DEFERRED','CANCELLED'}:
            raise ValueError('Etapa de progresso inválida')
        now=time.time();previous=self.get('progress',{})
        if previous.get('status')=='RUNNING' and (previous.get('phase')!=phase or status!='RUNNING'):
            self.finish_phase('DONE' if status=='RUNNING' else status,summary)
        started=previous.get('started_at',now) if previous.get('status')=='RUNNING' and previous.get('pid')==os.getpid() else now
        step,label=PROGRESS_STAGES[phase]
        same=previous.get('phase')==phase and previous.get('status')=='RUNNING'
        phase_started=previous.get('phase_started_at',now) if same else now
        value={'phase':phase,'status':status,'step':step,'total':7,'label':label,
               'started_at':started,'phase_started_at':phase_started,'updated_at':now,'pid':os.getpid()}
        if job_id:value['job_id']=job_id
        if wait_reason:value['wait_reason']=wait_reason
        self.set('progress',value)
        if status=='RUNNING' and not same:self.record_activity('phase_started',phase=phase,status=status)
        elif phase=='COMPLETE':self.finish_phase(status,summary)
        elif status=='DEFERRED':self.record_activity('waiting',reason=wait_reason or 'interactive')
        return value
    def observe(self,signal):
        body=canonical(signal)
        if len(body)>8000:raise ValueError('Sinal grande demais')
        with self.db:
            self.db.execute('INSERT INTO signals VALUES (?,?,1,?) ON CONFLICT(id) DO UPDATE SET count=count+1',
                            (signal['id'],body,'OPEN'))
        self.event('observed',{'signal':signal['id']})
        return signal['id']
    def status(self):
        return {'enabled':self.policy.enabled and not self.get('disabled',False),'paused':self.get('paused',False),
                'schedule_enabled':self.policy.schedule_enabled,'backlog':len(self.backlog()),
                'progress':self.progress(),'last_cycle':self.get('last_cycle'),
                'activity':self.get('activity',[]),
                'jobs':[{'id':i,'status':s} for i,s in self.db.execute('SELECT id,status FROM jobs ORDER BY id')],
                'scheduled_experiments':[{'proposal_id':i,'status':s} for i,s in self.db.execute('SELECT proposal_id,status FROM scheduled_experiments ORDER BY updated DESC LIMIT 20')],
                'audit_hash':self.verify_audit(),'metadata_bytes':self.path.stat().st_size,
                'phase_report_bytes':self.phase_bytes(),'metadata_total_bytes':self.path.stat().st_size+self.phase_bytes(),'security':'SECURITY INCOMPLETE'}


@contextmanager
def exclusive(state):
    path=private_file(state.root/'.run.lock')
    fd=os.open(path,os.O_RDWR|getattr(os,'O_NOFOLLOW',0))
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Experimento já em andamento') from None
        state.active()
        if state.get('disabled',False):raise PermissionError('Evolução desabilitada')
        state.set('cancel',False)
        yield
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
