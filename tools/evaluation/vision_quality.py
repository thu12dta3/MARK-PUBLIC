
import argparse
import base64
import copy
import io
import json
import re
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from mark.config import load_config
from mark.brain_model import _ollama
from mark.brain_prompt import SYSTEM
from mark.vision_quality import visual_instructions
from mark.model_state import ModelSession
from mark.cognition.storage import write_private_json


def fixtures():
    font=ImageFont.truetype('/usr/share/fonts/TTF/DejaVuSans.ttf',44)
    image=Image.new('RGB',(1280,720),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((100,220,320,440),fill='red')
    for x in (620,940):draw.ellipse((x,220,x+180,400),fill='blue')
    yield 'VQ-01',image,'Conte as formas coloridas e descreva sua cor e posição. Seja breve.',[r'tres|\b3\b',r'vermelh',r'azul'],[]
    image=Image.new('RGB',(1280,720),'white');draw=ImageDraw.Draw(image)
    draw.text((80,220),'MARK 27',font=font,fill='black')
    draw.text((80,300),'TEMPERATURA: 24 C',font=font,fill='black')
    yield 'VQ-02',image,'Leia o texto da etiqueta, sem adicionar informações.',[r'mark\s*27',r'24'],[]
    image=Image.new('RGB',(1280,720),'white');draw=ImageDraw.Draw(image)
    for y in (100,300):
        for x in (200,500):draw.rectangle((x,y,x+100,y+100),fill='green')
    yield 'VQ-03',image,'Quantos quadrados verdes aparecem? Responda somente a contagem.',[r'quatro|\b4\b'],[]
    image=Image.new('RGB',(1280,720),(5,5,5))
    yield 'VQ-04',image,'Qual número está na etiqueta? Responda só se conseguir ler.',[r'nao|não|escura|ileg[ií]vel|nenhum|n[aã]o.*vis[ií]vel'],[r'\b\d+\b']


def run(output):
    cfg=copy.deepcopy(load_config());cfg['ollama'].update(model=cfg['models']['call'],think=False,num_predict=220)
    session=ModelSession(cfg);rows=[]
    try:
        session.wake()
        for identity,image,prompt,required,forbidden in fixtures():
            stream=io.BytesIO();image.save(stream,'JPEG',quality=95,subsampling=0)
            start=time.monotonic()
            try:
                reply=_ollama(prompt,cfg,SYSTEM+visual_instructions(),images=[base64.b64encode(stream.getvalue()).decode()])
                checks={'required':all(re.search(pattern,reply,re.I) for pattern in required),
                        'forbidden':not any(re.search(pattern,reply,re.I) for pattern in forbidden)}
                row={'id':identity,'status':'PASS' if all(checks.values()) else 'FAIL','reply':reply,'checks':checks}
            except Exception as exc:
                row={'id':identity,'status':'ERROR','error_type':type(exc).__name__}
            row['latency_seconds']=round(time.monotonic()-start,2);rows.append(row)
            print(identity,row['status'],row['latency_seconds'],flush=True)
    finally:
        if session.awake:session.sleep()
    report={'scope':'4 authored synthetic vision cases; not a general recognition accuracy estimate, no training',
            'model':cfg['ollama']['model'],'cases':rows,'counts':{s:sum(row['status']==s for row in rows) for s in ('PASS','FAIL','ERROR')}}
    write_private_json(output,report);print('RESULT',report['counts'],flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
