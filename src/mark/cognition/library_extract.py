
import csv
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
import defusedxml.ElementTree as ET

MAX_INPUT = 64 * 1024**2
MAX_TEXT = 2_000_000
MAX_EXPANDED = 32 * 1024**2
SUPPORTED = {'.pdf', '.docx', '.txt', '.md', '.html', '.htm', '.csv', '.json', '.yaml', '.yml', '.xml', '.epub'}

TEXT_CODE={'.py','.c','.h','.cpp','.hpp','.cc','.cs','.java','.kt','.kts','.js','.ts','.tsx','.jsx','.go','.rs','.lua','.luau','.ino','.sh','.sql','.toml','.ini','.cfg','.log'}
TEXT_NAMES={'README','LICENSE','NOTICE','Makefile','Dockerfile','CMakeLists.txt','.gitignore'}
SUPPORTED |= TEXT_CODE

def supported(path):return True

class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        if tag in ('p','br','div','tr','h1','h2','h3'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)
    def text(self):return ''.join(self.parts)

def html_text(text):
    parser=TextHTML();parser.feed(text);return parser.text()

def xml(data):
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I):raise ValueError('XML_ENTITY_BLOCKED')
    return ET.fromstring(data)

def archive(path):
    z=zipfile.ZipFile(path)
    total=0
    if len(z.infolist())>2000:raise ValueError('ARCHIVE_LIMIT')
    for item in z.infolist():
        name=Path(item.filename)
        if name.is_absolute() or '..' in name.parts or '\\' in item.filename:raise ValueError('ARCHIVE_PATH')
        total+=item.file_size
        if item.flag_bits&1 or item.file_size>MAX_EXPANDED or total>MAX_EXPANDED:raise ValueError('ARCHIVE_LIMIT')
        if item.file_size>1024**2 and item.file_size/max(item.compress_size,1)>200:raise ValueError('ARCHIVE_RATIO')
    return z

