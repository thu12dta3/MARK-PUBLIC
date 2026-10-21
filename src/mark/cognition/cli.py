
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from .domains import domain
from .knowledge import KnowledgeStore, DEFAULT_DB
from .storage import read_json, write_private_json
from .datasets import load_samples, validate_samples, deduplicate, split_dataset, save_splits
from .benchmark import load_cases, run_benchmark
from .experiments import Experiment, ExperimentStore, validate_training_config
from .providers import get_provider
from .privacy import sanitize, sanitized_candidate
from .tools import default_registry, ActionRequest
from .schemas import utc_now

ROOT=Path(__file__).resolve().parents[3]
REPORTS=Path.home()/'.local/state/mark/cognition/reports'

def parser():
    p=argparse.ArgumentParser(prog='MARK',description='MARK Cognitive Foundation v0.1')
    groups=p.add_subparsers(dest='group',required=True)
    knowledge=groups.add_parser('knowledge'); knowledge.add_argument('--db',type=Path,default=DEFAULT_DB)
    sub=knowledge.add_subparsers(dest='command',required=True)
    ingest=sub.add_parser('ingest'); ingest.add_argument('path',type=Path); ingest.add_argument('--domain',required=True); ingest.add_argument('--title',required=True); ingest.add_argument('--license',required=True)
    ingest.add_argument('--metadata',type=Path); ingest.add_argument('--source-type',default='USER_OWNED'); ingest.add_argument('--url',default=''); ingest.add_argument('--publication-date')
    search=sub.add_parser('search'); search.add_argument('query'); search.add_argument('--domain',action='append',default=[]); search.add_argument('--limit',type=int,default=5); search.add_argument('--include-history',action='store_true')
    sub.add_parser('stats'); sub.add_parser('reindex')
    lib=sub.add_parser('library');lib.add_argument('--root',type=Path)
    ops=lib.add_subparsers(dest='operation',required=True)
    add=ops.add_parser('add');add.add_argument('paths',nargs='+',type=Path);add.add_argument('--collection',default='Geral');add.add_argument('--mode',choices=['reference','managed'],default='reference')
    for name in ('status','list','work','pause','resume','cancel','retry'):ops.add_parser(name)
    coll=ops.add_parser('collection');coll.add_argument('name');coll.add_argument('--disable',action='store_true')
    ds=groups.add_parser('dataset').add_subparsers(dest='command',required=True)
    val=ds.add_parser('validate'); val.add_argument('path',type=Path); val.add_argument('--require-approved',action='store_true'); val.add_argument('--output',type=Path)
    split=ds.add_parser('split'); split.add_argument('path',type=Path); split.add_argument('--output-dir',type=Path,required=True)
    dedupe=ds.add_parser('dedupe'); dedupe.add_argument('path',type=Path); dedupe.add_argument('--output',type=Path,required=True)
    bench=groups.add_parser('benchmark').add_subparsers(dest='command',required=True)
    run=bench.add_parser('run'); run.add_argument('--suite',type=Path,default=ROOT/'benchmarks/markbench-v0.1.json'); run.add_argument('--provider',choices=['none','ollama'],default='none'); run.add_argument('--profile',choices=['chat','call'],default='chat'); run.add_argument('--output',type=Path)
    perf=bench.add_parser('performance'); perf.add_argument('--output',type=Path)
    report=bench.add_parser('report'); report.add_argument('path',type=Path)
    exp=groups.add_parser('experiment').add_subparsers(dest='command',required=True); exp.add_parser('list')
    add=exp.add_parser('add'); add.add_argument('path',type=Path)
    tools=groups.add_parser('tool').add_subparsers(dest='command',required=True); tools.add_parser('list')
    call=tools.add_parser('run'); call.add_argument('name'); call.add_argument('--arguments',required=True,help='Objeto JSON; somente cálculos/leitura registrados')
    learning=groups.add_parser('learning').add_subparsers(dest='command',required=True)
    san=learning.add_parser('sanitize'); san.add_argument('path',type=Path); san.add_argument('--output',type=Path,required=True)
    train=groups.add_parser('training').add_subparsers(dest='command',required=True)
    gate=train.add_parser('validate'); gate.add_argument('config',type=Path); gate.add_argument('--dataset-report',type=Path,required=True); gate.add_argument('--baseline-report',type=Path,required=True)
    return p

