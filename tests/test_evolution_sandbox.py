
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from mark.evolution.policy import Policy
from mark.evolution.sandbox import Sandbox,SandboxUnavailable,ExperimentCancelled
from mark.evolution.code_worker import source_snapshot,prepare_edits
from mark.evolution.evaluator import evaluate,reply_cases

ROOT=Path(__file__).resolve().parents[1]


class RealSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:Sandbox(Policy()).probe(ROOT)
        except (OSError,RuntimeError):raise unittest.SkipTest('Namespace/cgroup indisponível; execução não confiável deve permanecer bloqueada')
    def test_namespace_separates_home_credentials_network_and_gpu(self):
        with patch.dict(os.environ,{'GH_TOKEN':'FAKE-SECRET-FIXTURE'}):
            result=Sandbox(Policy()).probe(ROOT)
        self.assertFalse(result['private_home_visible']);self.assertFalse(result['credential_environment']);self.assertEqual(result['interfaces'],['lo'])
    def test_current_provider_reproduces_null_final_without_live_ollama(self):
        result=Sandbox(Policy()).call(ROOT,{'operation':'reply','replies':[{'message':{'content':None}},{'message':{'content':'final'}}]})['result']
        self.assertEqual(result['error'],'AttributeError');self.assertEqual(result['calls'],1)
    def test_real_cancel_stops_own_sandbox(self):
        with self.assertRaises(ExperimentCancelled):Sandbox(Policy()).call(ROOT,{'operation':'probe'},cancel=lambda:True)
    def test_missing_bwrap_does_not_fallback_to_host_execution(self):
        with patch('mark.evolution.sandbox.Path.is_file',return_value=False):
            with self.assertRaises(SandboxUnavailable):Sandbox(Policy())._command(ROOT)
    def test_real_timeout_on_untrusted_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'candidate';source_snapshot(ROOT,target,'HEAD')
            p=target/'src/mark/cognition/providers.py';text=p.read_text();text=text.replace('        started = time.perf_counter()','        while True: pass\n        started = time.perf_counter()',1);p.write_text(text)
            with self.assertRaises(TimeoutError):
                Sandbox(replace(Policy(),timeout_seconds=1,cpu_seconds=3)).call(target,{'operation':'reply','replies':[{'message':{'content':'hello'}}]})
