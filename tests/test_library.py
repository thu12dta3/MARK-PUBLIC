
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mark.cognition.library import Library,safe_path,isolated_extract
from mark.cognition.knowledge import KnowledgeStore
from mark.cognition.integration import knowledge_context


def pdf(path,text,pages=1):
    bodies=[b'<< /Type /Catalog /Pages 2 0 R >>',None,b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids=[]
    for i in range(pages):
        pageid=len(bodies)+1;contentid=pageid+1;kids.append(f'{pageid} 0 R')
        stream=f'BT /F1 12 Tf 50 750 Td ({text} page {i+1}) Tj ET'.encode('ascii') if text is not None else b'q Q'
        bodies += [f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {contentid} 0 R >>'.encode(),b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    bodies[1]=f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {pages} >>'.encode()
    out=b'%PDF-1.4\n';offsets=[0]
    for i,body in enumerate(bodies,1):offsets.append(len(out));out+=str(i).encode()+b' 0 obj\n'+body+b'\nendobj\n'
    start=len(out);out+=f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    out+=b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets[1:]);out+=f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode();path.write_bytes(out)

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'knowledge.sqlite3';self.queue=self.root/'library'
    def tearDown(self):self.tmp.cleanup()
    def library(self):return Library(self.db,self.queue)
    def text(self,name='manual.txt',text='capacitores energia circuito unicode ação 日本語'):
        p=self.root/name;p.write_text(text);return p
    def drain(self,l):
        with l.owner():
            while l.process_one():pass
    def test_reference_dedupe_change_collection_page_removal(self):
        p=self.root/'capacitores.pdf';pdf(p,'capacitores energia circuito',2)
        copy=self.root/'copy.pdf';copy.write_bytes(p.read_bytes())
        with self.library() as l:
            l.enqueue([p,copy],'Eletrônica');self.drain(l)
            self.assertEqual(l.stats()['indexed'],1);self.assertEqual(l.stats()['queue']['DUPLICATE'],1);self.assertEqual(l.stats()['managed_bytes'],0)
            docs=l.documents();self.assertEqual(docs[0]['pages'],2);id_=docs[0]['id']
            cfg={'cognition':{'knowledge_db':str(self.db)}}
            context=knowledge_context('capacitores energia',cfg);self.assertTrue(context.citations);self.assertEqual(context.citations[0].page,1)
            l.enable(id_,False);self.assertFalse(knowledge_context('capacitores energia',cfg).citations)
            l.enable(id_,True);l.collection('Eletrônica',False);self.assertFalse(knowledge_context('capacitores energia',cfg).citations)
            l.collection('Eletrônica',True)
            l.enqueue([p],'Química');self.drain(l)
            with KnowledgeStore(self.db) as store:self.assertEqual(len({h.citation.source_id for h in store.search('capacitores energia',collections=['Química'])}),1)
            pdf(p,'capacitores energia alteracao',2);l.enqueue([p],'Eletrônica');self.drain(l)
            doc=next(d for d in l.documents() if d['collection']=='Eletrônica');self.assertEqual(doc['metadata']['version'],2)
            l.remove(id_);self.assertTrue(p.exists());self.assertEqual(l.stats()['indexed'],1)
    def test_resume_pause_cancel_error_and_missing_original(self):
        p=self.text();bad=self.root/'bad.pdf';bad.write_bytes(b'not a PDF')
        with self.library() as l:
            l.enqueue([p,bad]);l.pause();self.assertFalse(l.process_one());l.pause(False)
            with l.q:l.q.execute("UPDATE imports SET status='RUNNING' WHERE id=1")
        with self.library() as l:
            self.drain(l);self.assertEqual(l.stats()['indexed'],2);self.assertEqual(l.stats()['errors'],0);self.assertEqual(l.stats()['references'],1)
            doc=next(d for d in l.documents() if d['original']==str(p));p.unlink();self.assertEqual(next(d for d in l.documents() if d['id']==doc['id'])['origin_status'],'UNAVAILABLE')
            new=self.text('moved.txt');l.relocate(doc['id'],new);self.assertEqual(next(d for d in l.documents() if d['id']==doc['id'])['origin_status'],'AVAILABLE')
            l.enqueue([new],'Other');l.cancel();self.assertEqual(l.stats()['queue']['CANCELLED'],1);l.retry();self.drain(l)
            self.assertEqual(l.stats()['indexed'],3)
    def test_safety_unknown_symlink_traversal_and_size(self):
        p=self.text();link=self.root/'link.txt';link.symlink_to(p);exe=self.root/'app.exe';exe.write_bytes(b'MZ')
        with self.library() as l:
            l.enqueue([link,exe,self.root/'..'/'escape.txt']);self.assertEqual(l.stats()['errors'],2);self.assertEqual(l.stats()['unsupported'],0)
            huge=self.root/'huge.txt'
            with huge.open('wb') as f:f.truncate(65*1024**2)
            l.enqueue([huge]);self.drain(l);self.assertEqual(l.queue()[0]['error'],'INPUT_LIMIT')
        with self.assertRaises(ValueError):safe_path(link)
    def test_docx_tables_and_formats(self):
        doc=self.root/'table.docx'
        with zipfile.ZipFile(doc,'w') as z:
            z.writestr('[Content_Types].xml','<Types/>');z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>capacitores ação</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        paths=[doc]
        for ext,text in [('html','<html><script>secret malicious()</script><p>capacitores ação</p></html>'),('csv','item,valor\ncapacitores,10'),('json','{"capacitores":10}'),('yaml','capacitores: 10'),('xml','<a>capacitores 10</a>'),('md','# Energia\ncapacitores ação')]:paths.append(self.text('fixture.'+ext,text))
        with self.library() as l:
            l.enqueue(paths);self.drain(l);self.assertEqual(l.stats()['indexed'],len(paths),l.queue())
            with KnowledgeStore(self.db) as s:
                hits=s.search('capacitores ação');self.assertTrue(hits)
                self.assertFalse(any('secret malicious' in h.chunk.text for h in s.search('secret malicious')))
    def test_zip_bomb_entities_and_no_raw_error(self):
        p=self.root/'bomb.docx'
        with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('word/document.xml','A'*2_000_000)
        xml=self.text('bad.xml','<!DOCTYPE a [<!ENTITY x SYSTEM "file:///home/secret">]><a>&x;</a>')
        with self.library() as l:
            l.enqueue([p,xml]);self.drain(l);self.assertEqual(l.stats()['references'],2);self.assertEqual(l.stats()['errors'],0)
            errors=str(l.queue());self.assertNotIn('SYSTEM',errors);self.assertNotIn('file:///home/secret',errors)
    def test_worker_crash_retains_queue_backup_preserves_legacy(self):
        p=self.text()
        with KnowledgeStore(self.db) as store:store.ingest(p,'computing',title='Legacy',license='USER_OWNED')
        with self.library() as l:
            self.assertTrue((self.queue/'knowledge-before-library.sqlite3').exists());l.enqueue([p])
            with patch('mark.cognition.library.isolated_extract',side_effect=RuntimeError('private secret document text')):self.drain(l)
            self.assertEqual(l.queue()[0]['error'],'IMPORT_FAILED');l.retry();self.drain(l);self.assertEqual(l.stats()['indexed'],1)
        with KnowledgeStore(self.queue/'knowledge-before-library.sqlite3',read_only=True) as backup:self.assertEqual(backup.stats()['sources'],1)
    def test_managed_kept_until_explicit_deletion(self):
        p=self.text()
        with self.library() as l:
            l.enqueue([p],mode='managed');self.drain(l);doc=l.documents()[0];managed=Path(doc['managed']);self.assertTrue(managed.exists());p.unlink()
            self.assertEqual(l.documents()[0]['origin_status'],'AVAILABLE');self.assertGreater(l.stats()['managed_bytes'],0)
            l.remove(doc['id']);self.assertTrue(managed.exists())
    def test_cancel_cooperative_during_snapshot(self):
        p=self.text()
        with self.library() as l:
            l.enqueue([p]);l.process_one(cancel_item=lambda _:True);self.assertEqual(l.queue()[0]['status'],'CANCELLED');self.assertEqual(l.stats()['indexed'],0)

    def test_chapter_title_and_collection_prompt(self):
        p=self.text('Livro.txt','Capítulo 4 Capacitores\ncapacitores energia circuito\nCapítulo 5 Resistores\nresistores dissipacao')
        with self.library() as l:
            l.enqueue([p],'Química');self.drain(l)
            cfg={'cognition':{'knowledge_db':str(self.db)}}
            context=knowledge_context('Resuma o capítulo 4 do livro "Livro"',cfg)
            self.assertTrue(context.citations);self.assertIn('4',context.citations[0].section)
            self.assertNotIn('resistores dissipacao',context.text)
            self.assertTrue(knowledge_context('Procure na coleção de Química. capacitores energia',cfg).citations)
            self.assertFalse(knowledge_context('Procure na coleção de Inexistente. capacitores',cfg).citations)
    def test_parallel_workers_and_same_checksum(self):
        import threading
        p=self.text();copy=self.text('copy.txt');
        with self.library() as l:
            l.concurrency(2);l.enqueue([p,copy]);errors=[]
            def process():
                try:
                    with self.library() as worker:
                        while worker.process_one():pass
                except Exception as exc:errors.append(type(exc).__name__)
            with l.owner():
                workers=[threading.Thread(target=process) for _ in range(2)]
                for worker in workers:worker.start()
                for worker in workers:worker.join(10)
            self.assertFalse(errors);self.assertEqual(l.stats()['indexed'],1);self.assertEqual(l.stats()['queue']['DUPLICATE'],1)
    def test_blank_pdf_and_missing_isolation(self):
        p=self.root/'blank.pdf';pdf(p,None)
        with self.library() as l:
            l.enqueue([p]);self.drain(l);self.assertEqual(l.queue()[0]['error'],'OCR_NO_TEXT');self.assertEqual(l.queue()[0]['status'],'REFERENCE_ONLY')

        with patch('mark.cognition.library.shutil.which',return_value=None):
            with self.assertRaisesRegex(ValueError,'ISOLATION_UNAVAILABLE'):isolated_extract(p)


    def test_epub_valid_and_limited_rag_only_uses_model(self):
        p=self.root/'book.epub'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('mimetype','application/epub+zip')
            z.writestr('META-INF/container.xml','<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
            z.writestr('OEBPS/content.opf','<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="ch" href="chapter.xhtml"/></manifest><spine><itemref idref="ch"/></spine></package>')
            z.writestr('OEBPS/chapter.xhtml','<html><p>capacitores energia circuito</p></html>')
        with self.library() as l:
            l.enqueue([p]);self.drain(l);self.assertEqual(l.stats()['indexed'],1)
            from mark.brain import answer
            from mark.memory import ShortMemory
            cfg={'cognition':{'knowledge_db':str(self.db)}}
            with patch('mark.brain._ollama',return_value='Resposta da biblioteca') as model,patch('mark.brain.route') as route,patch('mark.brain.search') as web:
                result=answer('Use somente minha biblioteca: capacitores energia',cfg,ShortMemory())
                self.assertIn('Resposta da biblioteca',result);self.assertIn('book',result);self.assertEqual(model.call_count,1);route.assert_not_called();web.assert_not_called()
            with patch('mark.brain._ollama') as model:
                result=answer('Use somente minha biblioteca: assuntoinexistente',cfg,ShortMemory());self.assertIn('Não encontrei',result);model.assert_not_called()


    def test_code_reference_never_executes_and_legacy_queue_reclassified(self):
        marker=self.root/'must-not-exist'
        code=self.text('reference.py',f'from pathlib import Path\nPath({str(marker)!r}).touch()\n# capacitores energia')
        binary=self.root/'program.o';binary.write_bytes(b'\x7fELF fixture')
        with self.library() as l:
            with l.q:
                l.q.execute("DELETE FROM settings WHERE key='classifier_version'")
                for p in (code,binary):l.q.execute("INSERT INTO imports(path,collection,mode,status,error,created) VALUES(?,'Geral','reference','ERROR','UNSUPPORTED_FORMAT',0)",(str(p),))
        with self.library() as l:
            self.assertEqual(l.stats()['unsupported'],0);self.assertEqual(l.stats()['errors'],0);self.assertTrue((l.root/'imports-before-classifier-v3.sqlite3').exists())
            self.drain(l);self.assertEqual(l.stats()['indexed'],2);self.assertFalse(marker.exists())
    def test_named_text_reference(self):
        p=self.text('README','capacitores energia')
        with self.library() as l:l.enqueue([p]);self.drain(l);self.assertEqual(l.stats()['indexed'],1,l.queue())

    def test_heading_only_file_preserved_as_reference(self):
        p=self.text('onlyheadings', '# alpha\n# beta\n')
        with self.library() as l:
            l.enqueue([p]);self.drain(l)
            self.assertEqual(l.stats()['errors'],0)
            self.assertEqual(l.stats()['references'],1)
            self.assertEqual(l.documents()[0]['metadata']['metadata']['extraction_note'],'NO_INDEXABLE_BODY')
            self.assertTrue(p.exists())
    def test_queue_backup_on_classifier_change_without_errors(self):
        with self.library() as l:
            l.enqueue([self.text()])
            with l.q:l.q.execute("DELETE FROM settings WHERE key='classifier_version'")
        with self.library() as l:
            self.assertTrue((l.root/'imports-before-classifier-v3.sqlite3').exists())
            self.assertEqual(l.stats()['queue']['QUEUED'],1)

class BulkLibraryTests(LibraryTests):
    def test_500_real_pdfs(self):
        paths=[]
        for i in range(500):
            p=self.root/f'book-{i:04d}.pdf';pdf(p,f'capacitores energia fixture {i}');paths.append(p)
        started=time.monotonic()
        with self.library() as l:
            l.enqueue(paths);self.drain(l);stats=l.stats();self.assertEqual(stats['indexed'],500,l.queue());self.assertEqual(stats['errors'],0)
            with KnowledgeStore(self.db) as store:self.assertTrue(store.search('capacitores energia'))
            print('\nBULK_LIBRARY_METRICS '+json.dumps({**{k:stats[k] for k in ('indexed','errors','knowledge_bytes','queue_bytes','managed_bytes','backup_bytes')},'duration_seconds':time.monotonic()-started,'coordinator_peak_kib':__import__('resource').getrusage(__import__('resource').RUSAGE_SELF).ru_maxrss,'extractor_peak_kib':__import__('resource').getrusage(__import__('resource').RUSAGE_CHILDREN).ru_maxrss}),flush=True)

if __name__=='__main__':unittest.main()