def _benchmark(args):
    from ..config import load_config
    from ..model_state import ModelSession
    cases=load_cases(args.suite); provider=None; session=None
    cfg=load_config()
    cfg['ollama']['model']=cfg['models'][args.profile]; cfg['ollama']['think']=cfg['thinking'][args.profile]
    try:
        if args.provider=='ollama':
            session=ModelSession(cfg); session.wake(); provider=get_provider(cfg)
        report=run_benchmark(cases,provider,lambda i,n,r:print(f"{i}/{n} {r['case_id']} {r['status']} {r['latency_ms']:.1f} ms",file=sys.stderr,flush=True))
    finally:
        if session: session.sleep()
    report['model_metadata']=provider.describe() if provider else {}
    report['profile']=args.profile if provider else None
    report['thinking']=cfg['ollama']['think'] if provider else None
    report['num_predict']=cfg['ollama']['num_predict'] if provider else None
    output=args.output or REPORTS/('markbench-'+args.provider+'-'+utc_now().replace(':','-')+'.json')
    write_private_json(output,report)
    store=ExperimentStore(); existing=store.list(); next_id=max([int(e['experiment_id'].split('-')[1]) for e in existing] or [0])+1
    experiment=Experiment(f'EXP-{next_id:04d}',cfg['ollama']['model'] if provider else 'NO_MODEL',report['model_metadata'].get('parameter_size','NONE'),'MARKBench synthetic fixtures','0.1','BASELINE',report['model_metadata'].get('quantization_level','UNKNOWN'),None,None,None,None,
                          {'target_gpu':'RTX 2060 SUPER 8 GiB','target_cpu':'i3-10100','target_ram_gib':16},report['duration_seconds'],None,report['runner_RAM_peak_KiB']/1024,
                          report['output_tokens']/max(report['duration_seconds'],.001) if provider else None,report['score'],float(report['security_gate']),
                          'Component suite and provider synthesis are reported separately; no training; RAM measures runner only.',report['status'])
    store.add(experiment)
    print(json.dumps({'report':str(output),'experiment_id':experiment.experiment_id,'status':report['status'],'counts':report['counts'],'model_calls':report['model_calls']},ensure_ascii=False))
    return 1 if report['status']=='FAIL' else 0

def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.group=='knowledge' and args.command=='library':
            from .library import Library
            with Library(args.db,args.root) as library:
                if args.operation=='add':result={'enqueued':library.enqueue(args.paths,args.collection,args.mode)}
                elif args.operation=='list':result=library.documents()
                elif args.operation=='work':
                    with library.owner():
                        while library.process_one():pass
                    result=library.stats()
                elif args.operation in ('pause','resume'):library.pause(args.operation=='pause');result=library.stats()
                elif args.operation in ('cancel','retry'):getattr(library,args.operation)();result=library.stats()
                elif args.operation=='collection':library.collection(args.name,not args.disable);result=library.stats()
                else:result=library.stats()
        elif args.group=='knowledge':
            with KnowledgeStore(args.db) as store:
                if args.command=='ingest':
                    metadata=read_json(args.metadata) if args.metadata else {}
                    result=store.ingest(args.path,args.domain,title=args.title,license=args.license,metadata=metadata,source_type=args.source_type,url=args.url,publication_date=args.publication_date)
                elif args.command=='search': result=[h.to_dict() for h in store.search(args.query,args.domain,args.limit,include_history=args.include_history)]
                elif args.command=='reindex': result=store.reindex()
                else: result=store.stats()
        elif args.group=='dataset':
            samples=load_samples(args.path)
            if args.command=='validate':
                result=validate_samples(samples,args.require_approved); result['reviewed']=bool(samples) and all(s.review_status=='APPROVED' for s in samples)
                if args.output: write_private_json(args.output,result)
            elif args.command=='split': result=save_splits(split_dataset(samples),args.output_dir)
            else:
                clean,dropped=deduplicate(samples)
                from .storage import private_file
                target=private_file(args.output); target.write_text(''.join(json.dumps(s.to_dict(),ensure_ascii=False)+'\n' for s in clean),encoding='utf-8')
                result={'samples':len(clean),'dropped':dropped}
        elif args.group=='benchmark':
            if args.command=='run': return _benchmark(args)
            if args.command=='performance':
                from .performance import measure_local_index
                result=measure_local_index()
                if args.output: write_private_json(args.output,result)
                print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
            report=read_json(args.path)
            result={key:report.get(key) for key in ('suite','status','counts','model','model_calls','security_gate','score','duration_seconds','limitations')}
        elif args.group=='experiment':
            store=ExperimentStore(); result=store.list() if args.command=='list' else store.add(Experiment(**read_json(args.path)))
        elif args.group=='tool':
            registry=default_registry(); result=registry.contracts() if args.command=='list' else registry.invoke(ActionRequest(args.name,json.loads(args.arguments)),{'tools.calculate','knowledge.read'})
        elif args.group=='learning':
            data=read_json(args.path)
            result=sanitized_candidate(data.get('input',''),data.get('context',''),data.get('mark_output',''),data.get('failure_type','UNKNOWN'),data.get('correct_behavior',''))
            write_private_json(args.output,result)
        else: result=validate_training_config(read_json(args.config),read_json(args.dataset_report),read_json(args.baseline_report))
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        return 1 if isinstance(result,dict) and result.get('status')=='FAIL' else 0
    except Exception as exc:
        print('Falha na operação ('+type(exc).__name__+'): '+str(sanitize(str(exc)))[:500],file=sys.stderr)
        return 1

if __name__=='__main__': raise SystemExit(main())
