
import argparse
import base64
import copy
import io
import re
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from mark.brain_model import _ollama
from mark.brain_prompt import SYSTEM
from mark.config import load_config
from mark.model_state import ModelSession
from mark.vision_quality import visual_instructions
from mark.vision_focus import crop_focus, focus_instructions
from mark.cognition.storage import write_private_json


def cases():
    font=ImageFont.truetype('/usr/share/fonts/TTF/DejaVuSans.ttf',48)
    small=ImageFont.truetype('/usr/share/fonts/TTF/DejaVuSans.ttf',18)
    image=Image.new('RGB',(2048,1536),'#e7edf0');draw=ImageDraw.Draw(image)
    draw.text((80,80),'CATALOGO - OUTRO CODIGO: X99',font=font,fill='black')
    draw.rectangle((1580,1240,1980,1430),fill='white',outline='black',width=2)
    draw.text((1640,1310),'MARK B27',font=small,fill='black')
    yield 'VF-01',image,(.77,.80,.97,.94),'Leia o código da etiqueta pequena no canto inferior direito.',r'b\s*27',False
    image=Image.new('RGB',(1280,720),'white');draw=ImageDraw.Draw(image)
    for x in (60,260,460):draw.ellipse((x,200,x+100,300),fill='green')
    for x in (800,1060):draw.ellipse((x,220,x+100,320),fill='green')
    yield 'VF-02',image,(.6,0,1,1),'Quantos círculos verdes aparecem no lado direito da imagem? Responda somente a contagem.',r'\b2\b|dois',False
    image=Image.new('RGB',(1280,720),'white');draw=ImageDraw.Draw(image)
    draw.text((50,160),'OUTRO: R72',font=font,fill='black')
    draw.rectangle((820,150,1180,400),fill=(10,10,10))
    yield 'VF-03',image,(.64,.20,.94,.57),'Qual código aparece no retângulo escuro do lado direito? Só responda se legível.',r'n[aã]o|ileg[ií]vel|nenhum|escuro',True
    image=Image.new('RGB',(1280,720),'white');draw=ImageDraw.Draw(image)
    draw.text((45,100),'ITEM A: 15 C',font=font,fill='black')
    draw.rectangle((760,390,1230,650),outline='black',width=3)
    draw.text((790,450),'ITEM B: 24 C',font=font,fill='black')
    yield 'VF-04',image,(.59,.54,.97,.91),'Leia a temperatura indicada no ITEM B no canto inferior direito.',r'\b24\b',False


def run(output):
    cfg=copy.deepcopy(load_config());cfg['ollama'].update(model=cfg['models']['call'],think=False,num_predict=180)
    session=ModelSession(cfg);rows=[]
    try:
        session.wake()
        for identity,image,rect,prompt,expected,deny_digits in cases():
            raw=io.BytesIO();image.save(raw,'JPEG',quality=95,subsampling=0);data=raw.getvalue()
            for mode in ('whole','focus'):
                start=time.monotonic()
                images=[base64.b64encode(data).decode()]
                rules=SYSTEM+visual_instructions()
                if mode=='focus':
                    images.append(base64.b64encode(crop_focus(data,rect)).decode())
                    rules+=focus_instructions(rect)
                try:
                    reply=_ollama(prompt,cfg,rules,images=images)
                    passed=bool(re.search(expected,reply,re.I)) and not(deny_digits and re.search(r'\b\d+\b',reply))
                    row={'id':identity,'mode':mode,'status':'PASS' if passed else 'FAIL','reply':reply}
                except Exception as exc:row={'id':identity,'mode':mode,'status':'ERROR','error_type':type(exc).__name__}
                row['latency_seconds']=round(time.monotonic()-start,2);rows.append(row)
                print(identity,mode,row['status'],row['latency_seconds'],flush=True)
    finally:
        if session.awake:session.sleep()
    report={'scope':'4 small authored known fixtures, 8 serial calls, same installed model; no training or blind/general accuracy claim',
            'model':cfg['ollama']['model'],'cases':rows,
            'counts':{mode:{status:sum(row['mode']==mode and row['status']==status for row in rows) for status in ('PASS','FAIL','ERROR')} for mode in ('whole','focus')}}
    write_private_json(output,report);print('RESULT',report['counts'],flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
