
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mark.cognition.datasets import DatasetSample, load_samples
from mark.cognition.training import approved_split,validate_paths,MODEL_ID
from mark.cognition.providers import OllamaProvider, ModelRequest

class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.dataset=self.root/'approved.jsonl';self.approval=self.root/'approval.json'
        rows=[]
        for i in range(20):
            rows.append(DatasetSample(str(i),'fixture','computing','basic',f'Pergunta de teste {i}','',[],[],[],f'Resposta {i}',[],[],{'license':'USER_OWNED','provenance':'authored fixture','family':f'family-{i//2}'},'APPROVED').to_dict())
        self.dataset.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.approval.write_text(json.dumps({'status':'PASS','reviewed':True,'reviewed_by':'owner','approved_sha256':hashlib.sha256(self.dataset.read_bytes()).hexdigest()}))
    def tearDown(self):self.tmp.cleanup()
    def test_disjoint_task_families(self):
        result=approved_split(self.dataset,self.approval,self.root/'split')
        self.assertEqual(sum(result['counts'].values()),20)
        families=[{r.source['family'] for r in load_samples(self.root/'split'/(n+'.jsonl'))} for n in ('train','validation','test')]
        for i,j in ((0,1),(0,2),(1,2)):self.assertFalse(families[i]&families[j])
        self.assertEqual((self.root/'split/train.jsonl').stat().st_mode&0o777,0o600)
    def test_modified_dataset_requires_fresh_approval(self):
        self.dataset.write_text(self.dataset.read_text()+'\n')
        with self.assertRaises(PermissionError):approved_split(self.dataset,self.approval,self.root/'split')
    def test_automatic_approval_rejected(self):
        data=json.loads(self.approval.read_text());data['reviewed_by']='model';self.approval.write_text(json.dumps(data))
        with self.assertRaises(PermissionError):approved_split(self.dataset,self.approval,self.root/'split')
    def validation_fixture(self):
        cfg={'executor':'hf-qlora-v02','base_model':MODEL_ID,'method':'QLORA','base_model_parameters':4_400_000_000,'vram_budget_gb':7.5,'batch_size':1,'gradient_accumulation':4,'full_parameter_training':False,'epochs':1,'vision_frozen':True,'automatic_activation':False,'lora_rank':8,'max_sequence_length':256,'learning_rate':.00001}
        config=self.root/'config.json';config.write_text(json.dumps(cfg))
        baseline=self.root/'baseline.json';baseline.write_text(json.dumps({'status':'PASS','model_calls':1,'security_gate':True}))
        split=self.root/'split';approved_split(self.dataset,self.approval,split)
        base=self.root/'base';base.mkdir();files=[]
        for name in ('config.json','tokenizer.json','model.safetensors.index.json','weights.safetensors'):
            data=b'fixture';(base/name).write_bytes(data)
            files.append({'rfilename':name,'size':len(data),'lfs':{'sha256':hashlib.sha256(data).hexdigest()}})
        (base/'snapshot-manifest.json').write_text(json.dumps({'model':MODEL_ID,'revision':'a'*40,'license':'Apache-2.0','files':files}))
        return config,self.approval,baseline,base,split
    def test_validated_inputs_do_not_load_model(self):
        cfg,rows,report=validate_paths(*self.validation_fixture())
        self.assertEqual(sum(report['counts'].values()),20);self.assertEqual(report['gate']['status'],'PASS')
    def test_changed_split_cannot_enter_training(self):
        args=self.validation_fixture();path=args[-1]/'train.jsonl'
        rows=[json.loads(line) for line in path.read_text().splitlines()];rows[0]['expected_answer']='Resposta alterada'
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        with self.assertRaisesRegex(ValueError,'conteúdo aprovado'):validate_paths(*args)
    def test_changed_base_weights_denied(self):
        args=self.validation_fixture();(args[-2]/'weights.safetensors').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Checksum'):validate_paths(*args)
    def test_manifest_path_escape_denied(self):
        args=self.validation_fixture();path=args[-2]/'snapshot-manifest.json';data=json.loads(path.read_text())
        data['files'].append({'rfilename':'../outside','size':1});path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'fora da base'):validate_paths(*args)
    def test_fallback_preserves_request_and_metrics(self):
        cfg={'ollama':{'model':'slow','url':'http://localhost:11434','timeout':1,'think':True},'models':{'chat':'slow','call':'fast'}}
        import base64
        from PIL import Image
        from mark.vision_quality import prepare_frame
        encoded=BytesIO();Image.new('RGB',(32,32),'cyan').save(encoded,'JPEG')
        fixture=base64.b64encode(encoded.getvalue()).decode()
        prepared=base64.b64encode(prepare_frame(encoded.getvalue())).decode()
        captured=[]
        def response(req,**kwargs):
            payload=json.loads(req.data);captured.append(payload)
            content='Resposta' if payload['model']=='fast' else ''
            return BytesIO(json.dumps({'message':{'content':content},'prompt_eval_count':3,'eval_count':2}).encode())
        provider=OllamaProvider(cfg)
        with patch('mark.cognition.providers.urllib.request.urlopen',side_effect=response),patch('mark.model_state.ModelSession') as sessions:
            reply=provider.generate(ModelRequest('Texto do usuário',system='Contexto',images=(fixture,)))
        self.assertEqual(reply.text,'Resposta');self.assertEqual(reply.metrics.attempts,3)
        self.assertEqual(reply.metrics.prompt_tokens,9);self.assertEqual(reply.metrics.fallback_from,'slow')
        self.assertEqual(captured[-1]['messages'][-1]['content'],'Texto do usuário')
        self.assertEqual(captured[-1]['messages'][-1]['images'],[prepared])
        self.assertFalse(captured[-1]['think']);sessions.return_value.sleep.assert_called_once()
        self.assertEqual(cfg['ollama']['model'],'slow')
    def test_failed_fallback_is_bounded_and_recorded(self):
        cfg={'ollama':{'model':'slow','url':'http://localhost:11434','timeout':1},'models':{'chat':'slow','call':'fast'}}
        def response(*args,**kwargs):return BytesIO(b'{"message":{"content":""},"eval_count":2}')
        provider=OllamaProvider(cfg)
        with patch('mark.cognition.providers.urllib.request.urlopen',side_effect=response) as opened,patch('mark.model_state.ModelSession') as sessions:
            with self.assertRaises(RuntimeError):provider.generate(ModelRequest('Exemplo'))
        self.assertEqual(opened.call_count,4);self.assertEqual(provider.last_metrics.attempts,4)
        self.assertFalse(provider.last_metrics.success);sessions.return_value.sleep.assert_called_once()
    def test_disabled_fallback_does_not_use_other_model(self):
        cfg={'ollama':{'model':'slow','url':'http://localhost:11434','timeout':1},'models':{'chat':'slow','call':'fast'},'cognition':{'empty_fallback':False}}
        with patch('mark.cognition.providers.urllib.request.urlopen',side_effect=lambda *a,**k:BytesIO(b'{"message":{"content":""}}')),patch('mark.model_state.ModelSession') as sessions:
            with self.assertRaises(RuntimeError):OllamaProvider(cfg).generate(ModelRequest('Exemplo'))
        sessions.assert_not_called()

if __name__=='__main__':unittest.main()
