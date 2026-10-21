from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark import sentinel
from mark.brain import answer
from mark.memory import ShortMemory


class SentinelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_uses_argument_vector_and_preserves_spaces(self):
        target = self.root / "nome com espaço; $(nao-executar).txt"
        target.write_text("arquivo inofensivo", encoding="utf-8")
        proc = self._fake_proc(0, "")
        with patch.object(sentinel.shutil, "which", return_value="/usr/bin/clamscan"), \
             patch.object(sentinel.subprocess, "Popen", side_effect=proc) as popen, \
             patch.object(sentinel, "_save_report"):
            answer = sentinel.scan_path([str(target)])
        argv = popen.call_args.args[0]
        self.assertEqual(argv[-1], str(target))
        self.assertIn("sem detecções", answer)
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_scan_interprets_clamav_detection_code(self):
        target = self.root / "sample"
        target.write_text("fixture")
        proc = self._fake_proc(1, f"{target}: Test.Signature FOUND\n")
        with patch.object(sentinel.shutil, "which", return_value="clamscan"), \
             patch.object(sentinel.subprocess, "Popen", side_effect=proc), \
             patch.object(sentinel, "_save_report"):
            answer = sentinel.scan_path([str(target)])
        self.assertIn("detecções", answer)
        self.assertIn("Test.Signature FOUND", answer)
        self.assertIn("Nenhum arquivo foi alterado", answer)

    def test_scan_does_not_call_exit_two_clean(self):
        target = self.root / "sample"
        target.write_text("fixture")
        proc = self._fake_proc(2, "ERROR\n")
        with patch.object(sentinel.shutil, "which", return_value="clamscan"), \
             patch.object(sentinel.subprocess, "Popen", side_effect=proc), \
             patch.object(sentinel, "_save_report"):
            answer = sentinel.scan_path([str(target)])
        self.assertIn("terminou com erro", answer)
        self.assertNotIn("sem detecções", answer)

    def test_scan_rejects_missing_and_symlink_targets(self):
        with self.assertRaises(sentinel.SentinelError):
            sentinel.validate_path(str(self.root / "missing"))
        target = self.root / "target"
        target.write_text("x")
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaisesRegex(sentinel.SentinelError, "link simbólico"):
            sentinel.validate_path(str(link))

    def test_scan_rejects_special_files(self):
        with patch.object(sentinel.Path, "resolve", side_effect=lambda *a, **k: Path("/dev/null")):
            with self.assertRaises(sentinel.SentinelError):
                sentinel.validate_path("/dev/null")

    def test_unavailable_clamav_is_reported(self):
        with patch.object(sentinel.shutil, "which", return_value=None):
            self.assertIn("não está instalado", sentinel.scan_path([str(self.root)]))

    def test_status_does_not_run_freshclam_or_services(self):
        with patch.object(sentinel.shutil, "which", side_effect=["/usr/bin/clamscan", "/usr/bin/freshclam"]), \
             patch.object(sentinel.subprocess, "run", return_value=Mock(stdout="ClamAV 1.5", stderr="", returncode=0)) as run:
            text = sentinel.status()
        self.assertIn("não foi iniciado", text)
        run.assert_called_once_with(["/usr/bin/clamscan", "--version"], capture_output=True,
                                    text=True, timeout=5, check=False)

    def test_validate_path_handles_quotes_and_expands_home(self):
        target = self.root / "um nome.txt"
        target.write_text("x")
        self.assertEqual(sentinel.validate_path(f'"{target}"'), target)

    def test_hash_lookup_disabled_by_default(self):
        target = self.root / "private.bin"
        target.write_bytes(b"private")
        with patch.object(sentinel, "scan_path", return_value="sem detecções"), \
             patch.object(sentinel, "_save_report"), \
             patch.object(sentinel, "_virustotal_hash") as query:
            text = sentinel.investigate_file(str(target), query_external=True, cfg={"sentinel": {}})
        query.assert_not_called()
        self.assertIn("nenhum arquivo foi enviado", text)
        self.assertNotIn("private", text.partition("VirusTotal:")[2])

    def test_hash_lookup_uses_only_hash_and_no_file_upload(self):
        target = self.root / "file.bin"
        target.write_bytes(b"content")
        with patch.object(sentinel, "scan_path", return_value="sem detecções"), \
             patch.object(sentinel, "_save_report"), \
             patch.object(sentinel, "_virustotal_hash", return_value="consulta de teste") as query:
            text = sentinel.investigate_file(str(target), query_external=True,
                                             cfg={"sentinel": {"share_hash_with_virustotal": True,
                                                                "virustotal_api_key": "secret"}})
        expected = __import__("hashlib").sha256(b"content").hexdigest()
        query.assert_called_once_with(expected, "secret")
        self.assertIn("consulta de teste", text)

    def test_virustotal_unavailable_is_not_malicious_or_clean(self):
        with patch.object(sentinel.urllib.request, "urlopen", side_effect=OSError("offline")):
            text = sentinel._virustotal_hash("a" * 64, "not-printed")
        self.assertIn("Consulta falhou", text)
        self.assertNotIn("not-printed", text)

    def test_virustotal_no_stats_is_inconclusive(self):
        response = Mock()
        response.read.return_value = json.dumps({"data": {"attributes": {}}}).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(sentinel.urllib.request, "urlopen", return_value=response):
            self.assertIn("inconclusivo", sentinel._virustotal_hash("b" * 64, "secret"))

    def test_cancel_without_task_is_harmless(self):
        self.assertIn("Não há verificação", sentinel.cancel_scan())

    def test_running_scan_can_be_cancelled(self):
        fake = self.root / "fake-clamscan"
        fake.write_text("#!/usr/bin/python3\nimport time\ntime.sleep(10)\n", encoding="utf-8")
        fake.chmod(0o700)
        target = self.root / "target.txt"
        target.write_text("safe")
        result = []
        with patch.object(sentinel.shutil, "which", return_value=str(fake)), \
             patch.object(sentinel, "_save_report"):
            worker = threading.Thread(target=lambda: result.append(sentinel.scan_path([str(target)], timeout=30)))
            worker.start()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with sentinel._lock:
                    active = sentinel._active
                if active is not None and active.poll() is None:
                    break
                time.sleep(0.02)
            self.assertIn("Cancelamento solicitado", sentinel.cancel_scan())
            worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertIn("cancelada", result[0])

    def test_process_output_omits_command_line_arguments(self):

        source = Path(sentinel.__file__).read_text()
        self.assertNotIn("/cmdline", source)

    def test_process_investigation_is_read_only_and_limited(self):
        with patch.object(sentinel, "_persistence_entries", return_value=[]), \
             patch.object(sentinel.shutil, "which", return_value=None), \
             patch.object(sentinel, "_save_report"):
            text = sentinel.investigate_process(os.getpid())
        self.assertIn("nenhum processo ou serviço foi alterado", text)
        self.assertIn("Classificação heurística", text)

    def test_persistence_audit_does_not_mutate(self):
        with patch.object(sentinel.Path, "home", return_value=self.root), \
             patch.object(sentinel.shutil, "which", return_value=None):
            sentinel._persistence_entries()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_natural_language_routes_downloads_only_on_explicit_request(self):
        with patch.object(sentinel, "scan_path", return_value="VERIFICADO") as scan:
            self.assertEqual(sentinel.handle_chat("Faça uma verificação de vírus na minha pasta Downloads", {}), "VERIFICADO")
        self.assertEqual(scan.call_args.args[0], [str(sentinel.HOME / "Downloads")])

    def test_explicit_full_home_scan_routes_to_home(self):
        with patch.object(sentinel, "scan_path", return_value="VERIFICADO") as scan:
            self.assertEqual(sentinel.handle_chat("Faça uma verificação completa do meu diretório pessoal", {}), "VERIFICADO")
        self.assertEqual(scan.call_args.args[0], [str(sentinel.HOME)])

    def test_brain_routes_explicit_request_without_calling_model(self):
        with patch.object(sentinel, "scan_path", return_value="VERIFICADO") as scan, \
             patch("mark.brain._ollama", side_effect=AssertionError("modelo não deveria ser chamado")):
            response = answer("Faça uma verificação de vírus na minha pasta Downloads", {}, ShortMemory(), origin="pc")
        self.assertEqual(response, "VERIFICADO")
        scan.assert_called_once()

    def test_informational_or_negative_request_does_not_scan(self):
        with patch.object(sentinel, "scan_path") as scan:
            self.assertIsNone(sentinel.handle_chat("Como faço uma verificação de vírus?", {}))
            self.assertIsNone(sentinel.handle_chat("Não faça verificação de vírus", {}))
        scan.assert_not_called()

    def test_ambiguous_scan_asks_for_scope(self):
        response = sentinel.handle_chat("Faça uma verificação de vírus", {})
        self.assertIn("Qual arquivo ou pasta", response)

    def test_quoted_path_with_spaces_is_preserved(self):
        self.assertEqual(sentinel._path_from_request('verifique o arquivo "/home/a pasta/teste.txt"'),
                         "/home/a pasta/teste.txt")

    def test_latest_report_handles_absence(self):
        with patch.object(sentinel, "REPORT_FILE", self.root / "no-report.json"):
            self.assertIn("Ainda não há", sentinel.latest_report())

    def test_import_does_not_create_background_tasks_or_report(self):

        text = Path(sentinel.__file__).read_text()
        self.assertNotIn("threading.Thread(", text)
        self.assertNotIn("Timer(", text)
        self.assertNotIn("cron", text[text.find("def "):text.find("def ") + 100])

    def _fake_proc(self, code, output):
        def create(argv, **kwargs):
            kwargs["stdout"].write(output)
            kwargs["stdout"].flush()
            return Mock(returncode=code, wait=Mock(return_value=code), poll=Mock(return_value=code))
        return create


if __name__ == "__main__":
    unittest.main()
