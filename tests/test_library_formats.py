
import os,sys,tempfile,subprocess,zipfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mark.cognition.library import isolated_extract,Library
from mark.cognition.library_formats import IMAGES
from test_library import pdf

class FormatTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_unknown_binary_reference_and_static_strings(self):
        zero=self.root/'fixture.unrecognized';zero.write_bytes(b'\0'*64)
        r=isolated_extract(zero);self.assertEqual(r['extraction_level'],'METADATA_ONLY');self.assertEqual(r['extraction_note'],'NO_READABLE_TEXT')
        binary=self.root/'fixture.dll';binary.write_bytes(b'MZ\0technical capacitores energia circuitos\0')
        r=isolated_extract(binary);self.assertEqual(r['extraction_level'],'PARTIAL_TEXT');self.assertIn('capacitores',str(r['pages']))
    def test_pptx_xlsx_odt_real_parsers_no_macro_execution(self):
        for ext,files in [
            ('pptx',{'ppt/slides/slide1.xml':'<a:slide xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>capacitores energia</a:t></a:slide>'}),
            ('xlsx',{'xl/sharedStrings.xml':'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>capacitores energia</t></si></sst>','xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1"><f>1+2</f><v>3</v></c></row></sheetData></worksheet>'}),
            ('odt',{'content.xml':'<document><p>capacitores energia</p></document>'})]:
            p=self.root/('fixture.'+ext)
            with zipfile.ZipFile(p,'w') as archive:
                for name,content in files.items():archive.writestr(name,content)
            r=isolated_extract(p);self.assertEqual(r['extraction_level'],'TEXT',r);self.assertIn('capacitores',str(r['pages']))
            if ext=='pptx':self.assertEqual(r['pages'][0][0],1)
            if ext=='xlsx':self.assertIn('não recalculada',str(r['pages']))
    def test_archive_text_and_traversal_kept_as_metadata(self):
        good=self.root/'fixture.zip'
        with zipfile.ZipFile(good,'w') as z:z.writestr('guide.txt','capacitores energia');z.writestr('bin.o',b'\0\0')
        r=isolated_extract(good);self.assertEqual(r['extraction_level'],'PARTIAL_TEXT');self.assertIn('guide.txt',str(r['pages']))
        bad=self.root/'traversal.zip'
        with zipfile.ZipFile(bad,'w') as z:z.writestr('../escape','must not extract')
        r=isolated_extract(bad);self.assertEqual(r['extraction_level'],'METADATA_ONLY');self.assertEqual(r['extraction_note'],'ARCHIVE_PATH');self.assertFalse((self.root.parent/'escape').exists())
    def test_real_image_ocr(self):
        from PIL import Image,ImageDraw,ImageFont
        font_path=subprocess.check_output(['fc-match','-f','%{file}','DejaVu Sans'],text=True).strip()
        image=Image.new('RGB',(1600,240),'white');draw=ImageDraw.Draw(image);draw.text((40,80),'CAPACITORES ARMAZENAM ENERGIA',font=ImageFont.truetype(font_path,64),fill='black')
        p=self.root/'fixture.png';image.save(p)
        r=isolated_extract(p);self.assertEqual(r['extraction_level'],'OCR_TEXT',r);self.assertIn('capacitores',str(r['pages']).lower())
    def test_real_audio_transcription_cpu_existing_model(self):
        p=self.root/'fixture.wav'
        subprocess.run(['/usr/bin/espeak','-v','en-us','-s','135','-w',str(p),'The capacitor stores electrical energy. This is a synthetic test.'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        r=isolated_extract(p);self.assertEqual(r['extraction_level'],'TEXT',r);self.assertTrue(any(word in str(r['pages']).lower() for word in ('capacitor','electrical','energy')),r)
        self.assertIn('processed_seconds',r['coverage'])
    def test_corrupt_document_reference_has_honest_warning(self):
        p=self.root/'invalid.pdf';p.write_bytes(b'invalid payload')
        with Library(self.root/'knowledge.sqlite3') as library,library.owner():
            library.enqueue([p])
            while library.process_one():pass
            self.assertEqual(library.stats()['references'],1);self.assertEqual(library.queue()[0]['status'],'REFERENCE_ONLY');self.assertEqual(library.queue()[0]['error'],'INVALID_PDF')

if __name__=='__main__':unittest.main()
