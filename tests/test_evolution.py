
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from mark.evolution.policy import Policy,candidate_path
from mark.evolution.state import State,exclusive
from mark.evolution.signals import extract_signals,propose
from mark.evolution.skills import SkillStore,skills_context
from mark.evolution.budget import preflight,tree_bytes
from mark.evolution.code_worker import prepare_edits,tree_digest,source_snapshot,clean_revision
from mark.evolution.evaluator import compare
from mark.evolution.scheduler import cycle,render
from mark.evolution.sandbox import Sandbox,SandboxUnavailable
from mark.evolution.promotion import approval,prepare_release,apply_release,digest
from mark.evolution.engine import immutable_json


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
    def state(self):
        state=State(self.root/'state');self.addCleanup(state.close);return state
    def signal(self):
        return extract_signals({'cases':[{'case_id':'MB-1','status':'PASS','model_metrics':{'attempts':2}}]},'a'*40)[0]
    def skill(self):
        return {'name':'Recuperação de importação','tags':['importação','falha','transação'],
                'procedure':'Consultar estado antes de repetir uma transação com resultado incerto.',
                'source':{'kind':'AUTHORED_LAB','reference':'fixture-v1','license':'USER_OWNED'},
                'evidence':['verified controlled replay'],'confidence':.9,'ttl_days':30,'consent':True}
    def report(self,passed):
        return {'passed':sum(passed),'total':len(passed),'rubric':'fixed','security_gate':True,
                'source_unchanged':True,'authorization_denied':True,'median_wall_seconds':1.,
                'cases':[{'case_id':str(i),'repetition':0,'critical':i==0,'status':'PASS' if x else 'FAIL'} for i,x in enumerate(passed)]}
    def source(self):
        src=self.root/'baseline';candidate=self.root/'candidate'
        for root in (src,candidate):
            p=root/'src/mark/cognition/providers.py';p.parent.mkdir(parents=True)
            p.write_text('def answer(value):\n    return value.strip()\n')
        return src,candidate

    def test_default_periodic_and_model_experiments_disabled(self):
        p=Policy();self.assertFalse(p.schedule_enabled);self.assertFalse(p.allow_model_experiments)
    def test_risk_three_and_unknown_actions_never_authorized(self):
        for action in ('shell','change_policy','force_push','disable_security','firmware','read_credentials','anything'):
            with self.subTest(action=action),self.assertRaises(PermissionError):Policy().authorize(action,True)
    def test_admin_action_requires_specific_owner_authorization(self):
        for action in ('training','deploy','rollback','publish_pr','install_timer'):
            with self.assertRaises(PermissionError):Policy().authorize(action)
    def test_policy_rejects_nan_bool_and_unbounded_budgets(self):
        for kw in ({'max_experiments':0},{'timeout_seconds':9999},{'vram_gib':float('nan')},{'memory_mib':True}):
            with self.assertRaises(ValueError):Policy(**kw)
    def test_source_and_evaluator_paths_protected(self):
        for name in ('../x','/etc/passwd','tests/test_core.py','benchmarks/markbench-v0.1.json','src/mark/evolution/policy.py','src/mark/device_auth.py'):
            with self.assertRaises(PermissionError):candidate_path(name)
    def test_duplicate_observations_are_counted_not_new_rows(self):
        s=self.state();s.observe(self.signal());s.observe(self.signal());self.assertEqual(len(s.backlog()),1);self.assertEqual(s.backlog()[0]['occurrences'],2)
    def test_same_model_failure_aggregates_across_benchmark_cases(self):
        rows=extract_signals({'cases':[{'case_id':'MB-1','status':'PASS','model_metrics':{'attempts':2}},
                                      {'case_id':'MB-2','status':'PASS','model_metrics':{'attempts':2}}]},'a'*40)
        self.assertEqual(rows[0]['id'],rows[1]['id'])
    def test_observer_ignores_private_text_and_injected_operations(self):
        data={'cases':[{'case_id':'MB-1','status':'FAIL','error_type':'execute shell','prompt':'PRIVATE MESSAGE','token':'not-for-store'}]}
        result=json.dumps(extract_signals(data,'abc'))
        self.assertNotIn('PRIVATE',result);self.assertNotIn('shell',result);self.assertNotIn('not-for-store',result)
    def test_pause_cancel_does_not_resume_learning(self):
        s=self.state();s.control('pause');s.control('cancel');self.assertTrue(s.get('paused'))
        with self.assertRaises(PermissionError):s.active()
        s.control('resume');s.active()
    def test_disabled_blocks_cycle(self):
        s=self.state();s.control('disable');engine=Mock();engine.state=s
        self.assertEqual(cycle(engine)['status'],'PAUSED');engine.scan.assert_not_called()
    def test_disabled_timer_does_not_experiment(self):
        engine=Mock();engine.state=self.state();self.assertEqual(cycle(engine)['status'],'DISABLED_OPT_IN_REQUIRED');engine.scan.assert_not_called()
    def test_explicit_schedule_has_backoff_and_no_code_execution(self):
        s=self.state();s.policy=replace(s.policy,schedule_enabled=True);engine=Mock();engine.state=s;engine.proposals.return_value=[];engine.scan.return_value={}
        self.assertEqual(cycle(engine)['experiments_executed'],0);self.assertEqual(cycle(engine)['status'],'BACKOFF')
    def test_schedule_render_does_not_install_or_overwrite(self):
        r=render(self.root,self.root/'state',self.root/'units');self.assertFalse(r['installed']);self.assertFalse(r['enabled'])
        self.assertIn('Restart=no',(self.root/'units/mark-evolution.service').read_text())
        self.assertIn('IPAddressDeny=any',(self.root/'units/mark-evolution.service').read_text())
        self.assertIn('OnBootSec=5min',(self.root/'units/mark-evolution.timer').read_text())
        with self.assertRaises(FileExistsError):render(self.root,self.root/'state',self.root/'units')
    def test_report_audit_chain_is_immutable(self):
        s=self.state();s.event('test',{'a':1})
        with self.assertRaises(sqlite3.IntegrityError):s.db.execute('DELETE FROM audit')
        s.db.rollback();s.verify_audit()
    def test_audit_corruption_is_detected(self):
        s=self.state();s.db.execute('DROP TRIGGER audit_no_update');s.db.execute("UPDATE audit SET body='{}'");s.db.commit()
        with self.assertRaises(ValueError):s.verify_audit()
    def test_corrupt_database_is_not_reset(self):
        root=self.root/'bad';root.mkdir();p=root/'engine.sqlite3';p.write_bytes(b'invalid database');p.chmod(0o600)
        with self.assertRaises(sqlite3.DatabaseError):State(root)
        self.assertEqual(p.read_bytes(),b'invalid database')
    def test_symlink_state_rejected(self):
        target=self.root/'elsewhere';target.mkdir();link=self.root/'link';link.symlink_to(target,target_is_directory=True)
        with self.assertRaises(PermissionError):State(link)
    def test_concurrent_experiments_refused(self):
        s=self.state();other=State(s.root);self.addCleanup(other.close)
        with exclusive(s):
            with self.assertRaises(RuntimeError):
                with exclusive(other):pass
    def test_disk_and_temporary_budget_enforced(self):
        with self.assertRaises(RuntimeError):preflight(self.root,Policy(),free_bytes=1)
        with self.assertRaises(RuntimeError):preflight(self.root,Policy(),estimated_temporary=4*1024**3)
    def test_gpu_absent_is_blocked(self):
        with self.assertRaises(RuntimeError):preflight(self.root,Policy(),gpu_required=True,gpu_free_gib=None)
    def test_storage_counts_bytes_and_rejects_symlink(self):
        path=self.root/'files';path.mkdir();(path/'a').write_bytes(b'1234');self.assertEqual(tree_bytes(path),4)
        (path/'b').symlink_to(path/'a')
        with self.assertRaises(PermissionError):tree_bytes(path)
    def test_skill_requires_consent_and_evidence(self):
        with SkillStore(self.root/'skills.db') as s:
            data=self.skill();data['consent']=False
            with self.assertRaises(PermissionError):s.add(**data)
            data['consent']=True;data['evidence']=[]
            with self.assertRaises(ValueError):s.add(**data)
    def test_skill_deduplication_and_versions(self):
        with SkillStore(self.root/'skills.db') as s:
            data=self.skill();first=s.add(**data);self.assertEqual(s.add(**data)['status'],'DUPLICATE')
            data['procedure']+=' Compare o resultado esperado.';self.assertEqual(s.add(**data)['version'],2)
            self.assertEqual(s.stats()['versions'],2)
    def test_skill_selective_expiring_retrieval(self):
        with SkillStore(self.root/'skills.db') as s:
            s.add(**self.skill());self.assertEqual(len(s.retrieve('falha em importação transação')),1)
            self.assertEqual(s.retrieve('qual clima em Curitiba'),[])
            self.assertEqual(s.retrieve('falha importação',now=10**15),[])
    def test_skill_possible_secret_is_rejected(self):
        with SkillStore(self.root/'skills.db') as s:
            data=self.skill();data['procedure']='password=some-private-string'
            with self.assertRaises(ValueError):s.add(**data)
    def test_learning_disabled_and_paused_does_not_retrieve(self):
        s=self.state();path=s.root/'skills.sqlite3'
        with SkillStore(path) as store:store.add(**self.skill())
        cfg={'cognition':{'learned_skills':True,'skills_db':str(path)}}
        self.assertIn('Consultar',skills_context('falha importação',cfg))
        s.control('pause');self.assertEqual(skills_context('falha importação',cfg),'')
        self.assertEqual(skills_context('falha importação',{}),'')
    def test_patch_does_not_modify_baseline(self):
        src,candidate=self.source();before=tree_digest(src)
        prepare_edits(src,candidate,[{'path':'src/mark/cognition/providers.py','old':'return value.strip()','new':'return value.strip() if isinstance(value,str) else ""'}])
        self.assertEqual(tree_digest(src),before);self.assertNotEqual(tree_digest(candidate),before)
    def test_patch_public_contract_and_arbitrary_command_fields_rejected(self):
        src,candidate=self.source()
        with self.assertRaises(ValueError):prepare_edits(src,candidate,[{'path':'src/mark/cognition/providers.py','old':'answer(value)','new':'answer(other)'}])
        with self.assertRaises(ValueError):prepare_edits(src,candidate,[{'shell':'rm -rf /'}])
    def test_forged_metric_and_holdout_changes_rejected(self):
        before=self.report([1,0]);after=self.report([1,1]);after['passed']=500
        with self.assertRaises(ValueError):compare(before,after)
        after=self.report([1,1]);after['rubric']='easier'
        with self.assertRaises(ValueError):compare(before,after)
    def test_security_regression_rejected_even_with_score_gain(self):
        before=self.report([1,0,0]);after=self.report([0,1,1]);after['security_gate']=False
        self.assertEqual(compare(before,after)['decision'],'REJECTED')
    def test_no_gain_and_latency_regression_rejected(self):
        before=self.report([1,0]);self.assertEqual(compare(before,before)['decision'],'REJECTED')
        after=self.report([1,1]);after['median_wall_seconds']=9
        self.assertEqual(compare(before,after)['decision'],'REJECTED')
    def test_good_candidate_is_review_not_auto_deploy(self):
        result=compare(self.report([1,0]),self.report([1,1]));self.assertEqual(result['decision'],'REVIEW_REQUIRED');self.assertFalse(result['promotion_authorized'])
    def test_immutable_report_cannot_be_overwritten(self):
        path=self.root/'report.json';immutable_json(path,{'a':1})
        with self.assertRaises(FileExistsError):immutable_json(path,{'a':2})
    def test_approval_hash_bound_to_operation(self):
        receipt=self.root/'approval.json';receipt.write_text(json.dumps({'approved_by':'owner','operation':'training','target_hash':'a'*64,'approved':True}))
        approval(receipt,'training','a'*64)
        with self.assertRaises(PermissionError):approval(receipt,'deploy','a'*64)
    def test_github_duplicate_does_not_create_again(self):
        from mark.evolution.github_broker import existing_pr
        with patch('mark.evolution.github_broker._gh',return_value='[{"url":"https://github.com/thu12dta3/MARK/pull/999"}]') as gh:
            self.assertIn('/999',existing_pr('evolution/EXP-0001-empty-reply'));self.assertEqual(gh.call_args.args[0][1],'list')
    def test_github_failure_does_not_change_local_history(self):
        from mark.evolution.github_broker import existing_pr
        with patch('mark.evolution.github_broker._gh',side_effect=RuntimeError('network down')):
            with self.assertRaises(RuntimeError):existing_pr('evolution/EXP-0001-empty-reply')
    def test_out_of_scope_github_branch_denied(self):
        from mark.evolution.github_broker import existing_pr
        with self.assertRaises(ValueError):existing_pr('main')
    def test_unknown_source_version_is_not_migrated(self):
        s=self.state();s.db.execute('PRAGMA user_version=9');s.db.commit();s.close();self._cleanups.pop()
        with self.assertRaises(ValueError):State(s.root)

    def test_holdout_hash_cannot_change_after_baseline(self):
        from mark.evolution.benchmark_v02 import run
        p=self.root/'cases.json';p.write_text('{}')
        with self.assertRaises(PermissionError):run(p,expected_sha256='0'*64)

    def test_model_patch_generation_is_structured_and_not_a_shell(self):
        from mark.evolution.code_worker import generate_edits
        repo=self.root/'fake';p=repo/'src/mark/cognition/providers.py';p.parent.mkdir(parents=True);p.write_text('fixture')
        provider=Mock();provider.generate.return_value.text='{"shell":"delete files"}'
        with self.assertRaises(ValueError):generate_edits(repo,{'possible_files':['src/mark/cognition/providers.py']},provider)

    def test_cli_pause_resume_policy_are_real_commands(self):
        from mark.evolution.cli import main
        from contextlib import redirect_stdout
        from io import StringIO
        with redirect_stdout(StringIO()):
            self.assertEqual(main(['--state',str(self.root/'cli'),'pause']),0)
            self.assertEqual(main(['--state',str(self.root/'cli'),'resume']),0)
            self.assertEqual(main(['--state',str(self.root/'cli'),'policy']),0)

    def test_quality_mismatch_does_not_automatically_patch_provider(self):
        report={'cases':[{'case_id':'M2-1','status':'FAIL','model_metrics':{'success':True,'attempts':1}}]}
        signal=extract_signals(report,'abc')[0];self.assertEqual(signal['component'],'evaluation')
        self.assertEqual(propose(signal)['possible_files'],[])
