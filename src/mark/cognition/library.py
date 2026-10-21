
from contextlib import contextmanager, closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import time
import threading
import weakref
from .knowledge import KnowledgeStore, DEFAULT_DB
from .library_extract import SUPPORTED,MAX_INPUT,supported,TEXT_NAMES
from .storage import private_file

_LOCKS=weakref.WeakValueDictionary()
_LOCKS_GUARD=threading.Lock()
def content_lock(key):
    with _LOCKS_GUARD:
        lock=_LOCKS.get(key)
        if lock is None:lock=threading.Lock();_LOCKS[key]=lock
        return lock

DEFAULT_ROOT=DEFAULT_DB.parent/'library'
ERRORS={'OCR_REQUIRED':'PDF sem texto: OCR necessário (não instalado automaticamente).',
        'UNSUPPORTED_FORMAT':'Formato ainda não suportado: arquivo ignorado, original preservado.',
        'MALFORMED_JSON':'JSON inválido ou incompleto.', 'MALFORMED_XML':'XML inválido ou incompleto.',
        'ENCODING_UNSUPPORTED':'Texto não está em UTF-8; converta uma cópia antes de importar.', 'INPUT_LIMIT':'Arquivo maior que 64 MiB.',
        'TEXT_LIMIT':'Texto extraído excede o limite de 2 milhões de caracteres.',
        'CANCELLED':'Cancelado pelo proprietário.', 'SOURCE_UNAVAILABLE':'Origem indisponível.',
        'SOURCE_CHANGED':'Origem alterada durante a importação; reprocessar.',
        'ISOLATION_UNAVAILABLE':'Isolamento indisponível; importação bloqueada.'}

def safe_path(value):
    p=Path(value).expanduser()
    if '..' in p.parts:raise ValueError('INVALID_PATH')
    p=p.absolute()
    for part in (p,*p.parents):
        if part.is_symlink():raise ValueError('INVALID_PATH')
    return p

