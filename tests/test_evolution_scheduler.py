import tempfile
import json
import unittest
from dataclasses import replace
from pathlib import Path
from mark.evolution.policy import Policy
from mark.evolution.scheduler import cycle,resolve_evolution_model
from mark.evolution.state import State
from mark.evolution.engine import Engine
from mark.evolution.cli import parser,manage_schedule
from unittest.mock import patch


class FakeEngine:
    def __init__(self,state):
        self.state=state;self.executions=[]
    def scan(self):return {'version':'fixture','sandbox':{'ok':True}}
    def proposals(self):
        return [{'id':'P-fixture','experiment_kind':'code','possible_files':['src/mark/brain.py']}]
    def experiment(self,proposal,edits):
        self.executions.append((proposal,edits))
        return {'job_id':'JOB-fixture','status':'REVIEW_REQUIRED','proposal':{'id':proposal}}


class ScheduledCycleTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_evolution_uses_configured_installed_engineer_after_model_swap(self):
        cfg={'ollama':{'url':'http://127.0.0.1:11434','model':'stale:old'},
             'models':{'chat':'new-heretic:latest','call':'new-heretic:latest','engineer':'coder-heretic:q4'}}
        selected,profile=resolve_evolution_model(cfg,{'coder-heretic:q4','new-heretic:latest'})
        self.assertEqual(profile,'engineer')
        self.assertEqual(selected['ollama']['model'],'coder-heretic:q4')
        self.assertFalse(selected['ollama']['think'])
        self.assertEqual(cfg['ollama']['model'],'stale:old')
    def test_evolution_never_downloads_missing_models(self):
        cfg={'ollama':{'url':'http://127.0.0.1:11434','model':'stale'},
             'models':{'chat':'chat-heretic','call':'call-heretic','engineer':'coder-heretic'}}
        with self.assertRaisesRegex(RuntimeError,'Nada foi baixado'):
            resolve_evolution_model(cfg,{'unrelated'})
    def test_opt_in_cycle_executes_at_most_one_isolated_candidate_once(self):
        policy=replace(Policy(),schedule_enabled=True,scheduled_experiments=True)
        with State(self.root/'state',policy) as state:
            engine=FakeEngine(state);calls=[]
            generate=lambda proposal:calls.append(proposal['id']) or [{'path':'src/mark/brain.py','old':'a','new':'b'}]
            first=cycle(engine,generate,lambda:False)
            self.assertEqual(first['experiments_executed'],1)
            self.assertEqual(state.status()['progress']['status'],'DONE')
            self.assertEqual(calls,['P-fixture']);self.assertEqual(len(engine.executions),1)
            state.set('last_cycle',0)
            second=cycle(engine,generate,lambda:False)
            self.assertEqual(second['experiments_executed'],0)
            self.assertEqual(calls,['P-fixture'])
            row=state.db.execute('SELECT status,job_id,attempt_count FROM scheduled_experiments WHERE proposal_id=?',('P-fixture',)).fetchone()
            self.assertEqual(tuple(row),('REVIEW_REQUIRED','JOB-fixture',1))
    def test_active_model_session_defers_without_consuming_proposal(self):
        policy=replace(Policy(),schedule_enabled=True,scheduled_experiments=True)
        with State(self.root/'state',policy) as state:
            engine=FakeEngine(state);called=[]
            result=cycle(engine,lambda proposal:called.append(1),lambda:True)
            self.assertEqual(result['experiments_executed'],0);self.assertEqual(called,[])
            self.assertFalse(state.db.execute('SELECT 1 FROM scheduled_experiments').fetchone())
            self.assertEqual(state.status()['progress']['status'],'DEFERRED')
    def test_failed_generation_is_recorded_once(self):
        policy=replace(Policy(),schedule_enabled=True,scheduled_experiments=True)
        with State(self.root/'state',policy) as state:
            engine=FakeEngine(state);calls=[]
            def fail(proposal):calls.append(1);raise TimeoutError('private detail must not be logged')
            result=cycle(engine,fail,lambda:False)
            self.assertEqual(result['experiments'][0]['status'],'GENERATION_FAILED')
            self.assertEqual(result['status'],'FAILED')
            self.assertEqual(state.status()['progress']['status'],'FAILED')
            self.assertNotIn('private detail',str(state.backlog()))
            state.set('last_cycle',0)
            with state.db:state.db.execute('UPDATE scheduled_experiments SET updated=0')
            cycle(engine,fail,lambda:False)
            self.assertEqual(calls,[1,1])
            state.set('last_cycle',0)
            with state.db:state.db.execute('UPDATE scheduled_experiments SET updated=0')
            cycle(engine,fail,lambda:False)
            self.assertEqual(calls,[1,1])
    def test_schedule_disabled_never_asks_model_to_generate(self):
        with State(self.root/'state') as state:
            engine=FakeEngine(state);called=[]
            result=cycle(engine,lambda proposal:called.append(1),lambda:False)
            self.assertEqual(result['status'],'DISABLED_OPT_IN_REQUIRED');self.assertEqual(called,[])
    def test_concurrent_cycle_cannot_generate_second_patch(self):
        policy=replace(Policy(),schedule_enabled=True,scheduled_experiments=True)
        with State(self.root/'state',policy) as state:
            engine=FakeEngine(state);nested=[]
            def generate(proposal):
                nested.append(cycle(engine,lambda _:self.fail('parallel generation'),lambda:False))
                return []
            cycle(engine,generate,lambda:False)
            self.assertEqual(nested,[{'status':'ALREADY_RUNNING'}])
    def test_dead_coordinator_is_shown_as_interrupted(self):
        with State(self.root/'state') as state:
            state.set_progress('GENERATING')
            with patch('mark.model_state._pid_alive',return_value=False):
                self.assertEqual(state.status()['progress']['status'],'INTERRUPTED')
            self.assertEqual(state.get('progress')['status'],'RUNNING')
    def test_cli_exposes_distinct_periodic_enable_and_disable_controls(self):
        self.assertTrue(parser().parse_args(['schedule','--enable']).enable)
        self.assertTrue(parser().parse_args(['schedule','--disable']).disable)
    def test_admin_timer_control_rolls_back_policy_if_systemd_fails(self):
        with State(self.root/'state') as state:
            units=self.root/'units';units.mkdir()
            (units/'mark-evolution.service').write_text('service')
            (units/'mark-evolution.timer').write_text('timer')
            def success(*args,**kwargs):
                self.assertEqual(state.policy.schedule_enabled,'enable' in args[0])
            self.assertTrue(manage_schedule(state,True,units,success)['scheduled_experiments'])
            def failure(*args,**kwargs):raise RuntimeError('systemd failed')
            with self.assertRaises(RuntimeError):manage_schedule(state,False,units,failure)
            self.assertTrue(state.policy.schedule_enabled)
            self.assertFalse(manage_schedule(state,False,units,success)['schedule_enabled'])
    def test_rejection_allows_only_one_bounded_feedback_retry(self):
        policy=replace(Policy(),schedule_enabled=True,scheduled_experiments=True)
        with State(self.root/'state',policy) as state:
            engine=FakeEngine(state);calls=[]
            engine.experiment=lambda *args:{'job_id':'JOB-fixture','status':'REJECTED'}
            engine.reports=lambda:[{'job_id':'JOB-fixture','before':{'passed':12,'total':16},'after':{'passed':12,'total':16},'cases':'private test cases'}]
            generate=lambda p:calls.append(p) or []
            cycle(engine,generate,lambda:False)
            state.set('last_cycle',0)
            with state.db:state.db.execute('UPDATE scheduled_experiments SET updated=0')
            cycle(engine,generate,lambda:False)
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[1]['previous_attempt']['after_passed'],12)
            self.assertNotIn('private test cases',str(calls[1]))
            state.set('last_cycle',0)
            with state.db:state.db.execute('UPDATE scheduled_experiments SET updated=0')
            cycle(engine,generate,lambda:False)
            self.assertEqual(len(calls),2)
    def test_jsonl_source_imports_only_new_private_free_metric_lines(self):
        with State(self.root/'state') as state:
            engine=Engine(self.root,state);source=self.root/'metrics.jsonl'
            source.write_text('{"event_id":"one","success":false,"attempts":1,"prompt":"private text"}\n')
            with patch('mark.evolution.engine.clean_revision',return_value='a'*40):
                first=engine.scan(source);again=engine.scan(source)
                with source.open('a') as output:output.write('{"event_id":"two","success":false,"attempts":1}\n')
                second=engine.scan(source)
            self.assertEqual(first['observed'],1);self.assertEqual(again['observed'],0)
            self.assertEqual(second['observed'],1)
            self.assertNotIn('private text',str(state.backlog()))
            self.assertEqual(state.db.execute('SELECT count(*) FROM imports').fetchone()[0],2)
            self.assertEqual(state.backlog()[0]['occurrences'],2)
    def test_metrics_batch_capacity_matches_telemetry_byte_cap(self):
        with State(self.root/'state') as state:
            engine=Engine(self.root,state);source=self.root/'metrics.jsonl'
            source.write_text(''.join(json.dumps({'event_id':str(i),'success':True,'attempts':1})+'\n' for i in range(1001)))
            with patch('mark.evolution.engine.clean_revision',return_value='a'*40):result=engine.scan(source)
            self.assertEqual(result['duplicates'],0)
            self.assertEqual(state.db.execute('SELECT count(*) FROM imports').fetchone()[0],1001)


