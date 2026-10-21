
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from mark.evolution.state import State
from mark.evolution.engine import Engine
from mark.evolution.promotion import prepare_release,apply_release,digest
from mark.evolution.code_worker import clean_revision
from mark.evolution.sandbox import ExperimentCancelled


class ReplaySandbox:
    def __init__(self,*_):pass
    def probe(self,*_):return {'private_home_visible':False}
    def call(self,source,payload,cancel=lambda:False):
        if cancel():raise ExperimentCancelled()
        if payload['operation']=='contracts':return {'result':{'unauthorized_tool_denied':True}}
        fixed='isinstance' in (Path(source)/'src/mark/cognition/providers.py').read_text()
        broken='return ""' in (Path(source)/'src/mark/cognition/providers.py').read_text()
        result={'content':None,'error':'RuntimeError','calls':0,'thinking_preserved':True,'generation_budget_preserved':True}
        for row in payload['replies']:
            result['calls']+=1
            content=row.get('message',{}).get('content','')
            if not isinstance(content,str):
                if fixed:continue
                result['error']='AttributeError';break
            if broken:continue
            if '<think>' in content:content=content.rsplit('</think>',1)[-1] if '</think>' in content else ''
            if content.strip():result['content']=content.strip();result['error']=None;break
        return {'result':result,'wall_seconds':.01}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.repo=self.root/'repo';self.repo.mkdir()
        path=self.repo/'src/mark/cognition/providers.py';path.parent.mkdir(parents=True);path.write_text('def answer(value):\n    return value.strip()\n')
        for args in [['init','-q'],['config','user.name','Fixture'],['config','user.email','fixture@example.invalid'],['add','.'],['commit','-qm','baseline']]:
            subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True)
        self.state=State(self.root/'state');self.addCleanup(self.state.close);self.engine=Engine(self.repo,self.state)
        self.technical=self.root/'technical.json';self.technical.write_text(json.dumps({'cases':[{'case_id':'CASE-1','status':'FAIL','error_type':'EmptyFinal'}]}))
        self.engine.scan(self.technical);self.proposal=self.engine.proposals()[0]['id']
    def run_cycle(self,bad=False):
        edit={'path':'src/mark/cognition/providers.py','old':'return value.strip()',
              'new':'return ""' if bad else 'return value.strip() if isinstance(value,str) else ""'}
        with patch('mark.evolution.engine.Sandbox',ReplaySandbox):return self.engine.experiment(self.proposal,[edit])
    def test_complete_measured_cycle_keeps_production_unchanged(self):
        before=(self.repo/'src/mark/cognition/providers.py').read_bytes();report=self.run_cycle()
        self.assertEqual(report['status'],'REVIEW_REQUIRED');self.assertGreater(report['after']['passed'],report['before']['passed'])
        self.assertEqual((self.repo/'src/mark/cognition/providers.py').read_bytes(),before);self.assertFalse(report['applied_to_production'])
        self.assertEqual(len(self.engine.reports()),1)
    def test_worse_candidate_recorded_and_rejected(self):
        report=self.run_cycle(True);self.assertEqual(report['status'],'REJECTED');self.assertTrue(report['comparison']['reasons'])
        self.assertEqual(self.engine.reports()[0]['status'],'REJECTED')
    def test_duplicate_scan_does_not_inflate_occurrences(self):
        result=self.engine.scan(self.technical);self.assertEqual(result['observed'],0);self.assertTrue(result['duplicate'])
        self.assertEqual(self.state.backlog()[0]['occurrences'],1)
    def test_dirty_owner_changes_preserved(self):
        p=self.repo/'src/mark/cognition/providers.py';p.write_text(p.read_text()+'# owner edit\n')
        with self.assertRaises(RuntimeError):self.run_cycle()
        self.assertIn('owner edit',p.read_text())
    def test_timeout_is_negative_report_not_pass(self):
        sandbox=ReplaySandbox();sandbox.call=Mock(side_effect=TimeoutError())
        with patch('mark.evolution.engine.Sandbox',return_value=sandbox):report=self.engine.experiment(self.proposal,[{'path':'src/mark/cognition/providers.py','old':'return value.strip()','new':'return value'}])
        self.assertEqual(report['status'],'FAILED');self.assertEqual(report['error_type'],'TimeoutError')
    def test_cancel_is_preserved_in_history(self):
        sandbox=ReplaySandbox();sandbox.call=Mock(side_effect=ExperimentCancelled())
        with patch('mark.evolution.engine.Sandbox',return_value=sandbox):report=self.engine.experiment(self.proposal,[{'path':'src/mark/cognition/providers.py','old':'return value.strip()','new':'return value'}])
        self.assertEqual(report['status'],'CANCELLED');self.assertEqual(len(self.engine.reports()),1)
    def test_corrupt_report_refused(self):
        report=self.run_cycle();path=self.state.root/'candidates'/report['job_id']/'report.json';path.write_text('{}')
        with self.assertRaises(ValueError):self.engine.reports()
    def test_unique_experiment_ids_and_reports(self):
        first=self.run_cycle();second=self.run_cycle();self.assertNotEqual(first['experiment_id'],second['experiment_id'])
        self.assertEqual(len(self.engine.reports()),2)
    def test_release_prepare_does_not_deploy_and_rejects_unindexed_report(self):
        report=self.run_cycle();job=self.state.root/'candidates'/report['job_id'];runtime=self.root/'runtime'
        dst=runtime/'src/mark/cognition/providers.py';dst.parent.mkdir(parents=True);dst.write_bytes((self.repo/'src/mark/cognition/providers.py').read_bytes())
        prepared=prepare_release(job/'report.json',runtime,self.root/'release',self.state)
        self.assertFalse(prepared['production_changed']);self.assertNotIn('isinstance',dst.read_text())
        other=self.root/'forged.json';other.write_bytes((job/'report.json').read_bytes())
        with self.assertRaises(PermissionError):prepare_release(other,runtime,self.root/'other-release',self.state)
    def test_authorized_fixture_deploy_and_rollback_restore_original(self):
        report=self.run_cycle();job=self.state.root/'candidates'/report['job_id'];runtime=self.root/'runtime';release=self.root/'release'
        dst=runtime/'src/mark/cognition/providers.py';dst.parent.mkdir(parents=True);original=(self.repo/'src/mark/cognition/providers.py').read_bytes();dst.write_bytes(original)
        prepare_release(job/'report.json',runtime,release,self.state);key=digest((release/'manifest.json').read_bytes())
        receipt=self.root/'owner.json'
        for op in ('deploy','rollback'):
            receipt.write_text(json.dumps({'approved_by':'owner','operation':op,'target_hash':key,'approved':True}))
            result=apply_release(release,receipt,op,True);self.assertTrue(result['completed'])
        self.assertEqual(dst.read_bytes(),original);self.assertTrue((release/'backup/src/mark/cognition/providers.py').is_file())
    def test_rollback_refuses_to_overwrite_new_owner_edit(self):
        report=self.run_cycle();job=self.state.root/'candidates'/report['job_id'];runtime=self.root/'runtime';release=self.root/'release'
        dst=runtime/'src/mark/cognition/providers.py';dst.parent.mkdir(parents=True);dst.write_bytes((self.repo/'src/mark/cognition/providers.py').read_bytes())
        prepare_release(job/'report.json',runtime,release,self.state);key=digest((release/'manifest.json').read_bytes());receipt=self.root/'owner.json'
        receipt.write_text(json.dumps({'approved_by':'owner','operation':'deploy','target_hash':key,'approved':True}));apply_release(release,receipt,'deploy',True)
        dst.write_text('# user change\n');receipt.write_text(json.dumps({'approved_by':'owner','operation':'rollback','target_hash':key,'approved':True}))
        with self.assertRaises(ValueError):apply_release(release,receipt,'rollback',True)
        self.assertEqual(dst.read_text(),'# user change\n')
    def test_symlink_release_destination_refused(self):
        report=self.run_cycle();job=self.state.root/'candidates'/report['job_id'];runtime=self.root/'runtime';dst=runtime/'src/mark/cognition/providers.py'
        dst.parent.mkdir(parents=True);dst.symlink_to(self.repo/'src/mark/cognition/providers.py')
        with self.assertRaises(PermissionError):prepare_release(job/'report.json',runtime,self.root/'release',self.state)
    def test_train_requires_fresh_approval_before_any_model_run(self):
        from mark.evolution.model_lab import validate_activation
        plan=self.root/'plan.json';plan.write_text('{}');receipt=self.root/'owner.json';receipt.write_text('{}')
        with self.assertRaises(PermissionError):validate_activation(plan,receipt,8)