def snapshot(source,destination,cancel=lambda:False):
    source=safe_path(source)
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    digest=hashlib.sha256()
    with os.fdopen(fd,'rb') as incoming, destination.open('xb') as out:
        info=os.fstat(incoming.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_INPUT:raise ValueError('INPUT_LIMIT')
        copied=0
        while True:
            if cancel():raise ValueError('CANCELLED')
            block=incoming.read(256*1024)
            if not block:break
            copied+=len(block)
            if copied>MAX_INPUT:raise ValueError('INPUT_LIMIT')
            digest.update(block);out.write(block)
        after=os.fstat(incoming.fileno())
        if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('SOURCE_CHANGED')
    destination.chmod(0o600)
    return digest.hexdigest(),info

def isolated_extract(path,cancel=lambda:False):
    if not shutil.which('bwrap') or not shutil.which('prlimit'):raise ValueError('ISOLATION_UNAVAILABLE')
    parser=Path(__file__).with_name('library_extract.py');helper=parser.with_name('library_formats.py')
    rich=path.suffix.lower() in {'.pdf','.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff','.gif','.mp3','.wav','.ogg','.flac','.m4a','.aac','.opus','.mp4','.mkv','.webm','.mov','.avi','.wmv'}
    memory=2147483648 if rich else 536870912
    maximum=180 if rich else 36
    args=['prlimit','--as='+str(memory),'--cpu='+str(150 if rich else 30),'--fsize=12582912','--nofile=64','--core=0','--',
          'bwrap','--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL','--clearenv',
          '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
          '--symlink','usr/lib','/lib64','--proc','/proc','--dev','/dev',
          '--tmpfs','/tmp',  # nosec: B108
          '--dir','/home','--dir','/home/mark','--setenv','HOME','/home/mark','--setenv','LANG','C.UTF-8',
          '--ro-bind',str(path),'/input'+path.suffix,'--ro-bind',str(parser),'/extractor.py',
          '--dir','/helpers','--ro-bind',str(helper),'/helpers/library_formats.py']
    data=Path.home()/'.local/share/mark/library-resources/tessdata';model=Path.home()/'.local/share/mark/ggml-base.bin'
    args+=['--dir','/resources','--setenv','OMP_THREAD_LIMIT','2']
    if data.is_dir() and not data.is_symlink():args+=['--ro-bind',str(data),'/resources/tessdata']
    if model.is_file() and not model.is_symlink() and path.suffix.lower() in {'.mp3','.wav','.ogg','.flac','.m4a','.aac','.opus','.mp4','.mkv','.webm','.mov','.avi','.wmv'}:args+=['--ro-bind',str(model),'/resources/whisper.bin']
    args+=['/usr/bin/python3','-I','/extractor.py','/input'+path.suffix]
    with tempfile.TemporaryFile() as output:
        process=subprocess.Popen(args,stdout=output,stderr=subprocess.DEVNULL,start_new_session=True)
        start=time.monotonic()
        try:
            while process.poll() is None:
                if cancel():raise ValueError('CANCELLED')
                if time.monotonic()-start>maximum:raise ValueError('EXTRACTION_TIMEOUT')
                time.sleep(.025)
        finally:
            if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait()
        output.seek(0);raw=output.read(12*1024**2+1)
        if len(raw)>12*1024**2:raise ValueError('TEXT_LIMIT')
        try:result=json.loads(raw)
        except (ValueError,UnicodeDecodeError):raise ValueError('EXTRACTION_FAILED') from None
        if 'error' in result:raise ValueError(result['error'])
        if process.returncode:raise ValueError('EXTRACTION_FAILED')
        return result

class Library:
    def __init__(self,db=DEFAULT_DB,root=None):
        self.path=Path(db);self.root=Path(root or self.path.parent/'library')
        self.queue_path=private_file(self.root/'imports.sqlite3')
        self.q=sqlite3.connect(self.queue_path,timeout=10);self.q.row_factory=sqlite3.Row
        self.q.execute('PRAGMA foreign_keys=ON');self.q.execute('PRAGMA journal_mode=WAL')
        version=self.q.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0,1):self.q.close();raise ValueError('QUEUE_SCHEMA_UNSUPPORTED')
        with self.q:
            self.q.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES('paused','0');
                INSERT OR IGNORE INTO settings VALUES('concurrency','1');
                CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,path TEXT NOT NULL,collection TEXT NOT NULL,mode TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'QUEUED',progress INTEGER NOT NULL DEFAULT 0,error TEXT,source_id TEXT,
                created REAL NOT NULL,finished REAL);
                CREATE INDEX IF NOT EXISTS imports_status ON imports(status,id);
                PRAGMA user_version=1;
            """)
        with KnowledgeStore(self.path) as store:
            if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='library_documents'").fetchone():

                if shutil.disk_usage(self.root).free<self.path.stat().st_size+512*1024**2:raise ValueError('DISK_RESERVE')
                backup=private_file(self.root/'knowledge-before-library.sqlite3')
                with closing(sqlite3.connect(backup)) as destination:store.db.backup(destination)
                with store.db:
                    store.db.executescript("""
                      CREATE TABLE library_schema(version INTEGER NOT NULL);
                      INSERT INTO library_schema VALUES(1);
                      CREATE TABLE library_collections(name TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1);
                      INSERT INTO library_collections VALUES('Geral',1);
                      CREATE TABLE library_documents(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),
                      checksum TEXT NOT NULL,original TEXT NOT NULL,managed TEXT,collection TEXT NOT NULL REFERENCES library_collections(name),
                      enabled INTEGER NOT NULL DEFAULT 1,bytes INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,pages INTEGER,format TEXT NOT NULL,imported REAL NOT NULL);
                      CREATE INDEX library_checksum ON library_documents(checksum);
                    """)
            elif store.db.execute('SELECT version FROM library_schema').fetchone()[0]!=1:raise ValueError('LIBRARY_SCHEMA_UNSUPPORTED')
        self.reclassify_formats()

    def reclassify_formats(self):
        row=self.q.execute("SELECT value FROM settings WHERE key='classifier_version'").fetchone()
        if row and row[0]=='3':return

        if self.q.execute("SELECT 1 FROM imports LIMIT 1").fetchone():
            backup=private_file(self.root/'imports-before-classifier-v3.sqlite3')
            with closing(sqlite3.connect(backup)) as destination:self.q.backup(destination)
        with self.q:
            for id_,path in self.q.execute("SELECT id,path FROM imports WHERE status IN ('SKIPPED','NEEDS_OCR') OR error IN ('UNSUPPORTED_FORMAT','OCR_REQUIRED','MALFORMED_DOCUMENT')").fetchall():
                if supported(Path(path)):
                    self.q.execute("UPDATE imports SET status='QUEUED',error=NULL,progress=0 WHERE id=?",(id_,))
                else:self.q.execute("UPDATE imports SET status='SKIPPED' WHERE id=?",(id_,))

            self.q.execute("INSERT OR REPLACE INTO settings VALUES('classifier_version','3')")
    def close(self):self.q.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    @contextmanager
    def owner(self):
        lock=private_file(self.root/'worker.lock')
        with lock.open('r+') as file:
            fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.q:self.q.execute("UPDATE imports SET status='QUEUED',progress=0 WHERE status='RUNNING'")
            yield
    def collection(self,name,enabled=None):
        if not isinstance(name,str) or not name.strip() or len(name)>100:raise ValueError('INVALID_COLLECTION')
        with KnowledgeStore(self.path) as store,store.db:
            store.db.execute('INSERT OR IGNORE INTO library_collections(name) VALUES(?)',(name,))
            if enabled is not None:store.db.execute('UPDATE library_collections SET enabled=? WHERE name=?',(int(enabled),name))
    def enqueue(self,paths,collection='Geral',mode='reference',cancel=lambda:False):
        if mode not in ('reference','managed'):raise ValueError('INVALID_MODE')
        self.collection(collection);count=0
        def add(path):
            nonlocal count
            if cancel():return
            error=None
            try:
                p=safe_path(path)
                if not supported(p):error='UNSUPPORTED_FORMAT'
                elif not p.is_file():error='SOURCE_UNAVAILABLE'
            except ValueError:p=Path(path).absolute();error='INVALID_PATH'

            if count>=100000:raise ValueError('QUEUE_BATCH_LIMIT')
            with self.q:
                if not self.q.execute("SELECT 1 FROM imports WHERE path=? AND collection=? AND status IN ('QUEUED','RUNNING')",(str(p),collection)).fetchone():
                    self.q.execute('INSERT INTO imports(path,collection,mode,status,error,created) VALUES(?,?,?,?,?,?)',
                                   (str(p),collection,mode,'SKIPPED' if error=='UNSUPPORTED_FORMAT' else 'ERROR' if error else 'QUEUED',error,time.time()));count+=1
        for value in paths:
            p=Path(value).expanduser().absolute()
            try:checked=safe_path(p)
            except ValueError:add(p);continue
            if checked.is_dir():
                for folder,dirs,files in os.walk(checked,followlinks=False):
                    dirs[:]=[d for d in dirs if not (Path(folder)/d).is_symlink()]
                    if cancel():break
                    for filename in files:add(Path(folder)/filename)
            else:add(checked)
        return count
    def pause(self,value=True):
        with self.q:self.q.execute("UPDATE settings SET value=? WHERE key='paused'",('1' if value else '0',))
    def paused(self):return self.q.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='1'
    def concurrency(self,value=None):
        if value is not None:
            if type(value)is not int or not 1<=value<=2:raise ValueError('CONCURRENCY_LIMIT')
            with self.q:self.q.execute("UPDATE settings SET value=? WHERE key='concurrency'",(str(value),))
        return int(self.q.execute("SELECT value FROM settings WHERE key='concurrency'").fetchone()[0])
    def cancel(self,ids=None):
        with self.q:
            if ids:
                self.q.executemany("UPDATE imports SET status='CANCELLED',error='CANCELLED' WHERE id=? AND status IN ('QUEUED','RUNNING')",[(int(i),) for i in ids])
            else:self.q.execute("UPDATE imports SET status='CANCELLED',error='CANCELLED' WHERE status IN ('QUEUED','RUNNING')")
    def retry(self,ids=None):
        with self.q:
            if ids:self.q.executemany("UPDATE imports SET status='QUEUED',error=NULL,progress=0 WHERE id=? AND status IN ('ERROR','CANCELLED')",[(int(i),) for i in ids])
            else:self.q.execute("UPDATE imports SET status='QUEUED',error=NULL,progress=0 WHERE status IN ('ERROR','CANCELLED')")
    def process_one(self,stop=lambda:False,cancel_item=lambda _:False):
        if self.paused() or stop():return False
        with self.q:
            self.q.execute('BEGIN IMMEDIATE')
            row=self.q.execute("SELECT * FROM imports WHERE status='QUEUED' ORDER BY id LIMIT 1").fetchone()
            if not row:return False
            self.q.execute("UPDATE imports SET status='RUNNING',progress=10 WHERE id=?",(row['id'],))
        def cancelled():
            return stop() or cancel_item(row['id']) or self.q.execute('SELECT status FROM imports WHERE id=?',(row['id'],)).fetchone()[0]=='CANCELLED'
        try:
            if shutil.disk_usage(self.root).free<512*1024**2:raise ValueError('DISK_RESERVE')
            with tempfile.TemporaryDirectory(prefix='extract-',dir=self.root) as folder:
                src=Path(row['path']);copy=Path(folder)/('input.txt' if src.name in TEXT_NAMES else 'input'+src.suffix.lower())
                checksum,info=snapshot(src,copy,cancelled)
                with content_lock((checksum,row['collection'])),KnowledgeStore(self.path) as store:
                    duplicate=store.db.execute('SELECT source_id FROM library_documents WHERE checksum=? AND collection=? LIMIT 1',(checksum,row['collection'])).fetchone()
                    if duplicate:result={'source_id':duplicate[0],'status':'DUPLICATE'}
                    else:
                        with self.q:self.q.execute('UPDATE imports SET progress=35 WHERE id=?',(row['id'],))
                        extracted=isolated_extract(copy,cancelled)
                        if cancelled():raise ValueError('CANCELLED')
                        doc_id=hashlib.sha256((str(src)+'\0'+row['collection']).encode()).hexdigest()
                        metadata={'document_key':'library:'+doc_id,'library':True,'collection':row['collection'],
                                  'format':extracted['format'],'page_count':extracted['page_count'],'mtime_ns':info.st_mtime_ns,
                                  'position_unit':'page/chunk','extraction_level':extracted.get('extraction_level','TEXT'),
                                  'extraction_note':extracted.get('extraction_note'),'coverage':extracted.get('coverage',{})}
                        if extracted.get('extraction_level')=='METADATA_ONLY':
                            extracted['pages']=[(None,f"Arquivo de referência: {src.name}. Tipo: {extracted['format']}. Tamanho: {info.st_size} bytes. Conteúdo não foi extraído; não inferir conteúdo.")]
                        try:
                            result=store.ingest_pages(src,extracted['pages'],checksum,'COMPUTING',title=extracted['title'] if extracted['title']!='input' else src.stem,
                                                      license='REFERENCE_ONLY_PERMISSION',metadata=metadata,author=extracted['author'],source_type='USER_OWNED')
                        except ValueError as exc:


                            if str(exc)!='Documento vazio':raise
                            extracted['extraction_level']='METADATA_ONLY';extracted['extraction_note']='NO_INDEXABLE_BODY'
                            metadata.update(extraction_level='METADATA_ONLY',extraction_note='NO_INDEXABLE_BODY')
                            body=f"Arquivo de referência: {src.name}. Conteúdo sem corpo indexável; não inferir conteúdo."
                            result=store.ingest_pages(src,[(None,body)],checksum,'COMPUTING',title=src.stem,
                                                      license='REFERENCE_ONLY_PERMISSION',metadata=metadata,author=extracted['author'],source_type='USER_OWNED')
                        if extracted.get('extraction_level')=='METADATA_ONLY':result['status']='REFERENCE_ONLY'
                        elif extracted.get('extraction_level')=='PARTIAL_TEXT':result['status']='PARTIAL'
                        result['note']=extracted.get('extraction_note')
                        managed=None
                        if row['mode']=='managed':
                            dest=private_file(self.root/'files'/(checksum+src.suffix.lower()))
                            if dest.stat().st_size==0:
                                tmpcopy=Path(folder)/'managed-copy';shutil.copyfile(copy,tmpcopy);tmpcopy.chmod(0o600);os.replace(tmpcopy,dest)
                            else:
                                check=hashlib.sha256()
                                with dest.open('rb') as existing:
                                    for block in iter(lambda:existing.read(256*1024),b''):check.update(block)
                                if check.hexdigest()!=checksum:raise ValueError('MANAGED_COPY_CORRUPT')
                            managed=str(dest)
                        with store.db:
                            store.db.execute('INSERT OR REPLACE INTO library_documents VALUES(?,?,?,?,?,?,1,?,?,?,?,?)',
                                (doc_id,result['source_id'],checksum,str(src),managed,row['collection'],info.st_size,info.st_mtime_ns,
                                 extracted['page_count'],extracted['format'],time.time()))
                if cancelled():

                    pass
            with self.q:self.q.execute('UPDATE imports SET status=?,progress=100,source_id=?,error=?,finished=? WHERE id=?',
                                     (result['status'],result['source_id'],result.get('note'),time.time(),row['id']))
        except Exception as exc:
            code=str(exc) if isinstance(exc,ValueError) and re_error(str(exc)) else 'SOURCE_UNAVAILABLE' if isinstance(exc,FileNotFoundError) else 'IMPORT_FAILED'
            with self.q:self.q.execute('UPDATE imports SET status=?,error=?,finished=? WHERE id=?',
                                     ('QUEUED' if stop() else 'CANCELLED' if code=='CANCELLED' else 'NEEDS_OCR' if code=='OCR_REQUIRED' else 'SKIPPED' if code=='UNSUPPORTED_FORMAT' else 'ERROR',code,time.time(),row['id']))
        return True
    def documents(self,query='',collection=None,limit=500,offset=0):
        with KnowledgeStore(self.path,read_only=True) as store:
            params=[];sql='SELECT d.*,s.record,c.enabled AS collection_enabled FROM library_documents d JOIN sources s ON d.source_id=s.id JOIN library_collections c ON d.collection=c.name WHERE 1=1'
            if collection:sql+=' AND d.collection=?';params.append(collection)
            if query:
                from .embeddings import tokens
                terms=list(dict.fromkeys(tokens(query)))[:32]
                expression=' OR '.join('"'+term+'"' for term in terms)
                if expression:
                    sql+=" AND (json_extract(s.record,'$.title') LIKE ? OR d.source_id IN (SELECT c.source_id FROM chunks c JOIN chunk_fts f ON f.chunk_id=c.id WHERE chunk_fts MATCH ?))"
                    params += ['%'+query+'%',expression]
                else:sql+=" AND json_extract(s.record,'$.title') LIKE ?";params.append('%'+query+'%')
            if type(offset)is not int or not 0<=offset<=1000000:raise ValueError('INVALID_OFFSET')
            sql+=' ORDER BY imported DESC LIMIT ? OFFSET ?';params.extend((min(int(limit),1000),offset))
            result=[]
            cursor=store.db.execute(sql,params);keys=[d[0] for d in cursor.description]
            for row in cursor:
                item=dict(zip(keys,row));record=json.loads(item.pop('record'));item['title']=record['title'];item['metadata']=record
                origin=Path(item['managed'] or item['original'])
                try:
                    info=safe_path(origin).stat();item['origin_status']='AVAILABLE' if item['managed'] or (info.st_size==item['bytes'] and info.st_mtime_ns==item['mtime_ns']) else 'CHANGED'
                except (OSError,ValueError):item['origin_status']='UNAVAILABLE'
                result.append(item)
            return result
    def enable(self,id_,value):
        with KnowledgeStore(self.path) as store,store.db:store.db.execute('UPDATE library_documents SET enabled=? WHERE id=?',(int(value),id_))
    def relocate(self,id_,new_path):
        new=safe_path(new_path)
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            checksum,info=snapshot(new,Path(tmp)/'check')
        with KnowledgeStore(self.path) as store,store.db:
            row=store.db.execute('SELECT checksum FROM library_documents WHERE id=?',(id_,)).fetchone()
            if not row or row[0]!=checksum:raise ValueError('RELOCATION_CHECKSUM_MISMATCH')
            store.db.execute('UPDATE library_documents SET original=?,mtime_ns=? WHERE id=?',(str(new),info.st_mtime_ns,id_))
    def remove(self,id_,delete_managed=False):
        with KnowledgeStore(self.path) as store,store.db:
            row=store.db.execute('SELECT source_id,managed FROM library_documents WHERE id=?',(id_,)).fetchone()
            if not row:return
            source=row[0];group=store.db.execute('SELECT group_id FROM sources WHERE id=?',(source,)).fetchone()[0]

            revisions=[r[0] for r in store.db.execute('SELECT id FROM sources WHERE group_id=?',(group,))]
            store.db.execute('DELETE FROM library_documents WHERE id=?',(id_,))
            for revision in revisions:
                store.db.execute('DELETE FROM chunk_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id=?)',(revision,))
                store.db.execute('DELETE FROM chunks WHERE source_id=?',(revision,));store.db.execute('DELETE FROM sources WHERE id=?',(revision,))
            if delete_managed and row[1]:
                managed=safe_path(row[1]);files=(self.root/'files').absolute()
                if managed.parent!=files:raise ValueError('INVALID_MANAGED_PATH')
                if not store.db.execute('SELECT 1 FROM library_documents WHERE managed=?',(str(managed),)).fetchone():managed.unlink(missing_ok=True)
    def stats(self):
        with KnowledgeStore(self.path,read_only=True) as store:
            counts=dict(self.q.execute('SELECT status,count(*) FROM imports GROUP BY status'))
            collections=[dict(zip(('name','enabled'),row)) for row in store.db.execute('SELECT name,enabled FROM library_collections ORDER BY name')]
            indexed=store.db.execute('SELECT count(*) FROM library_documents').fetchone()[0]
            references=store.db.execute("SELECT count(*) FROM library_documents d JOIN sources s ON d.source_id=s.id WHERE json_extract(s.record,'$.metadata.extraction_level')='METADATA_ONLY'").fetchone()[0]
        def bytes_at(path):return sum(p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink()) if path.exists() else 0
        return {'queue':counts,'files':sum(counts.values()),'indexed':indexed,'errors':counts.get('ERROR',0),'references':references,'text_indexed':indexed-references,'unsupported':counts.get('SKIPPED',0),'ocr_required':counts.get('NEEDS_OCR',0),
                'knowledge_bytes':self.path.stat().st_size,'queue_bytes':sum(p.stat().st_size for p in self.root.glob('imports.sqlite3*')),
                'managed_bytes':bytes_at(self.root/'files'),'backup_bytes':sum(p.stat().st_size for p in self.root.glob('*before*.sqlite3') if p.is_file()),
                'temporary_bytes':sum(bytes_at(p) for p in self.root.glob('extract-*') if p.is_dir()),
                'paused':self.paused(),'collections':collections,'concurrency':self.concurrency()}
    def queue(self,limit=500):return [dict(row) for row in self.q.execute('SELECT * FROM imports ORDER BY id DESC LIMIT ?',(limit,))]

def re_error(code):
    return len(code)<80 and all(c.isupper() or c=='_' for c in code)
