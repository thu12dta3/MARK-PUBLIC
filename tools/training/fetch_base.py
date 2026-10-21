
import argparse,hashlib,json,os,re,time,urllib.request
from pathlib import Path
MODEL='Qwen/Qwen3-VL-4B-Instruct'
ROOT=Path.home()/'.local/share/mark/training-base'

def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'MARK-local-training/0.2'}),timeout=90)

def main():
    with fetch('https://huggingface.co/api/models/'+MODEL+'?blobs=true') as stream:info=json.load(stream)
    revision=info['sha'];assert re.fullmatch(r'[0-9a-f]{40}',revision)
    assert info.get('cardData',{}).get('license')=='apache-2.0','Licença não confirmada'
    directory=ROOT/'Qwen3-VL-4B-Instruct'/revision;directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    chosen=[r for r in info['siblings'] if '/' not in r['rfilename'] and (r['rfilename'].endswith(('.json','.safetensors','.jinja','.txt')) or r['rfilename']=='LICENSE')]
    manifest={'model':MODEL,'revision':revision,'license':'Apache-2.0','files':chosen};(directory/'snapshot-manifest.json').write_text(json.dumps(manifest,indent=2))
    print('Checkpoint público fixado:',MODEL,revision,'arquivos',len(chosen),flush=True)
    for record in chosen:
        name=record['rfilename'];assert re.fullmatch(r'[A-Za-z0-9_.-]+',name)
        size=record['size'];dest=directory/name;digest=record.get('lfs',{}).get('sha256')
        if dest.exists() and dest.stat().st_size==size:
            if digest and hashlib.file_digest(dest.open('rb'),'sha256').hexdigest()!=digest:raise ValueError('Checksum existente inválido')
            print('Preservado:',name,flush=True);continue
        part=directory/(name+'.partial');offset=part.stat().st_size if part.exists() else 0
        for attempt in range(4):
            try:
                headers={'User-Agent':'MARK-local-training/0.2'}
                if offset:headers['Range']=f'bytes={offset}-'
                req=urllib.request.Request('https://huggingface.co/'+MODEL+'/resolve/'+revision+'/'+name,headers=headers)
                with urllib.request.urlopen(req,timeout=90) as stream:
                    if offset and stream.status!=206:offset=0
                    with part.open('ab' if offset else 'wb') as output:
                        next_notice=offset+256*1024*1024
                        while True:
                            chunk=stream.read(1024*1024)
                            if not chunk:break
                            output.write(chunk);offset+=len(chunk)
                            if offset>=next_notice:print(name,round(offset/1024**2),'/',round(size/1024**2),'MiB',flush=True);next_notice=offset+256*1024*1024
                if offset!=size:raise ValueError('Tamanho incompleto')
                if digest:
                    with part.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==digest,'Checksum inválido'
                part.replace(dest);dest.chmod(0o600);print('Concluído:',name,flush=True);break
            except Exception as exc:
                print('Tentativa',attempt+1,name,type(exc).__name__,flush=True)
                if attempt==3:raise RuntimeError('Download não concluído: '+name) from None
                offset=part.stat().st_size if part.exists() else 0;time.sleep(2)
    (ROOT/'current.json').write_text(json.dumps({'model':MODEL,'revision':revision,'path':str(directory)},indent=2));(ROOT/'current.json').chmod(0o600)
    print('BASE_READY',directory,flush=True)
if __name__=='__main__':main()
