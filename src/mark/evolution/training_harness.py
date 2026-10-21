
import _thread
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0,'/trusted/src')


def main():
    plan=json.loads(Path('/inputs/plan.json').read_text())
    from mark.cognition.training import preflight,run
    if '--probe' in sys.argv:
        deps={name:bool(importlib.util.find_spec(name)) for name in ('torch','transformers','peft','bitsandbytes')}
        import torch
        available=torch.cuda.is_available();operation=False
        if available:
            optimizer=torch.optim.AdamW([torch.nn.Parameter(torch.ones(2,device='cuda'))]);value=torch.tensor([2.,3.],device='cuda').sum();torch.cuda.synchronize();operation=float(value)==5.
        from mark.evolution.efficiency import EnergyMeter
        with EnergyMeter() as meter:time.sleep(.5)
        print(json.dumps({'dependencies':deps,'cuda_available':available,'cuda_operation':operation,'gpu_energy_available':meter.result()['sensor_status']=='MEASURED','private_home_visible':Path('/home/mark/.config/mark/config.json').exists(),
                          'gpu_total_gib':torch.cuda.get_device_properties(0).total_memory/1024**3 if available else None}))
        return
    import torch
    torch.cuda.set_per_process_memory_fraction(plan['vram_budget_gib']/(torch.cuda.get_device_properties(0).total_memory/1024**3))
    stop=threading.Event()
    def monitor():
        started=time.monotonic()
        while not stop.wait(.1):
            if Path('/control/cancel').exists() or time.monotonic()-started>plan['max_seconds']:
                _thread.interrupt_main();return
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    try:
        run(Path(plan['config']),Path(plan['dataset_approval']),Path(plan['baseline']),Path(plan['base']),Path(plan['splits']),Path('/output'),resume=plan.get('resume',False))
        if plan.get('holdout'):evaluate_holdout(plan)
    finally:stop.set()

def evaluate_holdout(plan):

    import contextlib,gc,hashlib
    import torch
    from transformers import AutoProcessor,AutoModelForImageTextToText,BitsAndBytesConfig
    from peft import PeftModel
    from mark.cognition.domains import normalize
    from mark.cognition.storage import write_private_json
    path=Path(plan['holdout'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=plan['holdout_hash']:raise ValueError('Holdout alterado')
    gc.collect();torch.cuda.empty_cache()
    base=Path(plan['base']);processor=AutoProcessor.from_pretrained(base,local_files_only=True,trust_remote_code=False)  # nosec: B615
    quant=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,
                            bnb_4bit_compute_dtype=torch.float16,llm_int8_skip_modules=['visual','lm_head'])
    raw=AutoModelForImageTextToText.from_pretrained(base,local_files_only=True,trust_remote_code=False,  # nosec: B615
        torch_dtype=torch.float16,quantization_config=quant,device_map={'':0},attn_implementation='eager',low_cpu_mem_usage=True)
    model=PeftModel.from_pretrained(raw,Path('/output/adapter'),local_files_only=True,is_trainable=False)
    model.eval();cases=json.loads(path.read_text())['cases'];rows=[];metrics={}
    from mark.evolution.efficiency import EnergyMeter
    for repeat in range(3):
        for mode in (('original','adapter') if repeat%2==0 else ('adapter','original')):

            warm=processor.tokenizer('Olá',return_tensors='pt').to('cuda')
            with (model.disable_adapter() if mode=='original' else contextlib.nullcontext()),torch.no_grad():model.generate(**warm,max_new_tokens=2,do_sample=False)
            torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats();mode_start=time.monotonic()
            with EnergyMeter() as meter:
                for case in cases:
                    messages=[{'role':'system','content':'Responda em português de forma objetiva, sem inventar ações executadas.'},
                              {'role':'user','content':[{'type':'text','text':case['prompt']}]}]
                    text=processor.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
                    batch=processor.tokenizer(text,return_tensors='pt').to('cuda');start=time.monotonic()
                    with (model.disable_adapter() if mode=='original' else contextlib.nullcontext()),torch.no_grad(),torch.autocast('cuda',dtype=torch.float16):
                        outputs=model.generate(**batch,max_new_tokens=128,do_sample=False,use_cache=True)
                    reply=processor.tokenizer.decode(outputs[0,batch['input_ids'].shape[1]:],skip_special_tokens=True)
                    passed=all(normalize(fact) in normalize(reply) for fact in case['expected_facts'])
                    rows.append({'case_id':case['case_id'],'repeat':repeat,'mode':mode,'status':'PASS' if passed else 'FAIL',
                                 'latency_seconds':time.monotonic()-start,'reply':reply})
            measured=meter.result()
            entry=metrics.setdefault(mode,{'passed':0,'total':0,'energy_joules':0.,'vram_peak_gib':0.,'latency_seconds':0.,'sensor_status':'MEASURED','repeats':[]})
            measured_latency=time.monotonic()-mode_start
            entry['passed']+=sum(r['status']=='PASS' for r in rows if r['mode']==mode and r['repeat']==repeat)
            entry['total']+=len(cases)
            entry['vram_peak_gib']=max(entry['vram_peak_gib'],torch.cuda.max_memory_reserved()/1024**3)
            entry['latency_seconds']+=measured_latency
            if measured['energy_joules'] is None:entry['sensor_status']='UNMEASURED';entry['energy_joules']=None
            elif entry['energy_joules'] is not None:entry['energy_joules']+=measured['energy_joules']
            entry['repeats'].append({'energy_joules':measured['energy_joules'],'latency_seconds':measured_latency})
    counts={mode:sum(row['status']=='PASS' for row in rows if row['mode']==mode) for mode in ('original','adapter')}
    paired={(r['repeat'],r['case_id'],r['mode']):r['status'] for r in rows}
    regression=any(paired[(repeat,case['case_id'],'original')]=='PASS' and paired[(repeat,case['case_id'],'adapter')]!='PASS' for repeat in range(3) for case in cases)
    result={'scope':'Independent protected authored holdout; literal rubric, not general intelligence or complete safety',
            'holdout_hash':plan['holdout_hash'],'metrics':metrics,'counts':counts,'cases':rows,'regression':regression,
            'decision':'REJECTED' if regression or counts['adapter']<=counts['original'] else 'OWNER_REVIEW_REQUIRED',
            'vram_peak_gib':torch.cuda.max_memory_reserved()/1024**3,'model_activated':False}
    write_private_json(Path('/output/holdout-ab.json'),result)


if __name__=='__main__':main()