def extract_known(path):
    ext=path.suffix.lower()
    if not supported(path):raise ValueError('UNSUPPORTED_FORMAT')
    if path.stat().st_size>MAX_INPUT:raise ValueError('INPUT_LIMIT')
    pages=[];author='UNKNOWN';title=path.stem
    if ext=='.pdf':
        with path.open('rb') as f:
            if f.read(5)!=b'%PDF-':raise ValueError('INVALID_PDF')
        out=Path('/tmp/extracted.txt')  # nosec: B108
        p=subprocess.run(['/usr/bin/pdftotext','-layout','-enc','UTF-8',str(path),str(out)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=25)
        if p.returncode or not out.is_file():raise ValueError('PDF_EXTRACTION_FAILED')
        if out.stat().st_size>8*1024**2:raise ValueError('TEXT_LIMIT')
        text=out.read_text('utf-8')
        parts=text.split('\f');page_count=len(parts)-(1 if not parts[-1].strip() else 0)
        pages=[(i+1,t) for i,t in enumerate(parts) if t.strip()]
        if not pages:
            from library_formats import scan_pdf
            return scan_pdf(path)
    elif ext in ('.docx','.epub'):
        with path.open('rb') as f:
            if f.read(2)!=b'PK':raise ValueError('INVALID_ARCHIVE_TYPE')
        with archive(path) as z:
            if ext=='.docx':
                if '[Content_Types].xml' not in z.namelist() or any(n.lower().endswith('vbaproject.bin') for n in z.namelist()):raise ValueError('INVALID_ARCHIVE_TYPE')
                root=xml(z.read('word/document.xml'))
                ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                text='\n'.join(' '.join(t.text or '' for t in p.iter(ns+'t')) for p in root.iter(ns+'p'))
                if 'docProps/core.xml' in z.namelist():
                    core=xml(z.read('docProps/core.xml'))
                    for e in core.iter():
                        if e.tag.endswith('}creator'):author=(e.text or 'UNKNOWN')[:1000]
                        if e.tag.endswith('}title') and e.text:title=e.text[:1000]
            else:
                if z.read('mimetype')!=b'application/epub+zip':raise ValueError('INVALID_EPUB')
                container=xml(z.read('META-INF/container.xml'))
                rootfile=next(e.attrib['full-path'] for e in container.iter() if e.tag.endswith('}rootfile'))
                opf=xml(z.read(rootfile)); base=Path(rootfile).parent
                items={e.attrib['id']:e.attrib.get('href','') for e in opf.iter() if e.tag.endswith('}item')}
                order=[e.attrib['idref'] for e in opf.iter() if e.tag.endswith('}itemref')]
                parts=[]
                for ident in order[:1000]:
                    relative=Path(items[ident])
                    if relative.is_absolute() or '..' in relative.parts:raise ValueError('ARCHIVE_PATH')
                    parts.append(html_text(z.read(str(base/relative)).decode('utf-8')))
                text='\n'.join(parts)
                for e in opf.iter():
                    if e.tag.endswith('}creator'):author=(e.text or 'UNKNOWN')[:1000]
                    if e.tag.endswith('}title'):title=(e.text or title)[:1000]
        pages=[(None,text)];page_count=None
    else:
        data=path.read_bytes()
        if b'\0' in data or data.startswith((b'%PDF-',b'PK\x03\x04',b'MZ',b'\x7fELF',b'\x89PNG',b'GIF8',b'\xff\xd8')):raise ValueError('BINARY_DOCUMENT')
        text=data.decode('utf-8-sig')
        if ext in ('.html','.htm'):
            if not re.search(r'<(?:!doctype\s+html|html|body|head|p|div|table|section|h[1-6])(?:\s|>)',text,re.I):raise ValueError('MALFORMED_DOCUMENT')
            text=html_text(text)
        elif ext=='.xml':text='\n'.join(t for t in xml(data).itertext() if t.strip())
        elif ext=='.json':text=json.dumps(json.loads(text),ensure_ascii=False,indent=2)
        elif ext=='.csv':text='\n'.join(' | '.join(row) for row in csv.reader(io.StringIO(text)))

        pages=[(None,text)];page_count=None
    if sum(len(t) for _,t in pages)>MAX_TEXT:raise ValueError('TEXT_LIMIT')
    if not any(t.strip() for _,t in pages):raise ValueError('EMPTY_DOCUMENT')
    return {'pages':pages,'page_count':page_count,'author':author,'title':title,'format':ext[1:]}

def extract(path):
    if not __package__:sys.path.insert(0,'/helpers')
    else:

        from . import library_formats
        sys.modules.setdefault('library_formats',library_formats)
    from library_formats import universal,metadata_reference
    try:
        result=extract_known(path) if path.suffix.lower() in SUPPORTED else universal(path,archive,xml)
        if sum(len(t) for _,t in result['pages'])>MAX_TEXT:raise ValueError('TEXT_LIMIT')
        result.setdefault('extraction_level','TEXT');return result
    except Exception as exc:
        note='ENCODING_UNSUPPORTED' if isinstance(exc,UnicodeDecodeError) else 'MALFORMED_JSON' if isinstance(exc,json.JSONDecodeError) else 'MALFORMED_XML' if isinstance(exc,ET.ParseError) else str(exc)
        if not re.fullmatch('[A-Z_]{1,80}',note):note='EXTRACTION_FAILED'
        return metadata_reference(path,note)

if __name__=='__main__':
    try:print(json.dumps(extract(Path(sys.argv[1])),ensure_ascii=False))
    except Exception as exc:

        code='ENCODING_UNSUPPORTED' if isinstance(exc,UnicodeDecodeError) else 'MALFORMED_JSON' if isinstance(exc,json.JSONDecodeError) else 'MALFORMED_XML' if isinstance(exc,ET.ParseError) else str(exc) if str(exc) in {'OCR_REQUIRED','UNSUPPORTED_FORMAT','INPUT_LIMIT','TEXT_LIMIT','EMPTY_DOCUMENT','ARCHIVE_LIMIT','ARCHIVE_PATH','ARCHIVE_RATIO','XML_ENTITY_BLOCKED','BINARY_DOCUMENT','INVALID_PDF','INVALID_EPUB','INVALID_ARCHIVE_TYPE','PDF_EXTRACTION_FAILED'} else 'MALFORMED_DOCUMENT'
        print(json.dumps({'error':code}));sys.exit(2)
