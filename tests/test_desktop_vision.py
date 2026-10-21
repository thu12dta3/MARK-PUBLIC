
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
try:
    from PyQt6.QtWidgets import QApplication
    from mark.desktop_vision import VisionDialog, VisionWorker
except ModuleNotFoundError as exc:
    if exc.name.startswith('PyQt6'): raise unittest.SkipTest('Execute com Python do sistema.') from exc
    raise
from test_vision import fixture
from mark.vision_quality import inspect_frame
from mark.vision_live import LiveFrame


class DesktopVisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def cfg(self):
        return {'camera':{'source':'usb','timeout':1},'ollama':{'timeout':1},
                'models':{'call':'qwen3-vl:4b-instruct'}}

    def test_opening_window_never_activates_camera_or_model(self):
        with patch('mark.desktop_vision.load_config',return_value=self.cfg()),patch('mark.vision_live.subprocess.Popen',side_effect=AssertionError):
            dialog=VisionDialog();dialog.show();self.app.processEvents()
            self.assertIsNone(dialog.worker);self.assertFalse(dialog.analyze_button.isEnabled())
            dialog.close()

    def test_preview_clears_when_frame_stale(self):
        with patch('mark.desktop_vision.load_config',return_value=self.cfg()):dialog=VisionDialog()
        camera=Mock();camera.latest.side_effect=RuntimeError('Sem quadro recente')
        worker=Mock();worker.stop_requested.is_set.return_value=False;worker.camera=camera
        dialog.worker=worker;dialog.preview.setText('anterior');dialog._capture_time=time.monotonic()-10
        dialog.refresh_preview()
        self.assertIn('Aguardando',dialog.preview.text());self.assertIn('desatualizada',dialog.age.text())
        dialog.worker=None;dialog.close()

    def test_stopping_stream_does_not_wait_for_model_on_gui_thread(self):
        with patch('mark.desktop_vision.load_config',return_value=self.cfg()):dialog=VisionDialog()
        worker=Mock();dialog.worker=worker;dialog.stop()
        worker.stop.assert_called_once();worker.camera.stop.assert_called_once()
        worker.camera.close.assert_not_called()
        self.assertFalse(dialog.analyze_button.isEnabled())
        dialog.worker=None;dialog.close()

    def test_pending_answer_is_discarded_after_stop(self):
        worker=VisionWorker(self.cfg());data=fixture(marks=True)
        frame=LiveFrame(data,time.monotonic(),1,inspect_frame(data));results=[]
        worker.result.connect(lambda *args:results.append(args))
        worker.camera=Mock();worker.camera.latest.return_value=frame
        session=Mock();session.awake=True
        def finish(*args,**kwargs):worker.stop();return 'Resultado atrasado'
        with patch('mark.desktop_vision.ModelSession',return_value=session),patch('mark.desktop_vision._ollama',side_effect=finish):
            worker.run()
        self.assertEqual(results,[]);worker.camera.close.assert_called_once();session.sleep.assert_called_once()

    def test_next_inference_uses_latest_frame_without_queued_frames(self):
        worker=VisionWorker(self.cfg());data1=fixture('red');data2=fixture('blue')
        frames=[LiveFrame(data1,time.monotonic(),1,inspect_frame(data1)),LiveFrame(data2,time.monotonic(),2,inspect_frame(data2))]
        worker.camera=Mock();worker.camera.latest.side_effect=[frames[0],frames[0],frames[1]]
        calls=[];session=Mock();session.awake=True
        def reply(*args,**kwargs):
            calls.append(kwargs['images'])
            if len(calls)==1:worker.request('Conte as formas',False,True)
            else:worker.stop()
            return 'Observação'
        with patch('mark.desktop_vision.ModelSession',return_value=session),patch('mark.desktop_vision._ollama',side_effect=reply):worker.run()
        self.assertEqual(len(calls),2);self.assertNotEqual(calls[0],calls[1])
        self.assertFalse(worker.cfg['ollama']['think'])

    def test_close_waits_for_worker_before_destroying_qthread(self):
        with patch('mark.desktop_vision.load_config',return_value=self.cfg()):dialog=VisionDialog()
        worker=Mock();dialog.worker=worker;event=Mock()
        dialog.closeEvent(event)
        event.ignore.assert_called_once();self.assertTrue(dialog._closing)
        worker.stop.assert_called_once();dialog.worker=None;dialog.close()

    def test_old_result_ignored_after_new_question_or_stop(self):
        with patch('mark.desktop_vision.load_config',return_value=self.cfg()):dialog=VisionDialog()
        worker=Mock();worker.stop_requested.is_set.return_value=False;dialog.worker=worker;dialog._request_revision=2
        dialog.show_result('Antigo',1,time.monotonic(),1)
        self.assertEqual(dialog.observation.toPlainText(),'')
        dialog.show_result('Atual',1,time.monotonic(),2)
        self.assertEqual(dialog.observation.toPlainText(),'Atual')
        worker.stop_requested.is_set.return_value=True;dialog.show_result('Tardio',1,time.monotonic(),2)
        self.assertEqual(dialog.observation.toPlainText(),'Atual');dialog.worker=None;dialog.close()

    def test_changing_focus_clears_previous_observation_and_requests_new_analysis(self):
        with patch('mark.desktop_vision.load_config',return_value=self.cfg()):dialog=VisionDialog()
        worker=Mock();worker.stop_requested.is_set.return_value=False;worker.request.return_value=3
        dialog.worker=worker;dialog.observation.setPlainText('Cena antiga');dialog._capture_time=time.monotonic()
        dialog.focus_changed((0,0,.5,.5))
        self.assertEqual(dialog.observation.toPlainText(),'');self.assertIsNone(dialog._capture_time)
        self.assertEqual(dialog._request_revision,3);self.assertTrue(dialog.reset_focus.isEnabled())
        self.assertEqual(worker.request.call_args.args[-1],(0,0,.5,.5));dialog.worker=None;dialog.close()

    def test_worker_sends_scene_and_crop_from_same_frame(self):
        import base64,io
        from PIL import Image
        worker=VisionWorker(self.cfg());worker.request('Leia a área',False,True,(0,0,.5,1))
        data=fixture('red');frame=LiveFrame(data,time.monotonic(),1,inspect_frame(data))
        worker.camera=Mock();worker.camera.latest.return_value=frame
        captured=[];session=Mock();session.awake=True
        def reply(*args,**kwargs):captured.append((args,kwargs));worker.stop();return 'Texto'
        with patch('mark.desktop_vision.ModelSession',return_value=session),patch('mark.desktop_vision._ollama',side_effect=reply):worker.run()
        args,kwargs=captured[0];self.assertEqual(len(kwargs['images']),2);self.assertIn('Imagem 2',args[2])
        sizes=[]
        for data in kwargs['images']:
            with Image.open(io.BytesIO(base64.b64decode(data))) as image:sizes.append(image.size)
        self.assertEqual(sizes,[(160,120),(80,120)])

    def test_superseded_inference_never_emits_its_response(self):
        worker=VisionWorker(self.cfg());data=fixture();frame=LiveFrame(data,time.monotonic(),1,inspect_frame(data))
        worker.camera=Mock();worker.camera.latest.return_value=frame;results=[];calls=[]
        worker.result.connect(lambda *args:results.append(args));session=Mock();session.awake=True
        def reply(*args,**kwargs):
            calls.append(args[0])
            if len(calls)==1:worker.request('Outra pergunta',False,True)
            else:worker.stop()
            return 'Resposta da pergunta anterior'
        with patch('mark.desktop_vision.ModelSession',return_value=session),patch('mark.desktop_vision._ollama',side_effect=reply):worker.run()
        self.assertEqual(len(calls),2);self.assertEqual(results,[])

    def test_escape_uses_worker_shutdown_and_keeps_dialog_until_finished(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        with patch('mark.desktop_vision.load_config',return_value=self.cfg()):dialog=VisionDialog()
        dialog.show();self.app.processEvents();worker=Mock();dialog.worker=worker
        QTest.keyClick(dialog,Qt.Key.Key_Escape);self.app.processEvents()
        worker.stop.assert_called_once();self.assertTrue(dialog.isVisible());self.assertTrue(dialog._closing)
        dialog.worker=None;dialog.close();self.assertFalse(dialog.isVisible())

    def test_mouse_selection_accounts_for_letterbox_and_reverse_drag(self):
        from PyQt6.QtCore import Qt,QPoint
        from PyQt6.QtTest import QTest
        from mark.desktop_vision_preview import VisionPreview
        preview=VisionPreview();preview.resize(400,400);preview.set_frame(fixture());preview.show();self.app.processEvents()
        area=preview.image_rect();self.assertGreater(area.top(),0)
        changes=[];preview.focusChanged.connect(changes.append)
        a=QPoint(round(area.left()+area.width()*.75),round(area.top()+area.height()*.75))
        b=QPoint(round(area.left()+area.width()*.25),round(area.top()+area.height()*.25))
        QTest.mousePress(preview,Qt.MouseButton.LeftButton,pos=a)
        QTest.mouseMove(preview,b);QTest.mouseRelease(preview,Qt.MouseButton.LeftButton,pos=b)
        self.assertEqual(len(changes),1)
        for value,expected in zip(preview.focus,(.25,.25,.75,.75)):self.assertAlmostEqual(value,expected,delta=.01)
        preview.resize(600,350);self.assertEqual(preview.focus,changes[0]);preview.close()

    def test_letterbox_click_and_tiny_selection_do_not_change_focus(self):
        from PyQt6.QtCore import Qt,QPoint
        from PyQt6.QtTest import QTest
        from mark.desktop_vision_preview import VisionPreview
        preview=VisionPreview();preview.resize(400,400);preview.set_frame(fixture());preview.show();self.app.processEvents()
        failures=[];preview.selectionFailed.connect(failures.append)
        QTest.mouseClick(preview,Qt.MouseButton.LeftButton,pos=QPoint(10,10));self.assertIsNone(preview.focus)
        center=preview.image_rect().center().toPoint()
        QTest.mousePress(preview,Qt.MouseButton.LeftButton,pos=center)
        QTest.mouseRelease(preview,Qt.MouseButton.LeftButton,pos=center+QPoint(1,1))
        self.assertIsNone(preview.focus);self.assertEqual(len(failures),1);preview.close()
