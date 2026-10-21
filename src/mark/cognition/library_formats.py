
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
from xml.etree import ElementTree as ET

IMAGES={'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff','.gif'}
MEDIA={'.mp3','.wav','.ogg','.flac','.m4a','.aac','.opus','.mp4','.mkv','.webm','.mov','.avi','.wmv'}
OFFICE={'.odt','.ods','.odp','.pptx','.xlsx','.rtf'}

def ocr(path):
    data=Path('/resources/tessdata')
    if not Path('/usr/bin/tesseract').is_file() or not (data/'por.traineddata').exists() or not (data/'eng.traineddata').exists():raise ValueError('OCR_DEPENDENCY_MISSING')
    result=subprocess.run(['/usr/bin/tesseract',str(path),'stdout','--tessdata-dir',str(data),'-l','por+eng','--psm','6'],capture_output=True,timeout=35)
    if result.returncode:raise ValueError('OCR_FAILED')
    text=result.stdout.decode('utf-8').strip()
    if not text:raise ValueError('OCR_NO_TEXT')
    return text

def scan_pdf(path):
    info=subprocess.run(['/usr/bin/pdfinfo',str(path)],capture_output=True,timeout=10)
    match=re.search(rb'(?m)^Pages:\s+(\d+)',info.stdout)
    count=int(match.group(1)) if match else None
    if not count:raise ValueError('PDF_EXTRACTION_FAILED')
    pages=[]
    for page in range(1,min(count,5)+1):
        output=Path('/tmp')/f'ocr-page-{page}'  # nosec: B108
        result=subprocess.run(['/usr/bin/pdftoppm','-f',str(page),'-l',str(page),'-scale-to','2200','-png','-singlefile',str(path),str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        if result.returncode:raise ValueError('OCR_FAILED')
        try:pages.append((page,ocr(output.with_suffix('.png'))))
        except ValueError as exc:
            if str(exc)!='OCR_NO_TEXT':raise
    if not pages:raise ValueError('OCR_NO_TEXT')
    return {'pages':pages,'page_count':count,'title':path.stem,'author':'UNKNOWN','format':'pdf',
            'extraction_level':'PARTIAL_TEXT' if count>5 else 'TEXT','coverage':{'ocr_pages':min(count,5),'total_pages':count}}

def office(path,archive,xml):
    ext=path.suffix.lower();pages=[];page_count=None
    if ext=='.rtf':
        text=path.read_text('cp1252')
        if not text.lstrip().startswith('{\\rtf'):raise ValueError('MALFORMED_DOCUMENT')
        text=re.sub(r"\\'([0-9a-fA-F]{2})",lambda m:bytes([int(m.group(1),16)]).decode('cp1252',errors='replace'),text)
        text=re.sub(r'\\u(-?\d+)\??',lambda m:chr(int(m.group(1))%65536),text)
        text=re.sub(r'\\(?:par|line)\b','\n',text);text=re.sub(r'\\[a-zA-Z]+-?\d*\s?','',text);text=re.sub(r'[{}]','',text)
        return {'pages':[(None,text)],'page_count':None,'format':'rtf','extraction_level':'PARTIAL_TEXT','title':path.stem,'author':'UNKNOWN'}
    with archive(path) as z:
        if ext in ('.odt','.ods','.odp'):
            root=xml(z.read('content.xml'));text='\n'.join(t for t in root.itertext() if t.strip());pages=[(None,text)]
        elif ext=='.pptx':
            names=[n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)];names.sort(key=lambda n:int(re.search(r'(\d+)\.xml',n).group(1)));page_count=len(names)
            for i,name in enumerate(names):
                text='\n'.join(e.text or '' for e in xml(z.read(name)).iter() if e.tag.endswith('}t'))
                if text.strip():pages.append((i+1,text))
        else:
            shared=[]
            if 'xl/sharedStrings.xml' in z.namelist():
                for item in xml(z.read('xl/sharedStrings.xml')).iter():
                    if item.tag.endswith('}si'):shared.append(''.join(e.text or '' for e in item.iter() if e.tag.endswith('}t')))
            parts=[]
            for name in sorted(n for n in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',n)):
                rows=[]
                for cell in xml(z.read(name)).iter():
                    if not cell.tag.endswith('}c'):continue
                    values=[e.text or '' for e in cell.iter() if e.tag.endswith(('}v','}t'))];value=' '.join(values)
                    if cell.attrib.get('t')=='s' and value.isdigit():value=shared[int(value)]
                    formula=next((e.text or '' for e in cell if e.tag.endswith('}f')),None)
                    if formula:value+=' [fórmula registrada, não recalculada: '+formula+']'
                    rows.append(cell.attrib.get('r','')+': '+value)
                parts.append('# Planilha '+Path(name).stem+'\n'+'\n'.join(rows))
            pages=[(None,'\n'.join(parts))]
    if not pages or not any(t.strip() for _,t in pages):raise ValueError('EMPTY_DOCUMENT')
    return {'pages':pages,'page_count':page_count,'format':ext[1:],'extraction_level':'TEXT','title':path.stem,'author':'UNKNOWN'}

def media(path):
    probe=subprocess.run(['/usr/bin/ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,timeout=15)
    if probe.returncode:raise ValueError('MEDIA_INVALID')
    details=json.loads(probe.stdout);duration=float(details.get('format',{}).get('duration',0))
    if not any(s.get('codec_type')=='audio' for s in details.get('streams',[])):raise ValueError('MEDIA_NO_AUDIO')
    if not Path('/resources/whisper.bin').is_file() or not Path('/usr/bin/whisper-cli').is_file():raise ValueError('ASR_DEPENDENCY_MISSING')
    wav=Path('/tmp/media.wav');convert=subprocess.run(['/usr/bin/ffmpeg','-v','error','-threads','2','-i',str(path),'-t','60','-vn','-ac','1','-ar','16000','-y',str(wav)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=25)  # nosec: B108
    if convert.returncode:raise ValueError('MEDIA_INVALID')
    transcript=subprocess.run(['/usr/bin/whisper-cli','-ng','-t','2','-m','/resources/whisper.bin','-f',str(wav),'-l','auto','-np','-nt'],capture_output=True,timeout=100)
    if transcript.returncode:raise ValueError('ASR_FAILED')
    text=transcript.stdout.decode('utf-8',errors='replace').strip()
    if not text:raise ValueError('ASR_NO_SPEECH')
    return {'pages':[(None,'# Transcrição 00:00–01:00\n'+text)],'page_count':None,'title':path.stem,'author':'UNKNOWN','format':path.suffix.lstrip('.'),
            'extraction_level':'PARTIAL_TEXT' if duration>60 else 'TEXT','coverage':{'processed_seconds':min(duration,60),'total_seconds':duration}}

def universal(path,archive,xml):
    ext=path.suffix.lower()
    if ext in IMAGES:return {'pages':[(None,ocr(path))],'page_count':None,'title':path.stem,'author':'UNKNOWN','format':ext[1:],'extraction_level':'OCR_TEXT'}
    if ext in OFFICE:return office(path,archive,xml)
    if ext in MEDIA:return media(path)
    if zipfile.is_zipfile(path):
        pieces=[];entries=0
        with archive(path) as z:
            for item in z.infolist():
                if item.is_dir():continue
                entries+=1
                raw=z.read(item)
                try:text=raw.decode('utf-8')
                except UnicodeDecodeError:continue
                if '\0' not in text:pieces.append('# Entrada '+item.filename+'\n'+text)
                if sum(len(p) for p in pieces)>2_000_000:break
        if pieces:return {'pages':[(None,'\n'.join(pieces)[:2_000_000])],'page_count':None,'title':path.stem,'author':'UNKNOWN','format':'archive','extraction_level':'PARTIAL_TEXT','coverage':{'archive_entries':entries}}
    raw=path.read_bytes()
    if '\0' not in raw.decode('utf-8',errors='replace') and not raw.startswith((b'MZ',b'\x7fELF',b'\x89PNG',b'PK')):
        try:text=raw.decode('utf-8');encoding='UTF-8'
        except UnicodeDecodeError:text=raw.decode('cp1252',errors='replace');encoding='CP1252 (heurístico)'
        if text.strip():return {'pages':[(None,text[:2_000_000])],'page_count':None,'title':path.stem,'author':'UNKNOWN','format':ext.lstrip('.') or 'text','extraction_level':'PARTIAL_TEXT' if len(text)>2_000_000 else 'TEXT','encoding':encoding}

    output=Path('/tmp/static-strings.txt')  # nosec: B108
    with output.open('wb') as stream:
        subprocess.run(['/usr/bin/strings','-a','-n','8',str(path)],stdout=stream,stderr=subprocess.DEVNULL,timeout=15)
    text=output.read_bytes()[:2_000_000].decode('utf-8',errors='replace')
    lines=[line for line in text.splitlines() if len(line)<2000 and sum(c.isalpha() for c in line)>max(6,len(line)*.5)]
    if lines:return {'pages':[(None,'# Strings estáticas (não é código decompilado)\n'+'\n'.join(lines))],'page_count':None,'title':path.stem,'author':'UNKNOWN','format':ext.lstrip('.') or 'binary','extraction_level':'PARTIAL_TEXT'}
    return metadata_reference(path,'NO_READABLE_TEXT')

def metadata_reference(path,reason):
    size=path.stat().st_size
    return {'pages':[(None,f'Arquivo de referência: {path.name}. Tamanho: {size} bytes. Tipo: {path.suffix or "sem extensão"}. Conteúdo não foi extraído; não inferir seu conteúdo.')],
            'page_count':None,'title':path.stem,'author':'UNKNOWN','format':path.suffix.lstrip('.') or 'binary','extraction_level':'METADATA_ONLY','extraction_note':reason}
