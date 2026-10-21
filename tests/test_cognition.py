import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from mark.cognition.domains import domain, Domain, route_domains
from mark.cognition.schemas import SourceRecord, ChunkRecord, AFVRecord
from mark.cognition.knowledge import KnowledgeStore, freshness, conflict_report, extract_file
from mark.cognition.embeddings import HashEmbeddingProvider
from mark.cognition.tools import default_registry, ActionRequest, ToolRegistry, ToolContract, schema
from mark.cognition.chemistry import molar_mass, formula_counts
from mark.cognition.integration import direct_response, knowledge_context
from mark.cognition.datasets import DatasetSample, validate_samples, split_dataset, check_overlap, deduplicate, load_samples
from mark.cognition.privacy import sanitize, sanitized_candidate
from mark.cognition.providers import ModelProvider, ModelRequest, ModelResponse, ModelMetrics, get_provider
from mark.cognition.benchmark import load_cases, run_benchmark
from mark.cognition.experiments import ExperimentStore, validate_training_config
from mark.memory import ShortMemory
from mark.config import load_config
from mark.brain import answer

class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'source.md'; self.path.write_text('# Linux\nLinux kernel networking diagnostics baseline measurements.',encoding='utf-8')
        self.db=self.root/'knowledge.sqlite3'
    def tearDown(self): self.tmp.cleanup()
    def store(self): return KnowledgeStore(self.db)
    def ingest(self,store,**kwargs):
        args={'title':'Linux manual fixture','license':'USER_OWNED','metadata':{'claims':{'baseline':'observed'}}}; args.update(kwargs)
        return store.ingest(self.path,'computing',**args)
    def source(self,**kwargs):
        data=dict(source_id='s',domain='computing',title='Fixture',license='USER_OWNED',checksum='a'*64); data.update(kwargs)
        return SourceRecord(**data)
    def sample(self,id='s1',prompt='Calcule água',**kwargs):
        data=dict(sample_id=id,dataset_version='0.1',domain='chemistry',difficulty='basic',user_input=prompt,context='',available_tools=['chemistry.molar_mass'],expected_plan=['Use calculator'],expected_tool_calls=[],expected_answer='18.015 g/mol',constraints=[],failure_modes=[],source={'license':'USER_OWNED','provenance':'synthetic test'},review_status='APPROVED'); data.update(kwargs)
        return DatasetSample(**data)
    def test_source_requires_license_and_checksum(self):
        for change in ({'license':'UNKNOWN'},{'checksum':'wrong'},{'title':''},{'domain':'nonsense'},{'schema_version':2},{'publication_date':'bad'}):
            with self.subTest(change=change),self.assertRaises(ValueError): self.source(**change)
    def test_missing_legal_metadata(self):
        with self.assertRaises(ValueError): self.source(domain='law')
    def test_legal_metadata_dates(self):
        metadata=dict(country='BR',jurisdiction='federal',authority='fixture',document='example',article='1',publication_date='2020-01-01',effective_date=None,version_date='2020-01-01',source_url='',source_type='texto legal')
        s=self.source(domain='law',metadata=metadata); self.assertEqual(freshness(s).value,'STALE')
    def test_chunk_validation(self):
        with self.assertRaises(ValueError): ChunkRecord('c','s','computing','',page=0)
        self.assertEqual(ChunkRecord('c','s','computing','text',page=1).schema_version,1)
    def test_afv_historical_record(self):
        self.assertEqual(AFVRecord('Renault FT',country='France',vehicle_class='tank').development_status.value,'UNKNOWN')
    def test_afv_claim_requires_independent_source(self):
        claim=dict(value=1,unit='example',status='INDEPENDENTLY_CONFIRMED',source_id='s',independent_confirmation=False)
        with self.assertRaises(ValueError): AFVRecord('fixture',engine_power=claim,sources=['s'])
        with self.assertRaises(ValueError): AFVRecord('fixture',confidence='HIGH')
    def test_domain_registry_and_alias(self):
        self.assertEqual(domain('afv'),Domain.AFV_ARMOR)
        self.assertEqual(domain('weapons'),Domain.WEAPONS_KNOWLEDGE)
    def test_multidomain_route(self):
        self.assertEqual(set(route_domains('corrosão galvânica num carro').domains),{Domain.CHEMISTRY,Domain.AUTOMOTIVE})
        self.assertTrue({Domain.AFV_ARMOR,Domain.ELECTRONICS} <= set(route_domains('eletrônica de fire control dos blindados').domains))
    def test_general_chat_low_confidence(self):
        decision=route_domains('olá'); self.assertEqual(decision.domains,()); self.assertEqual(decision.confidence,0)
    def test_deduplicate_source(self):
        with self.store() as store:
            one=self.ingest(store); two=self.ingest(store)
            self.assertEqual(two['status'],'DUPLICATE'); self.assertEqual(one['source_id'],two['source_id']); self.assertEqual(store.stats()['sources'],1)
    def test_source_version_and_history(self):
        with self.store() as store:
            first=self.ingest(store); self.path.write_text('# Linux\nLinux updated kernel network measurements.')
            second=self.ingest(store); self.assertEqual(second['version'],2); self.assertEqual(store.stats()['active_sources'],1)
            self.assertTrue(all(h.citation.version==2 for h in store.search('Linux')))
            self.assertEqual({h.citation.version for h in store.search('Linux',include_history=True)},{1,2})
    def test_chunk_linking_and_page_section(self):
        with self.store() as store:
            self.ingest(store); hits=store.search('kernel networking')
            self.assertTrue(hits); self.assertEqual(hits[0].chunk.source_id,hits[0].citation.source_id); self.assertEqual(hits[0].citation.section,'Linux')
    def test_filter_and_no_results(self):
        with self.store() as store:
            self.ingest(store); self.assertFalse(store.search('kernel',domains=['law'])); self.assertFalse(store.search('zzzzunknown')); self.assertFalse(store.search('" OR 1=1 --'))
    def test_lexical_reference_needs_query_coverage(self):
        self.path.write_text('O guia está atual e mostra como conectar MARK no celular.')
        with self.store() as store:
            self.ingest(store,title='Guia MARK')
            self.assertFalse(store.search('Como está o tempo em Curitiba?'))
            self.assertFalse(store.search('cotação atual do euro'))
            self.assertTrue(store.search('conectar MARK celular'))
    def test_conflict_metadata(self):
        with self.store() as store:
            self.ingest(store); self.ingest(store,title='Other',metadata={'claims':{'baseline':'different'}})
            self.assertIn('baseline',conflict_report(store.search('kernel')))
    def test_empty_and_invalid_documents(self):
        with self.store() as store:
            self.path.write_text('')
            with self.assertRaises(ValueError): self.ingest(store)
            self.path.write_text('\x00 invalid')
            with self.assertRaises(ValueError): self.ingest(store)
    def test_symlink_source_and_database_refused(self):
        link=self.root/'linked.md'; link.symlink_to(self.path)
        with self.assertRaises(ValueError): extract_file(link)
        other=self.root/'link.sqlite3'; other.symlink_to(self.db)
        with self.assertRaises(PermissionError): KnowledgeStore(other)
    def test_invalid_pdf(self):
        bad=self.root/'invalid.pdf'; bad.write_text('not pdf')
        with self.assertRaises(ValueError): extract_file(bad)
    def test_read_only_file_permissions(self):
        with self.store(): self.assertEqual(self.db.stat().st_mode & 0o777,0o600)
    def test_reindex(self):
        with self.store() as store:
            self.ingest(store); provider=HashEmbeddingProvider(); provider.version='2'
            store.embedding=provider
            with self.assertRaises(ValueError): store.search('kernel')
            self.assertEqual(store.reindex()['reindexed'],1); self.assertTrue(store.search('kernel'))
    def test_molar_mass_and_parentheses(self):
        for f,value in [('H2O',18.015),('NaCl',58.44),('Ca(OH)2',74.092),('(NH4)2SO4',132.134)]:
            with self.subTest(f=f): self.assertAlmostEqual(molar_mass(f)['value'],value,places=4)
    def test_invalid_formula(self):
        for formula in ('H0','H2O)','(H2O','Xx2','H2O;exec','999','()','H2O+', '(' * 9+'H'+')'*9):
            with self.subTest(formula=formula),self.assertRaises(ValueError): formula_counts(formula)
    def test_tool_authorization(self):
        registry=default_registry(); request=ActionRequest('chemistry.molar_mass',{'formula':'H2O'})
        with self.assertRaises(PermissionError): registry.invoke(request)
        self.assertEqual(registry.invoke(request,{'tools.calculate'})['value'],18.015)
    def test_tool_schema_and_no_arbitrary_dispatch(self):
        for req in (ActionRequest('os.system',{}),ActionRequest('chemistry.molar_mass',{'formula':'H2O','extra':1}),ActionRequest('chemistry.stoichiometry',{'known_moles':1,'known_coefficient':0,'target_coefficient':1})):
            with self.subTest(req=req),self.assertRaises(ValueError): default_registry().invoke(req,{'tools.calculate'})
    def test_nonfinite_tool_inputs_denied(self):
        with self.assertRaises(ValueError): default_registry().invoke(ActionRequest('chemistry.unit_conversion',{'value':float('nan'),'from_unit':'g','to_unit':'kg'}),{'tools.calculate'})
    def test_unit_and_stoichiometry(self):
        r=default_registry()
        self.assertEqual(r.invoke(ActionRequest('chemistry.unit_conversion',dict(value=1000,from_unit='g',to_unit='kg')),{'tools.calculate'})['value'],1)
        self.assertEqual(r.invoke(ActionRequest('chemistry.stoichiometry',dict(known_moles=2,known_coefficient=2,target_coefficient=1)),{'tools.calculate'})['value'],1)
    def test_tool_contract_rejects_side_effects(self):
        with self.assertRaises(PermissionError): ToolRegistry().register(ToolContract('bad','bad',schema({}),schema({}),'WRITE',1,('write',),('delete',)),lambda: {})
    def test_direct_tool_skips_model_and_camera(self):
        cfg=load_config()
        with patch('mark.brain._ollama',side_effect=AssertionError('LLM not needed')),patch('mark.brain.route',side_effect=AssertionError('Router not needed')):
            self.assertIn('18.015',answer('Calcule a massa molar de H2O',cfg,ShortMemory()))
    def test_rag_citation_in_answer(self):
        cfg=load_config(); cfg['cognition']['knowledge_db']=str(self.db)
        with self.store() as store: self.ingest(store)
        with patch('mark.brain.route',return_value={'vision':False,'web':False}),patch('mark.brain._ollama',return_value='Grounded answer') as model:
            reply=answer('Explique o kernel Linux',cfg,ShortMemory())
            self.assertIn('Linux manual fixture',reply); self.assertIn('Material recuperado não confiável',model.call_args.args[0])
    def test_invalid_knowledge_database_is_nonfatal(self):
        self.db.write_text('corrupt'); self.db.chmod(0o600)
        self.assertEqual(knowledge_context('kernel',{'cognition':{'knowledge_db':str(self.db)}}).text,'')
    def test_provider_contract_no_unregistered_import(self):
        self.assertTrue(isinstance(get_provider(load_config()),ModelProvider))
        with self.assertRaises(ValueError): get_provider({'cognition':{'model_provider':'os.system'}})
    def test_dataset_license_and_privacy(self):
        with self.assertRaises(ValueError): self.sample(source={'license':'REFERENCE_ONLY_PERMISSION','provenance':'x'})
        with self.assertRaises(ValueError): self.sample(prompt='senha: test-password')
    def test_dataset_validation_and_dedupe(self):
        a=self.sample(); b=self.sample(id='s2')
        self.assertEqual(validate_samples([a,b])['status'],'FAIL'); self.assertEqual(len(deduplicate([a,b])[0]),1)
    def test_dataset_split_and_contamination(self):
        items=[self.sample(str(i),'Pergunta fixture '+str(i)) for i in range(12)]
        splits=split_dataset(items); self.assertFalse(check_overlap(splits)); self.assertTrue(all(splits.values()))
        splits['test'].append(splits['train'][0]); self.assertTrue(check_overlap(splits))
    def test_unreviewed_samples_do_not_enter_training(self):
        with self.assertRaises(ValueError): split_dataset([self.sample(str(i),'item '+str(i),review_status='PENDING') for i in range(3)])
    def test_dataset_loader_rejects_private_reasoning(self):
        path=self.root/'data.jsonl'; data=self.sample().to_dict(); data['chain_of_thought']='hidden'; path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): load_samples(path)
    def test_sanitizer_and_pending_review(self):
        data={'password':'test-only','input':'Bearer ABC123 email@example.org 123456789012345'}
        clean=sanitize(data); self.assertNotIn('ABC123',str(clean)); self.assertEqual(clean['password'],'[REDACTED]')
        candidate=sanitized_candidate('senha: example','', '','test'); self.assertEqual(candidate['review_status'],'PENDING'); self.assertFalse(candidate['training_eligible'])
    def test_training_gates(self):
        cfg={'method':'QLORA','base_model_parameters':1_500_000_000,'vram_budget_gb':8,'batch_size':1,'gradient_accumulation':8,'full_parameter_training':False}
        self.assertEqual(validate_training_config(cfg,{'status':'PASS','reviewed':True},{'status':'PASS','model_calls':1,'security_gate':True})['status'],'PASS')
        self.assertEqual(validate_training_config(cfg,{'status':'FAIL'}, {})['status'],'FAIL')
    def test_benchmark_runner_component_suite(self):
        cases=load_cases(Path(__file__).resolve().parents[1]/'benchmarks/markbench-v0.1.json')
        self.assertEqual(len(cases),50)
        report=run_benchmark(cases); self.assertEqual(report['counts'],{'PASS':42,'FAIL':0,'SKIPPED':8}); self.assertEqual(report['status'],'PARTIAL')
    def simple_pdf(self,text='Linux PDF reference'):
        stream=('BT /F1 12 Tf 40 700 Td ('+text+') Tj ET').encode()
        objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
        data=b'%PDF-1.4\n'; offsets=[0]
        for i,obj in enumerate(objects,1): offsets.append(len(data)); data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        xref=len(data); data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'
        path=self.root/'fixture.pdf'; path.write_bytes(data); return path
    @unittest.skipUnless(shutil.which('pdftotext'),'Poppler unavailable')
    def test_pdf_text_ingestion_preserves_page(self):
        pdf=self.simple_pdf()
        with self.store() as store:
            store.ingest(pdf,'computing',title='PDF fixture',license='USER_OWNED')
            self.assertEqual(store.search('Linux')[0].citation.page,1)
    @unittest.skipUnless(shutil.which('pdftotext'),'Poppler unavailable')
    def test_pdf_without_text_requires_ocr(self):
        with self.assertRaisesRegex(ValueError,'OCR_REQUIRED'): extract_file(self.simple_pdf(''))
    def test_readonly_tools_and_database(self):
        with self.store() as store: self.ingest(store)
        registry=default_registry(self.db)
        with self.assertRaises(PermissionError): registry.invoke(ActionRequest('knowledge.search',{'query':'Linux'}))
        self.assertTrue(registry.invoke(ActionRequest('knowledge.search',{'query':'Linux'}),{'knowledge.read'})['hits'])
        with KnowledgeStore(self.db,read_only=True) as store:
            self.path.write_text('changed')
            import sqlite3
            with self.assertRaises(sqlite3.OperationalError): self.ingest(store)
    def test_source_claim_and_freshness_validation(self):
        for metadata in ({'claim_status':'marketing'},{'source_tier':'tierX'},{'last_verified':'invalid'}):
            with self.assertRaises(ValueError): self.source(metadata=metadata)
        with self.assertRaises(ValueError): self.source(reliability='HIGH')
    def test_tool_timeout(self):
        import time
        registry=ToolRegistry()
        registry.register(ToolContract('test.slow','test',schema({}),schema({}),'READ_ONLY',.01,('test.read',)),lambda: time.sleep(.02) or {})
        with self.assertRaises(TimeoutError): registry.invoke(ActionRequest('test.slow',{}),{'test.read'})
    def test_benchmark_failed_provider_preserves_metrics_and_gate(self):
        class FailedProvider(ModelProvider):
            last_metrics=None
            def generate(self,request):
                self.last_metrics=ModelMetrics('fake',1,4,5,2,False)
                raise RuntimeError('empty')
        cases=load_cases(Path(__file__).resolve().parents[1]/'benchmarks/markbench-v0.1.json')
        report=run_benchmark([cases[39]],FailedProvider()); self.assertEqual(report['status'],'FAIL'); self.assertEqual(report['model_calls'],2); self.assertEqual(report['output_tokens'],5)
    def test_telemetry_excludes_content_and_secrets(self):
        from mark.cognition import telemetry
        path=self.root/'metrics.jsonl'
        with patch.object(telemetry,'DEFAULT_LOG',path):
            telemetry.record_metrics({'model':'test','prompt':'private','password':'test','latency_ms':1}, {'cognition':{'telemetry':True}})
        data=json.loads(path.read_text()); self.assertNotIn('prompt',data); self.assertNotIn('password',data)
    def test_chemistry_api_does_not_wake_qwen(self):
        from mark.mobile_api import MobileHandler
        from types import SimpleNamespace
        cfg=load_config(); session=SimpleNamespace(awake=False,wake=unittest.mock.Mock())
        handler=object.__new__(MobileHandler)
        handler.server=SimpleNamespace(cfg=cfg,model_cfgs={},model_sessions={},model_session=session,agenda=None)
        with patch('mark.mobile_api.handle_agenda_command',return_value=None),patch('mark.mobile_api.sleep_command',return_value=False):
            self.assertIn('18.015',handler._answer('massa molar de H2O',ShortMemory()))
        session.wake.assert_not_called()
    def test_local_rag_remains_available_when_web_fails(self):
        cfg=load_config(); cfg['cognition']['knowledge_db']=str(self.db)
        with self.store() as store: self.ingest(store)
        from mark.web import FAILED
        with patch('mark.brain.route',return_value={'vision':False,'web':True}),patch('mark.brain.search',return_value=FAILED),patch('mark.brain._ollama',return_value='Local reference'):
            self.assertIn('Linux manual fixture',answer('Linux kernel notícias recentes',cfg,ShortMemory()))
    def test_provider_lifecycle_keeps_historical_request_contract(self):
        from io import BytesIO
        from mark.model_state import _model_command
        cfg=load_config()
        with patch('mark.model_state.urllib.request.urlopen',return_value=BytesIO(b'{}')) as opened:
            _model_command(cfg,-1)
        payload=json.loads(opened.call_args.args[0].data)
        self.assertEqual(payload['keep_alive'],-1); self.assertEqual(payload['messages'],[])
    def test_dataset_tool_arguments_cannot_hide_secrets(self):
        with self.assertRaises(ValueError): self.sample(expected_tool_calls=[{'tool':'example','arguments':{'password':'fixture-secret'}}])
    def test_experiment_id_is_immutable_under_concurrent_creation(self):
        from mark.cognition.experiments import Experiment
        from concurrent.futures import ThreadPoolExecutor
        exp=Experiment('EXP-0001','fixture','small','synthetic','1','BASELINE','NONE',None,None,None,None,{},1,None,None,None,1,1,'test fixture','PASS')
        store=ExperimentStore(self.root/'experiments')
        def create(_):
            try: store.add(exp); return 'created'
            except ValueError: return 'duplicate'
        with ThreadPoolExecutor(max_workers=2) as pool: outcomes=list(pool.map(create,range(2)))
        self.assertEqual(outcomes.count('created'),1); self.assertEqual(len(store.list()),1)
    def test_stopwords_do_not_create_irrelevant_citations(self):
        self.path.write_text('# Linux\nThe diagnostics of the Linux kernel are a source of facts.')
        with self.store() as store:
            self.ingest(store); self.assertEqual(store.search('the of are a'),[])
            self.assertTrue(store.search('Linux diagnostics'))

    def test_p0_granular_mcp_capability_authorization_per_tool_and_per_mission(self):
        from mark.cognition.tools import ToolCapabilityError
        from mark.mcp_sandbox import SandboxMCPBridge

        registry = default_registry(self.db)
        # 1. Emitir CapabilityToken restrito exclusivamente a chemistry.molar_mass para a missão M-101
        cap_m101 = registry.issue_mission_capability(
            "mission-101",
            allowed_tools=["chemistry.molar_mass"],
            ttl_seconds=300.0,
        )
        res = registry.invoke(
            ActionRequest("chemistry.molar_mass", {"formula": "H2O"}, mission_id="mission-101"),
            grants=cap_m101,
        )
        self.assertEqual(res["value"], 18.015)

        # 2. Mesmo compartilhando a permissão 'tools.calculate', chemistry.density deve ser NEGADA para o token de mission-101
        with self.assertRaises(ToolCapabilityError):
            registry.invoke(
                ActionRequest(
                    "chemistry.density",
                    {"mass": 10, "mass_unit": "g", "volume": 2, "volume_unit": "mL"},
                    mission_id="mission-101",
                ),
                grants=cap_m101,
            )

        # 3. Reuso de CapabilityToken em outra missão (mission-999) deve ser bloqueado
        with self.assertRaises(ToolCapabilityError):
            registry.invoke(
                ActionRequest("chemistry.molar_mass", {"formula": "H2O"}, mission_id="mission-999"),
                grants=cap_m101,
            )

        # 4. Argumentos tentando acessar arquivos privados/segredos são bloqueados mesmo com permissão válida
        with self.assertRaises(ToolCapabilityError):
            registry.invoke(
                ActionRequest("knowledge.search", {"query": "../../etc/shadow"}),
                grants=frozenset({"knowledge.read"}),
            )

        # 5. Ponte MCP em modo estrito (require_explicit_capabilities=True) nega chamadas sem CapabilityToken
        bridge = SandboxMCPBridge(workspace=self.root, require_explicit_capabilities=True)
        with self.assertRaises(ToolCapabilityError):
            bridge.execute_tool("sandbox_run_pytest", {"target": "tests"})

        # E nega ferramentas privilegiadas (engineer_solve_task) se allow_privileged=False
        cap_ro = bridge.issue_mission_capability(
            "mission-ro",
            allowed_tools=["engineer_solve_task"],
            allow_privileged=False,
        )
        with self.assertRaises(ToolCapabilityError):
            bridge.validate_call(
                "engineer_solve_task",
                {"task": "Teste", "target_file": "app.py"},
                grants=cap_ro,
                mission_id="mission-ro",
            )

