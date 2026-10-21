import json
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from mark.evolution.state import State
from mark.evolution.policy import Policy
from mark.evolution.cli import control_run
from mark.evolution.activity import log_line,phase_report_text
from mark.evolution.scheduler import render


class ActivityTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_fast_phases_are_saved_without_ui_poll_and_exclude_private_text(self):
        with State(self.root/'state') as state:
            state.set_progress('BASELINE')
            state.finish_phase(summary={'passed':12,'total':16,'prompt':'private text','password':'private secret'})
            state.set_progress('CANDIDATE');state.finish_phase(summary={'passed':16,'total':16,'security_gate':True})
            state.set_progress('COMPLETE','DONE',summary={'gain':4,'decision':'REVIEW_REQUIRED'})
            reports=state.phase_reports();self.assertEqual([r['phase'] for r in reports],['COMPLETE','CANDIDATE','BASELINE'])
            for record in reports:
                path=state.root/'phase-reports'/record['report_file']
                self.assertTrue(path.is_file());self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
                self.assertNotIn('private',path.read_text());self.assertNotIn('private',json.dumps(record))
            self.assertIn('16/16',phase_report_text(reports[1]));self.assertIn('revisão',phase_report_text(reports[0]))
            state.verify_audit()
    def test_bounded_log_retains_phase_reports_and_audit(self):
        with State(self.root/'state') as state:
            for _ in range(30):state.set_progress('BASELINE');state.set_progress('COMPLETE','DONE')
            self.assertLessEqual(len(state.status()['activity']),24)
            self.assertEqual(len(list((state.root/'phase-reports').glob('*.txt'))),60)
            self.assertEqual(len(state.phase_reports(100)),60)
            state.verify_audit()
    def test_phase_report_budget_preserves_existing_reports(self):
        with State(self.root/'state',replace(Policy(),metadata_bytes=1024**2)) as state:
            state.set_progress('BASELINE')
            folder=state.root/'phase-reports';folder.mkdir()
            retained=folder/'retained.txt';retained.write_bytes(b'x'*(300*1024))
            with self.assertRaises(RuntimeError):state.finish_phase()
            self.assertEqual(retained.stat().st_size,300*1024)
    def test_start_and_stop_control_timer_service_and_cancel_flag(self):
        units=self.root/'units';units.mkdir()
        for name in ('mark-evolution.service','mark-evolution.timer'):(units/name).write_text('fixture')
        calls=[]
        with State(self.root/'state') as state:
            runner=lambda args,**kwargs:calls.append(args)
            control_run(state,'start',units,runner)
            self.assertTrue(state.policy.schedule_enabled);self.assertFalse(state.get('paused'));self.assertFalse(state.get('cancel'))
            control_run(state,'stop',units,runner)
            self.assertFalse(state.policy.schedule_enabled);self.assertTrue(state.get('paused'));self.assertTrue(state.get('cancel'))
            self.assertFalse(state.policy.allow_model_experiments)
            self.assertIn('start',calls[1]);self.assertIn('stop',calls[3])
            self.assertIn('Parada solicitada',log_line(state.status()['activity'][-1]))
    def test_failed_start_restores_previous_policy_and_settings(self):
        units=self.root/'units';units.mkdir()
        for name in ('mark-evolution.service','mark-evolution.timer'):(units/name).write_text('fixture')
        with State(self.root/'state') as state:
            state.control('pause')
            def runner(args,**kwargs):
                if 'start' in args:raise RuntimeError('private error detail')
            with self.assertRaises(RuntimeError):control_run(state,'start',units,runner)
            self.assertFalse(state.policy.schedule_enabled);self.assertTrue(state.get('paused'))
            self.assertNotIn('private error detail',str(state.status()['activity']))
            self.assertIn('Não foi possível',log_line(state.status()['activity'][-1]))
    def test_service_executes_reviewed_coordinator_separately_from_candidate_repo(self):
        repo=self.root/'candidate-repo';coordinator=self.root/'runtime'
        render(repo,self.root/'state',self.root/'units',coordinator=coordinator)
        unit=(self.root/'units/mark-evolution.service').read_text()
        self.assertIn(str(coordinator/'scripts/evolution_cycle.py'),unit)
        self.assertIn('--repo "'+str(repo)+'"',unit)
