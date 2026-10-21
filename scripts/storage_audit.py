#!/usr/bin/env python3

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import stat
import time


def write_private(path, data):
    path=Path(path); path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.parent.lstat().st_uid!=os.getuid() or path.parent.is_symlink(): raise PermissionError('Pasta de saída inválida')
    path.parent.chmod(0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
    os.fchmod(fd,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream: json.dump(data,stream,ensure_ascii=False,indent=2)


def file_rows(label,root):
    root=Path(root); rows=[]; errors=[]
    if not root.exists(): return rows,errors
    candidates=[root] if root.is_file() else []
    if root.is_dir():
        for base,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not (d=='.git' and label!='git_internal') and not Path(base,d).is_symlink()]
            candidates.extend(Path(base,f) for f in files)
    for path in candidates:
        try:
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode): continue
            rows.append({'component':label,'path':str(path),'relative':str(path.relative_to(root)) if path!=root else path.name,
                         'size':info.st_size,'allocated':info.st_blocks*512,'device':info.st_dev,'inode':info.st_ino,'links':info.st_nlink,
                         'mtime_ns':info.st_mtime_ns,'ctime_ns':info.st_ctime_ns,'extension':path.suffix.lower() or '[none]'})
        except OSError as exc: errors.append({'component':label,'type':type(exc).__name__})
    return rows,errors


def digest(row,cache):
    key=':'.join(str(row[k]) for k in ('device','inode','size','mtime_ns','ctime_ns'))
    if key not in cache:
        h=hashlib.sha256()
        with open(row['path'],'rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
        after=Path(row['path']).stat()
        if (after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=(row['size'],row['mtime_ns'],row['ctime_ns']): raise RuntimeError('Arquivo alterado durante hash')
        cache[key]=h.hexdigest()
    return cache[key]


def scan(roots,cache=None):
    cache=cache if cache is not None else {}; rows=[]; errors=[]; started=time.monotonic()
    components={}
    for label,path in roots.items():
        entries,failures=file_rows(label,path); rows+=entries; errors+=failures
        components[label]={'files':len(entries),'logical_bytes':sum(r['size'] for r in entries),
                           'allocated_bytes':sum(r['allocated'] for r in entries),
                           'types':dict(Counter(r['extension'] for r in entries).most_common(10)),
                           'largest':[{'relative':r['relative'],'size':r['size']} for r in sorted(entries,key=lambda r:r['size'],reverse=True)[:8]]}
    sizes=defaultdict(list)
    for row in rows:
        if row['size']>0: sizes[row['size']].append(row)
    groups=[]
    for size,entries in sizes.items():
        if len(entries)<2: continue
        hashes=defaultdict(list)
        for row in entries:
            try: hashes[digest(row,cache)].append(row)
            except (OSError,RuntimeError) as exc: errors.append({'component':row['component'],'type':type(exc).__name__})
        for sha,same in hashes.items():
            if len(same)>1:
                inodes={(r['device'],r['inode']) for r in same}
                groups.append({'sha256':sha,'size':size,'copies':len(same),'unique_inodes':len(inodes),
                               'logical_excess_bytes':size*(len(same)-1),'members':[{'component':r['component'],'relative':r['relative'],'path':r['path']} for r in same]})
    unique={(r['device'],r['inode']):r for r in rows}
    return {'schema_version':1,'elapsed_seconds':time.monotonic()-started,'components':components,'files':len(rows),
            'logical_bytes':sum(r['size'] for r in rows),'allocated_unique_inode_bytes':sum(r['allocated'] for r in unique.values()),
            'exact_duplicate_groups':len(groups),'duplicate_excess_paths':sum(g['copies']-1 for g in groups),
            'duplicates':sorted(groups,key=lambda g:g['logical_excess_bytes'],reverse=True),'errors':errors,'rows':rows,
            'measurement_warning':'st_blocks counts reflinks more than once. This is not physical extent uniqueness; use Btrfs exclusive measurement separately.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',action='append',default=[],metavar='LABEL=PATH')
    parser.add_argument('--output',type=Path,required=True,help='Inventário privado; não publicar automaticamente')
    parser.add_argument('--hash-cache',type=Path)
    parser.add_argument('--budget',action='append',default=[],metavar='LABEL=MIB',
                        help='Limite de aviso configurável; nunca apaga arquivos')
    args=parser.parse_args()
    roots=dict(item.split('=',1) for item in args.root) if args.root else {'checkout':str(Path.cwd())}
    cache=json.loads(args.hash_cache.read_text()) if args.hash_cache and args.hash_cache.exists() else {}
    budgets={}
    for item in args.budget:
        try:
            label,value=item.split('=',1); value=float(value)
            if label not in roots or not 0<value<float('inf'): raise ValueError()
            budgets[label]=int(value*1024*1024)
        except ValueError: parser.error('Budget deve ser LABEL=MIB positivo para uma raiz selecionada')
    report=scan(roots,cache)
    report['budget_warnings']=[{'component':label,'logical_bytes':report['components'][label]['logical_bytes'],'budget_bytes':limit}
                              for label,limit in budgets.items() if report['components'][label]['logical_bytes']>limit]
    write_private(args.output,report)
    if args.hash_cache: write_private(args.hash_cache,cache)
    print(json.dumps({k:report[k] for k in ('files','logical_bytes','allocated_unique_inode_bytes','exact_duplicate_groups','elapsed_seconds','errors','budget_warnings')}))
if __name__=='__main__': main()
