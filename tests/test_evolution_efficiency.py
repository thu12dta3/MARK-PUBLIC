import unittest,math,tempfile,json
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from mark.evolution.efficiency import EnergyMeter,compare_efficiency
from mark.evolution.automatic_training import run_round,validate_contract
from mark.evolution.state import State
from mark.evolution.policy import Policy

class EfficiencyTests(unittest.TestCase):
    def metrics(self,**updates):return dict(passed=3,total=4,energy_joules=100,vram_peak_gib=4,latency_seconds=10)|updates
    def test_quality_gain_with_resources_preserved(self):
        r=compare_efficiency(self.metrics(),self.metrics(passed=4));self.assertEqual(r['decision'],'REVIEW_REQUIRED');self.assertFalse(r['promotion_authorized'])
    def test_efficiency_gain_preserves_quality(self):
        self.assertEqual(compare_efficiency(self.metrics(),self.metrics(energy_joules=80))['decision'],'REVIEW_REQUIRED')
    def test_smaller_cannot_hide_quality_regression(self):
        self.assertEqual(compare_efficiency(self.metrics(),self.metrics(passed=2,energy_joules=40))['decision'],'REJECTED')
    def test_individual_regression_cannot_hide_in_total_gain(self):
        self.assertEqual(compare_efficiency(self.metrics(),self.metrics(passed=4),True)['decision'],'REJECTED')
    def test_missing_energy_cannot_claim_gain(self):
        self.assertEqual(compare_efficiency(self.metrics(),self.metrics(energy_joules=None))['reason'],'UNMEASURED')
    def test_nonfinite_cannot_pass(self):
        self.assertEqual(compare_efficiency(self.metrics(),self.metrics(energy_joules=math.nan))['decision'],'REJECTED')
    def test_power_tradeoff_rejected(self):
        self.assertEqual(compare_efficiency(self.metrics(),self.metrics(passed=4,energy_joules=120))['decision'],'REJECTED')
    def test_noise_is_not_optimization(self):
        self.assertEqual(compare_efficiency(self.metrics(),self.metrics(energy_joules=99))['reason'],'NO_MEASURABLE_GAIN')
    def test_meter_sensor_failure_is_unknown(self):
        def fail():raise RuntimeError()
        with EnergyMeter(fail) as meter:pass
        self.assertIsNone(meter.result()['energy_joules'])
    def test_meter_integrates_watts_over_time(self):
        values=iter([{'time':0.,'watts':10.,'vram_gib':1.},{'time':2.,'watts':20.,'vram_gib':2.}])
        with EnergyMeter(lambda:next(values),interval=30) as meter:pass
        self.assertEqual(meter.result()['energy_joules'],30.)
    def test_training_is_opt_in(self):
        with tempfile.TemporaryDirectory() as root,State(root) as state:
            self.assertIsNone(run_round(state,Path(root),lambda:False))
    def test_unreviewed_scope_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'scope.json';path.write_text('{}')
            with self.assertRaises(PermissionError):validate_contract(path)
    def test_finite_search_stops_before_dispatch(self):
        with tempfile.TemporaryDirectory() as root,State(root) as state:
            state.set('automatic_training_contract','unused');state.set('automatic_training_rounds',[{'time':0,'round':i,'status':'TRAINED_CANDIDATE'} for i in range(1,4)])
            with patch('mark.evolution.automatic_training.validate_contract',return_value={'max_rounds':3}):
                self.assertEqual(run_round(state,Path(root),lambda:False)['status'],'TRAINING_SEARCH_COMPLETE')
    def test_interactive_sessions_defer_without_training(self):
        with tempfile.TemporaryDirectory() as root,State(root) as state:
            state.set('automatic_training_contract','unused')
            with patch('mark.evolution.automatic_training.validate_contract',return_value={'max_rounds':3}),patch('mark.evolution.automatic_training.model_lab.prepare') as prepare:
                self.assertEqual(run_round(state,Path(root),lambda:True)['reason'],'interactive');prepare.assert_not_called()
    def test_probe_zero_exit_does_not_imply_cuda_ready(self):
        from mark.evolution.model_lab import run_isolated
        import hashlib
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);repo=root/'repo';trainer=repo/'src/mark/cognition/training.py';trainer.parent.mkdir(parents=True);trainer.write_text('fixture')
            import mark.evolution.model_lab as lab
            plan={'harness_hash':hashlib.sha256(Path(lab.__file__).with_name('training_harness.py').read_bytes()).hexdigest(),'trainer_hash':hashlib.sha256(trainer.read_bytes()).hexdigest(),'budget':{'temporary_estimated_bytes':1},'max_seconds':60}
            path=root/'plan.json';path.write_text(json.dumps(plan))
            process=Mock(returncode=0);process.poll.return_value=0
            def popen(*args,**kwargs):
                kwargs['stdout'].write(b'{"dependencies":{"torch":true},"cuda_available":false}\n');kwargs['stdout'].flush();return process
            with patch('mark.evolution.model_lab.sandbox_command',return_value=['fixture']),patch('subprocess.Popen',side_effect=popen):
                result=run_isolated(path,None,repo,root/'workspace',Policy(),probe=True)
            self.assertEqual(result['status'],'PROBE_FAILED')
    def test_infrastructure_failure_retries_same_recipe_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as root,State(root) as state:
            state.set('automatic_training_contract','fixture');history=[{'round':1,'time':0,'status':'LOADING','training_started':False}];state.set('automatic_training_rounds',history)
            contract={'max_rounds':3,'minimum_interval_seconds':86400,'vram_gib':6.,'configs':['one','two','three'],'dataset_approval':'approval','baseline':'baseline','base':'base','splits':'splits','holdout':'holdout'}
            with patch('mark.evolution.automatic_training.validate_contract',return_value=contract),patch('mark.evolution.automatic_training.model_lab.prepare',side_effect=RuntimeError('stop fixture')) as prepare:
                with self.assertRaises(RuntimeError):run_round(state,Path(root),lambda:False)
            self.assertEqual(prepare.call_args.args[0],'one');self.assertEqual(state.get('automatic_training_rounds'),history)
    def test_two_infrastructure_attempts_exhaust_recipe(self):
        with tempfile.TemporaryDirectory() as root,State(root) as state:
            state.set('automatic_training_contract','fixture');state.set('automatic_training_rounds',[{'round':1,'time':0,'status':'LOADING','training_started':False}]*2)
            contract={'max_rounds':3,'minimum_interval_seconds':86400,'vram_gib':6.,'configs':['one','two','three'],'dataset_approval':'approval','baseline':'baseline','base':'base','splits':'splits','holdout':'holdout'}
            with patch('mark.evolution.automatic_training.validate_contract',return_value=contract),patch('mark.evolution.automatic_training.model_lab.prepare',side_effect=RuntimeError()) as prepare:
                with self.assertRaises(RuntimeError):run_round(state,Path(root),lambda:False)
            self.assertEqual(prepare.call_args.args[0],'two')
