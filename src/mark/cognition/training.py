
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import re
import time
from .datasets import load_samples, validate_samples, check_overlap, save_splits
from .experiments import validate_training_config
from .storage import read_json, write_private_json

MODEL_ID='Qwen/Qwen3-VL-4B-Instruct'
SYSTEM='Você é MARK. Responda em português brasileiro, com clareza e precisão. Use o contexto disponível e não invente ações executadas.'


def approved_split(dataset:Path,approval:Path,output:Path):
    review=read_json(approval);digest=hashlib.sha256(dataset.read_bytes()).hexdigest()
    if review.get('reviewed') is not True or review.get('reviewed_by')!='owner' or review.get('approved_sha256')!=digest:
        raise PermissionError('Aprovação humana não corresponde ao arquivo')
    rows=load_samples(dataset);validation=validate_samples(rows,True)
    if validation['status']!='PASS':raise ValueError(validation['errors'])
    groups={}
    for row in rows:
        family=row.source.get('family')
        if not isinstance(family,str) or not family:raise ValueError('Família de tarefa ausente')
        groups.setdefault(family,[]).append(row)
    if len(groups)<10:raise ValueError('Poucas famílias para avaliação separada')
    families=sorted(groups,key=lambda key:hashlib.sha256(('MARK-v02:'+key).encode()).hexdigest())
    reserve=max(1,len(families)//10)
    selected={'test':families[:reserve],'validation':families[reserve:reserve*2],'train':families[reserve*2:]}
    splits={name:[row for family in group for row in groups[family]] for name,group in selected.items()}
    if check_overlap(splits):raise ValueError('Overlap de exemplos')
    sets={name:{r.source['family'] for r in rows} for name,rows in splits.items()}
    if any(sets[a]&sets[b] for a,b in [('train','test'),('train','validation'),('validation','test')]):raise ValueError('Overlap de famílias')
    report=save_splits(splits,output);report.update({'reviewed':True,'status':'PASS','dataset_sha256':digest,'approved_path':str(dataset.resolve()),'families':selected,'family_overlap':False})
    write_private_json(output/'report.json',report)
    return report


def validate_paths(config_path:Path,approval_path:Path,baseline_path:Path,model_path:Path,splits_path:Path):
    cfg=read_json(config_path);approval=read_json(approval_path);baseline=read_json(baseline_path);split=read_json(splits_path/'report.json')
    gate=validate_training_config(cfg,approval,baseline)
    if gate['status']!='PASS':raise ValueError(gate['errors'])
    if cfg.get('executor')!='hf-qlora-v02' or cfg.get('base_model')!=MODEL_ID or cfg.get('method')!='QLORA':raise ValueError('Executor/modelo não suportado')
    if cfg.get('batch_size')!=1 or cfg.get('epochs')!=1 or cfg.get('vision_frozen') is not True or cfg.get('automatic_activation') is not False:raise ValueError('Receita fora do experimento inicial')
    if not 1<=cfg.get('lora_rank',0)<=16 or not 64<=cfg.get('max_sequence_length',0)<=512 or not 1e-6<=cfg.get('learning_rate',0)<=1e-3:raise ValueError('Hiperparâmetros fora do orçamento')
    if split.get('dataset_sha256')!=approval.get('approved_sha256') or split.get('family_overlap') is not False:raise ValueError('Splits não correspondem à aprovação')
    original_path=Path(split['approved_path'])
    original=load_samples(original_path)
    if hashlib.sha256(original_path.read_bytes()).hexdigest()!=approval['approved_sha256']:raise ValueError('Arquivo aprovado alterado')
    original_by_id={r.sample_id:r.to_dict() for r in original}
    rows={name:load_samples(splits_path/(name+'.jsonl')) for name in ('train','validation','test')}
    merged=[r for group in rows.values() for r in group]
    if len(merged)!=len(original) or any(original_by_id.get(r.sample_id)!=r.to_dict() for r in merged):raise ValueError('Splits alteraram conteúdo aprovado')
    family_sets=[{r.source['family'] for r in rows[name]} for name in ('train','validation','test')]
    if any(family_sets[i]&family_sets[j] for i,j in ((0,1),(0,2),(1,2))):raise ValueError('Famílias repetidas entre treino e avaliação')
    if check_overlap(rows) or any(validate_samples(r,True)['status']!='PASS' for r in rows.values()):raise ValueError('Splits inválidos')
    snapshot=read_json(model_path/'snapshot-manifest.json')
    if snapshot.get('model')!=MODEL_ID or snapshot.get('license')!='Apache-2.0':raise ValueError('Base sem proveniência esperada')
    if not re.fullmatch(r'[0-9a-f]{40}',snapshot.get('revision','')):raise ValueError('Revisão da base inválida')
    required={'config.json','tokenizer.json','model.safetensors.index.json'}
    if not required.issubset({f['rfilename'] for f in snapshot['files']}):raise ValueError('Manifesto da base incompleto')

    for file in snapshot['files']:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',file['rfilename']) or file['rfilename'] in {'.','..'}:raise ValueError('Nome de arquivo fora da base')
        p=model_path/file['rfilename']
        if p.is_symlink() or not p.is_file() or p.stat().st_size!=file['size']:raise ValueError('Base incompleta')
        digest=file.get('lfs',{}).get('sha256')
        if digest:
            with p.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise ValueError('Checksum da base divergente')
    counts={'train':len(rows['train']),'validation':len(rows['validation']),'test':len(rows['test'])}
    return cfg,rows,{'gate':gate,'counts':counts,'base_revision':snapshot['revision'],'dataset_sha256':split['dataset_sha256']}


def preflight(config,approval,baseline,base,splits,output):
    cfg,rows,report=validate_paths(config,approval,baseline,base,splits)
    return runtime_preflight(report,output)


def runtime_preflight(report,output):
    deps={name:importlib.util.find_spec(name) is not None for name in ('torch','transformers','peft','bitsandbytes')}
    report.update({'dependencies':deps,'status':'READY' if all(deps.values()) else 'BLOCKED_DEPENDENCIES','training_started':False})
    if all(deps.values()):
        import torch
        if not torch.cuda.is_available():raise RuntimeError('CUDA indisponível; não iniciar treino CPU pesado')
        free,total=torch.cuda.mem_get_info();cap=torch.cuda.get_device_capability()
        if cap<(7,5) or total<7*1024**3 or free<5*1024**3:raise RuntimeError('GPU/orçamento disponível incompatível com esta receita')
        report['gpu']={'name':torch.cuda.get_device_name(),'capability':list(cap),'total_gib':total/1024**3,'free_gib':free/1024**3,'torch':torch.__version__}
    if output:write_private_json(output,report)
    return report


def run(config,approval,baseline,base,splits,output,resume=False):
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1');os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    cfg,rows,manifest=validate_paths(config,approval,baseline,base,splits)
    ready=runtime_preflight(dict(manifest),None)
    if ready['status']!='READY':raise RuntimeError('Dependências de treinamento ausentes')
    if output.is_symlink():raise ValueError('Pasta de saída inválida')
    output.mkdir(mode=0o700,parents=True,exist_ok=True);output.chmod(0o700)
    if (output/'status.json').exists() and not resume:raise FileExistsError('Use outro nome de rodada; não sobrescrever progresso')
    previous=read_json(output/'status.json') if resume else None
    if resume and (previous.get('dataset_sha256')!=manifest['dataset_sha256'] or previous.get('base_revision')!=manifest['base_revision'] or previous.get('config')!=cfg):raise ValueError('Checkpoint de outro experimento')
    import torch
    from transformers import AutoProcessor,AutoModelForImageTextToText,BitsAndBytesConfig
    from peft import LoraConfig,get_peft_model,PeftModel,TaskType
    torch.set_num_threads(4);torch.manual_seed(42);random.seed(42)
    started=time.perf_counter();journal={**(previous or {}),**manifest,'state':'LOADING','training_started':bool(previous and previous.get('training_started')),'applied_to_mark':False,'config':cfg}
    write_private_json(output/'status.json',journal)
    processor=AutoProcessor.from_pretrained(base,local_files_only=True,trust_remote_code=False)  # nosec: B615
    quant=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.float16,llm_int8_skip_modules=['visual','lm_head'])
    model=AutoModelForImageTextToText.from_pretrained(base,local_files_only=True,trust_remote_code=False,torch_dtype=torch.float16,quantization_config=quant,device_map={'':0},attn_implementation='eager',low_cpu_mem_usage=True)  # nosec: B615
    actual=model.num_parameters();journal['base_parameters_actual']=actual
    if actual>4_500_000_000:raise ValueError('Base real excede orçamento')

    for parameter in model.parameters():parameter.requires_grad=False
    model.config.use_cache=False;model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False});model.enable_input_require_grads()
    if resume:
        model=PeftModel.from_pretrained(model,output/'checkpoint-adapter',is_trainable=True,local_files_only=True)
    else:
        model=get_peft_model(model,LoraConfig(r=cfg['lora_rank'],lora_alpha=cfg['lora_alpha'],lora_dropout=0.0,target_modules=r'.*language_model.*\.(q_proj|v_proj)$',bias='none',task_type=TaskType.CAUSAL_LM))
    names=[name for name,p in model.named_parameters() if p.requires_grad]
    if not names or any('lora_' not in name or 'visual' in name for name in names):raise ValueError('Somente adapters de linguagem podem ser treinados')
    journal['trainable_parameters']=sum(p.numel() for p in model.parameters() if p.requires_grad)
    if journal['trainable_parameters']>20_000_000:raise ValueError('Adapter excede orçamento')
    tokenizer=processor.tokenizer
    def encode(row):
        prompt=row.user_input+(('\n\nContexto:\n'+row.context) if row.context else '')
        messages=[{'role':'system','content':SYSTEM},{'role':'user','content':[{'type':'text','text':prompt}]}]
        prefix=processor.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
        inputs=tokenizer.encode(prefix,add_special_tokens=False);answer=tokenizer.encode(row.expected_answer,add_special_tokens=False)+[tokenizer.eos_token_id]
        if len(inputs)+len(answer)>cfg['max_sequence_length']:raise ValueError('Exemplo excede contexto; não truncar resposta: '+row.sample_id)
        ids=torch.tensor([inputs+answer],device='cuda');labels=ids.clone();labels[:,:len(inputs)]=-100
        return {'input_ids':ids,'attention_mask':torch.ones_like(ids),'labels':labels}
    def evaluate(items):
        model.eval();losses=[]
        with torch.no_grad(),torch.autocast('cuda',dtype=torch.float16):
            for row in items:
                loss=float(model(**encode(row)).loss);assert math.isfinite(loss);losses.append(loss)
        return sum(losses)/len(losses)
    def generate_examples(items):
        model.eval();results=[]
        with torch.no_grad():
            for row in items:
                batch=encode(row);labels=batch['labels'][0];prefix_length=int((labels==-100).sum());ids=batch['input_ids'][:,:prefix_length]
                with torch.autocast('cuda',dtype=torch.float16):generated=model.generate(input_ids=ids,attention_mask=torch.ones_like(ids),max_new_tokens=128,do_sample=False,use_cache=True)
                results.append({'id':row.sample_id,'prompt':row.user_input,'expected':row.expected_answer,'answer':tokenizer.decode(generated[0,prefix_length:],skip_special_tokens=True)})
        return results
    torch.cuda.reset_peak_memory_stats()
    if not resume:
        journal['before']={'validation_loss':evaluate(rows['validation']),'test_loss':evaluate(rows['test'])}
        write_private_json(output/'before-generations.json',generate_examples(rows['test']))
    else:journal['before']=previous['before']
    print('BASELINE_HF',journal['before'],flush=True)
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=cfg['learning_rate'],weight_decay=0.01)
    scaler=torch.amp.GradScaler('cuda',init_scale=128);order=list(range(len(rows['train'])));random.Random(42).shuffle(order);next_micro=0;step=0
    if resume:
        checkpoint_path=output/'optimizer.pt'
        if checkpoint_path.is_symlink():raise ValueError('Checkpoint inválido')
        checkpoint=torch.load(checkpoint_path,map_location='cpu',weights_only=True);optimizer.load_state_dict(checkpoint['optimizer']);scaler.load_state_dict(checkpoint['scaler']);next_micro=checkpoint['next_micro'];step=checkpoint['step']
    def checkpoint(state):
        model.save_pretrained(output/'checkpoint-adapter',safe_serialization=True)
        torch.save({'optimizer':optimizer.state_dict(),'scaler':scaler.state_dict(),'next_micro':next_micro,'step':step},output/'optimizer.pt');(output/'optimizer.pt').chmod(0o600)
        journal.update({'state':state,'training_started':True,'step':step,'samples_processed':next_micro,'duration_seconds':time.perf_counter()-started,'vram_peak_gib':torch.cuda.max_memory_reserved()/1024**3});write_private_json(output/'status.json',journal)
    try:
        model.train();acc=cfg['gradient_accumulation']
        overflow_retries=0
        while next_micro<len(order):
            chosen=order[next_micro:next_micro+acc];optimizer.zero_grad(set_to_none=True);loss_sum=0.0
            for index in chosen:
                with torch.autocast('cuda',dtype=torch.float16):loss=model(**encode(rows['train'][index])).loss/len(chosen)
                if not torch.isfinite(loss):raise RuntimeError('Loss não finita; preservar base')
                scaler.scale(loss).backward();loss_sum+=float(loss.detach())
            scaler.unscale_(optimizer)
            parameters=[p for p in model.parameters() if p.requires_grad]
            if any(p.grad is not None and not bool(torch.isfinite(p.grad).all()) for p in parameters):


                scaler.step(optimizer);scaler.update();optimizer.zero_grad(set_to_none=True)
                overflow_retries+=1
                print('FP16_RETRY',overflow_retries,'scale',scaler.get_scale(),flush=True)
                if overflow_retries>8 or scaler.get_scale()<1:raise RuntimeError('Gradientes não finitos persistentes')
                continue
            torch.nn.utils.clip_grad_norm_(parameters,1.0,error_if_nonfinite=True);scaler.step(optimizer);scaler.update();overflow_retries=0
            next_micro+=len(chosen);step+=1
            print('TRAIN_STEP',step,'samples',next_micro,'loss',round(loss_sum,4),'peak_gib',round(torch.cuda.max_memory_reserved()/1024**3,2),flush=True)
            if torch.cuda.max_memory_reserved()>cfg['vram_budget_gb']*1024**3:raise RuntimeError('Orçamento VRAM excedido')
            if step%4==0:checkpoint('TRAINING')
        checkpoint('EVALUATING')
        journal['after']={'validation_loss':evaluate(rows['validation']),'test_loss':evaluate(rows['test'])}
        write_private_json(output/'after-generations.json',generate_examples(rows['test']))
        journal['loss_gate']=all(journal['after'][k]<=journal['before'][k]*1.02 for k in journal['before'])
        model.save_pretrained(output/'adapter',safe_serialization=True);processor.save_pretrained(output/'adapter')
        journal.update({'state':'TRAINED_CANDIDATE','training_started':True,'duration_seconds':time.perf_counter()-started,'vram_peak_gib':torch.cuda.max_memory_reserved()/1024**3,'applied_to_mark':False,'next_step':'Review held-out generations and runtime conversion before activation'})
        write_private_json(output/'status.json',journal);print('TRAINING_COMPLETE',journal['after'],'loss_gate',journal['loss_gate'],flush=True)
    except KeyboardInterrupt:
        optimizer.zero_grad(set_to_none=True);checkpoint('INTERRUPTED');print('INTERRUPTED: adapter e optimizer preservados; não aplicado.',flush=True);raise
    except Exception as exc:
        checkpoint('FAILED');journal['error_type']=type(exc).__name__;write_private_json(output/'status.json',journal);raise


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    split=sub.add_parser('split');split.add_argument('--dataset',type=Path,required=True);split.add_argument('--approval',type=Path,required=True);split.add_argument('--output',type=Path,required=True)
    for name in ('preflight','run'):
        p=sub.add_parser(name)
        for field in ('config','approval','baseline','base','splits','output'):p.add_argument('--'+field,type=Path,required=True)
        if name=='run':p.add_argument('--resume',action='store_true')
    args=vars(parser.parse_args());action=args.pop('action')
    if action=='split':result=approved_split(**args)
    elif action=='preflight':result=preflight(**args)
    else:
        status_path=args['output']/'status.json'
        old_status=status_path.read_bytes() if status_path.is_file() and not status_path.is_symlink() else None
        try:run(**args)
        except BaseException as exc:


            if status_path.is_file() and not status_path.is_symlink() and status_path.read_bytes()!=old_status:
                journal=read_json(status_path)
                if journal.get('state')=='LOADING':
                    journal.update({'state':'INTERRUPTED_LOADING' if isinstance(exc,KeyboardInterrupt) else 'FAILED_LOADING','error_type':type(exc).__name__,'applied_to_mark':False})
                    write_private_json(status_path,journal)
            raise
        return
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