if __name__=='__main__':unittest.main()

class RecoveryTests(unittest.TestCase):
    def test_transient_observation_retries_and_continues(self):
        with tempfile.TemporaryDirectory() as root,State(root,replace(Policy(),schedule_enabled=True)) as state:
            engine=FakeEngine(state);attempts=[]
            def scan():
                attempts.append(1)
                if len(attempts)<3:raise TimeoutError('private detail')
                return {'imported':[]}
            engine.scan=scan
            result=cycle(engine)
            self.assertEqual(result['status'],'PROPOSED')
            self.assertEqual(len(attempts),3)
            activity=state.get('activity');self.assertEqual(sum(r['kind']=='recovery' for r in activity),2)
            self.assertNotIn('private detail',str(activity))
    def test_persistent_transient_failure_is_bounded(self):
        with tempfile.TemporaryDirectory() as root,State(root,replace(Policy(),schedule_enabled=True)) as state:
            engine=FakeEngine(state);engine.scan=lambda:(_ for _ in ()).throw(TimeoutError())
            result=cycle(engine);self.assertEqual(result['status'],'FAILED')
            self.assertEqual(sum(r['kind']=='recovery' for r in state.get('activity')),2)
    def test_dirty_repository_defers_without_modifying_owner_files(self):
        from mark.evolution.code_worker import RepositoryBusy
        with tempfile.TemporaryDirectory() as root,State(root,replace(Policy(),schedule_enabled=True)) as state:
            engine=FakeEngine(state);engine.scan=lambda:(_ for _ in ()).throw(RepositoryBusy())
            result=cycle(engine);self.assertEqual(result,{'status':'DEFERRED','reason':'repository'})
            self.assertFalse(state.get('cycle_failed'))
            self.assertEqual(state.progress()['wait_reason'],'repository')
            state.set('last_cycle',0);engine.scan=lambda:{'imported':[]}
            self.assertEqual(cycle(engine)['status'],'PROPOSED')
    def test_pause_during_recovery_prevents_next_attempt(self):
        with tempfile.TemporaryDirectory() as root,State(root,replace(Policy(),schedule_enabled=True)) as state:
            engine=FakeEngine(state);attempts=[]
            def scan():
                attempts.append(1);state.control('pause');raise TimeoutError()
            engine.scan=scan
            self.assertEqual(cycle(engine)['status'],'CANCELLED');self.assertEqual(len(attempts),1)
    def test_non_transient_errors_are_not_retried(self):
        with tempfile.TemporaryDirectory() as root,State(root,replace(Policy(),schedule_enabled=True)) as state:
            engine=FakeEngine(state);attempts=[]
            def scan():attempts.append(1);raise PermissionError('private detail')
            engine.scan=scan
            self.assertEqual(cycle(engine)['status'],'FAILED');self.assertEqual(len(attempts),1)
            self.assertFalse(any(r['kind']=='recovery' for r in state.get('activity')))
    def test_recovery_log_explains_retry_without_private_exception(self):
        from mark.evolution.activity import log_line,phase_report_text
        with tempfile.TemporaryDirectory() as root,State(root) as state:
            row=state.record_activity('recovery',phase='OBSERVING',status='RUNNING',summary={'attempt':1,'error_type':'TimeoutError'})
            self.assertIn('nova tentativa 1 de 2',log_line(row))
            waiting=state.record_activity('waiting',reason='repository')
            self.assertIn('repositório em edição',log_line(waiting))
            self.assertIn('versão estável',phase_report_text({'phase':'OBSERVING','status':'DEFERRED','summary':{'error_type':'RepositoryBusy'}}))
    def test_daily_training_cooldown_does_not_block_code_cycle(self):
        with tempfile.TemporaryDirectory() as root,State(root,replace(Policy(),schedule_enabled=True)) as state:
            state.set('automatic_training_contract','authorized-fixture');engine=FakeEngine(state);engine.repo=Path(root)
            with patch('mark.evolution.automatic_training.run_round',return_value={'status':'DEFERRED','reason':'training_interval'}):
                self.assertEqual(cycle(engine)['status'],'PROPOSED')
