
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import hashlib
import json
import sqlite3
import time
import shlex
from pathlib import Path
from subprocess import CompletedProcess, Popen, PIPE
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtGui import QTextDocument
    from PyQt6.QtWidgets import QApplication, QLabel
    from mark import desktop
except ModuleNotFoundError as exc:
    if exc.name and exc.name.startswith("PyQt6"):
        raise unittest.SkipTest("PyQt6 ausente; rode esta suíte com /usr/bin/python3.") from exc
    raise


class DesktopSecurityTests(unittest.TestCase):
    def visible_text(self,window):
        texts=[]
        for label in window.findChildren(QLabel):
            text=label.text()
            if text.startswith('<div '):
                document=QTextDocument();document.setHtml(text);text=document.toPlainText()
            texts.append(text)
        return texts

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.original_quit_on_close = cls.app.quitOnLastWindowClosed()


        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        cls.app.setQuitOnLastWindowClosed(cls.original_quit_on_close)

    def test_token_requires_private_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "mobile.token"
            token_file.write_text("local-test-token\n", encoding="utf-8")
            token_file.chmod(0o600)
            with patch("mark.desktop_helpers.TOKEN_FILE", token_file):
                self.assertEqual(desktop._token(), "local-test-token")
                token_file.chmod(0o644)
                with self.assertRaises(PermissionError):
                    desktop._token()

    def test_task_delivers_result_only_after_thread_has_stopped(self):
        worker = desktop.Task(lambda: (time.sleep(0.05), "ok")[1])
        loop = QEventLoop()
        states = []

        def completed(value):
            states.append((value, worker.isRunning()))
            worker.deleteLater()
            loop.quit()

        worker.completed.connect(completed)
        worker.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        self.assertEqual(states, [("ok", False)])

    def test_menu_launcher_works_from_home_with_legacy_mark_py(self):
        launcher = Path(__file__).resolve().parents[1] / "scripts/mark-desktop.sh"
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        process = Popen([str(launcher)], cwd=Path.home(), env=environment,
                        stdout=PIPE, stderr=PIPE)
        try:
            time.sleep(0.8)
            if process.poll() is not None:
                error = process.communicate(timeout=2)[1].decode(errors="replace")
                self.fail("O aplicativo do menu terminou antes de abrir: " + error)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)

    def test_agenda_waits_for_worker_before_closing(self):
        with patch.object(desktop.AgendaDialog, "refresh"):
            dialog = desktop.AgendaDialog()
        dialog.show()
        dialog._start_task(lambda: (time.sleep(0.15), None)[1], lambda _: None, lambda _: None)
        dialog.close()
        self.assertTrue(dialog.isVisible())
        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        dialog.close()
        self.assertFalse(dialog.isVisible())

    def test_open_desktop_receives_phone_turn_without_duplicate_bubbles(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "conversation.sqlite3"
            db = sqlite3.connect(history)
            try:
                db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, created_at INTEGER, "
                           "source TEXT, user_text TEXT, assistant_text TEXT)")
                db.commit()
            finally:
                db.close()
            history.chmod(0o600)
            with patch.object(desktop, "MEMORY_FILE", history):
                window = desktop.MarkWindow()
                db = sqlite3.connect(history)
                try:
                    db.execute("INSERT INTO turns VALUES (1, 1, 'mobile-chat', 'pergunta do celular', 'resposta')")
                    db.commit()
                finally:
                    db.close()
                window._sync_history()
                window._sync_history()
                visible = self.visible_text(window)
                self.assertEqual(visible.count("pergunta do celular"), 1)
                self.assertEqual(visible.count("resposta"), 1)
                window.close()

    def test_empty_shared_history_keeps_welcome_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "conversation.sqlite3"
            db = sqlite3.connect(history)
            try:
                db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, created_at INTEGER, "
                           "source TEXT, user_text TEXT, assistant_text TEXT)")
                db.commit()
            finally:
                db.close()
            history.chmod(0o600)
            with patch.object(desktop, "MEMORY_FILE", history):
                window = desktop.MarkWindow()
                self.assertIsNotNone(window.empty_state)
                window._append_bubble("Olá", "assistant")
                self.assertIsNone(window.empty_state)
                window.close()

    def test_pc_call_opens_local_dialog_without_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop, "MEMORY_FILE", Path(directory) / "missing.sqlite3"):
                window = desktop.MarkWindow()
        with patch.object(desktop.CallDialog, "exec", return_value=0) as call, \
             patch.object(desktop.QDesktopServices, "openUrl") as browser:
            window.open_call()
        call.assert_called_once()
        browser.assert_not_called()
        window.close()

    def test_token_refuses_symbolic_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "real-token"
            target.write_text("local-test-token\n", encoding="utf-8")
            target.chmod(0o600)
            link = root / "mobile.token"
            link.symlink_to(target)
            with patch("mark.desktop_helpers.TOKEN_FILE", link):
                with self.assertRaises(OSError):
                    desktop._token()

    def test_service_control_uses_fixed_argument_list_without_shell(self):
        result = CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.object(desktop.subprocess, "run", return_value=result) as run:
            desktop._service("start", desktop.VOICE_SERVICE)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["systemctl", "--user", "start", "mark-voice.service"])
        self.assertFalse(kwargs.get("shell", False))

    def test_api_transport_targets_only_loopback(self):
        from urllib.parse import urlsplit
        self.assertEqual(urlsplit(desktop.API).hostname, "127.0.0.1")

    def test_chat_composer_sends_mark_request_and_displays_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop, "MEMORY_FILE", Path(directory) / "missing.sqlite3"):
                window = desktop.MarkWindow()
        chat_requests = []

        def fake_request(path, payload=None, timeout=240):
            if path == "/health":
                return 200, b'{"status":"ok"}'
            chat_requests.append(payload)
            return 200, b'{"choices":[{"message":{"content":"Resposta de teste"}}]}'

        with patch.object(desktop, "_request", side_effect=fake_request):
            window.input.setPlainText("Olá, MARK")
            window.send_message()
            loop = QEventLoop()

            def finish_when_idle():
                if not window.busy and not window.tasks:
                    loop.quit()
                else:
                    QTimer.singleShot(20, finish_when_idle)

            QTimer.singleShot(20, finish_when_idle)
            QTimer.singleShot(4000, loop.quit)
            loop.exec()

        self.assertFalse(window.busy)
        self.assertEqual(chat_requests[0]["model"], "mark")
        self.assertEqual(chat_requests[0]["messages"][0]["content"],
                         [{"type": "text", "text": "Olá, MARK"}])
        rendered = self.visible_text(window)
        self.assertIn("Resposta de teste", rendered)
        window.close()

    def test_clearing_shared_memory_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop, "MEMORY_FILE", Path(directory) / "missing.sqlite3"):
                window = desktop.MarkWindow()
        with patch.object(desktop.QInputDialog, "getText", return_value=("", False)), \
             patch.object(desktop, "_request") as request:
            window.clear_memory()
        request.assert_not_called()
        window.close()

    def test_chat_renders_user_text_as_escaped_plain_content(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop, "MEMORY_FILE", Path(directory) / "missing.sqlite3"):
                window = desktop.MarkWindow()
        payload = '<img src=x onerror="alert(1)">'
        window._append_bubble(payload, "user")
        rendered = [label.text() for label in window.findChildren(QLabel)]
        self.assertTrue(any("&lt;img" in value for value in rendered))
        self.assertFalse(any(payload in value for value in rendered))
        window.close()

    def test_tuta_email_composer_opens_reviewable_draft_not_sends(self):
        dialog = desktop.EmailComposerDialog("Texto de teste")
        dialog.recipient.setText("destino@example.com")
        dialog.subject.setText("Assunto de teste")
        with patch.object(desktop.QDesktopServices, "openUrl", return_value=True) as open_url:
            dialog.open_draft()
        target = open_url.call_args.args[0]
        self.assertEqual(target.scheme(), "mailto")
        self.assertIn("destino@example.com", target.toString())
        self.assertIn("Texto%20de%20teste", bytes(target.toEncoded()).decode())
        self.assertEqual(dialog.result(), desktop.QDialog.DialogCode.Accepted)

    def test_tuta_email_composer_rejects_bad_address_without_opening_client(self):
        dialog = desktop.EmailComposerDialog()
        dialog.recipient.setText("endereco-invalido")
        with patch.object(desktop.QDesktopServices, "openUrl") as open_url, \
             patch.object(desktop.QMessageBox, "warning") as warning:
            dialog.open_draft()
        warning.assert_called_once()
        open_url.assert_not_called()
        self.assertEqual(dialog.result(), desktop.QDialog.DialogCode.Rejected)

    def test_desktop_entry_has_icon_and_does_not_open_terminal(self):
        root = Path(__file__).resolve().parents[1]
        entry = (root / "packaging/mark.desktop").read_text(encoding="utf-8")
        self.assertIn("Terminal=false", entry)
        fields = dict(line.split("=", 1) for line in entry.splitlines() if "=" in line)


        launcher = Path(shlex.split(fields["Exec"])[0])
        icon = Path(fields["Icon"])
        self.assertEqual(launcher.name, "mark-desktop.sh")
        self.assertEqual(icon, launcher.parent.parent / "assets/mark.svg")
        self.assertTrue(launcher.is_file())
        self.assertFalse(desktop.QIcon(str(icon)).isNull())
        self.assertFalse(("x11" in entry.lower()) or ("wayland" in entry.lower()))
        self.assertFalse(desktop.QIcon(str(root / "assets/mark.svg")).isNull())

    def test_mobile_pairing_checks_live_tls13_api_before_opening_enrollment(self):
        endpoint = MagicMock()
        endpoint.sock.version.return_value = "TLSv1.3"
        endpoint.sock.getpeercert.return_value = b"test-certificate"
        endpoint.getresponse.return_value.status = 200
        endpoint.getresponse.return_value.read.return_value = b'{"status":"unknown"}'
        registry = MagicMock()
        registry.open_enrollment.return_value = "test-code"
        tailscale = CompletedProcess([], 0, json.dumps({"Self": {"DNSName": "mark.example.ts.net."}}), "")
        with patch.object(desktop.subprocess, "run", return_value=tailscale), \
             patch("mark.desktop_helpers.HTTPSConnection", return_value=endpoint), \
             patch("mark.device_auth.DeviceRegistry", return_value=registry):
            pairing = desktop._mobile_pairing()
        endpoint.request.assert_called_once_with("GET", "/v1/pair/status", headers={"X-Mark-Device": "probe"})
        registry.open_enrollment.assert_called_once()
        registry.close.assert_called_once()
        self.assertEqual(pairing["url"], "https://mark.example.ts.net")
        self.assertEqual(pairing["pin"], hashlib.sha256(b"test-certificate").hexdigest())

    def test_mobile_pairing_refuses_downstream_api_failure(self):
        endpoint = MagicMock()
        endpoint.sock.version.return_value = "TLSv1.3"
        endpoint.sock.getpeercert.return_value = b"test-certificate"
        endpoint.getresponse.return_value.status = 502
        tailscale = CompletedProcess([], 0, json.dumps({"Self": {"DNSName": "mark.example.ts.net"}}), "")
        with patch.object(desktop.subprocess, "run", return_value=tailscale), \
             patch("mark.desktop_helpers.HTTPSConnection", return_value=endpoint), \
             patch("mark.device_auth.DeviceRegistry") as registry:
            with self.assertRaises(RuntimeError):
                desktop._mobile_pairing()
        registry.assert_not_called()

    def test_mobile_qr_code_is_passed_through_stdin_not_process_arguments(self):
        registry = MagicMock()
        registry.list_devices.return_value = []
        registry.lockdown.return_value = False
        with patch("mark.device_auth.DeviceRegistry", return_value=registry):
            dialog = desktop.MobileDevicesDialog()
            with patch.object(desktop.subprocess, "run", return_value=CompletedProcess([], 1, b"", b"")) as run:
                dialog._show_pairing({"url": "https://mark.example.ts.net", "code": "test-code", "pin": "a" * 64})
            self.assertNotIn("test-code", " ".join(run.call_args.args[0]))
            self.assertEqual(json.loads(run.call_args.kwargs["input"])["code"], "test-code")
            self.assertIn("manualmente", dialog.status.text())
            dialog.close()


    def test_escape_in_call_stops_audio_and_waits_for_pending_worker(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        dialog=desktop.CallDialog();dialog.show();self.app.processEvents()
        dialog.worker=MagicMock()
        with patch.object(dialog,'_stop_call') as stop:
            QTest.keyClick(dialog,Qt.Key.Key_Escape);self.app.processEvents()
            stop.assert_called_once()
        self.assertTrue(dialog.closing_when_idle);self.assertTrue(dialog.isVisible())
        dialog.worker=None;dialog.close();self.assertFalse(dialog.isVisible())

    def test_escape_in_agenda_waits_for_write(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        with patch.object(desktop.AgendaDialog,'refresh'):dialog=desktop.AgendaDialog()
        dialog.show();self.app.processEvents();dialog.workers.add(MagicMock())
        QTest.keyClick(dialog,Qt.Key.Key_Escape);self.app.processEvents()
        self.assertTrue(dialog.isVisible());dialog.workers.clear();dialog.close()

    def test_escape_in_github_waits_for_pending_operation(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        with patch.object(desktop.GitHubDialog,'refresh_repositories'):dialog=desktop.GitHubDialog()
        dialog.show();self.app.processEvents();dialog.workers.add(MagicMock())
        QTest.keyClick(dialog,Qt.Key.Key_Escape);self.app.processEvents()
        self.assertTrue(dialog.isVisible());self.assertIn('Aguarde',dialog.status.text())
        dialog.workers.clear();dialog.close()

    def test_escape_in_devices_waits_and_then_clears_pairing(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        with patch.object(desktop.MobileDevicesDialog,'refresh_devices'):dialog=desktop.MobileDevicesDialog()
        dialog.show();self.app.processEvents();dialog.worker=MagicMock();dialog.worker.isRunning.return_value=True
        dialog.details.setPlainText('Fixture de pareamento')
        QTest.keyClick(dialog,Qt.Key.Key_Escape);self.app.processEvents()
        self.assertTrue(dialog.isVisible());dialog.worker=None;dialog.close()
        self.assertFalse(dialog.isVisible());self.assertEqual(dialog.details.toPlainText(),'')

if __name__ == "__main__":
    unittest.main()
