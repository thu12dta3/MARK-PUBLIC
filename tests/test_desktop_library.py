
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import tempfile
import time
import unittest
from unittest.mock import patch
from PyQt6.QtCore import QTimer,QMimeData,QUrl,Qt,QPointF,QPoint
from PyQt6.QtGui import QDropEvent,QDragEnterEvent
from PyQt6.QtWidgets import QApplication,QPushButton
from mark.desktop_library import LibraryPage,DropArea
from mark import desktop

class DesktopLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)
    def pump(self,predicate,timeout=10):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.app.processEvents()
            if predicate():return True
            time.sleep(.01)
        return False
    def test_drop_local_files_and_reject_urls(self):
        area=DropArea();received=[];area.files.connect(received.append)
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile('/tmp/test.pdf')])
        enter=QDragEnterEvent(QPoint(1,1),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier);area.dragEnterEvent(enter);self.assertTrue(enter.isAccepted())
        event=QDropEvent(QPointF(1,1),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier);area.dropEvent(event);self.assertEqual(received,[['/tmp/test.pdf']])
        mime.setUrls([QUrl('https://example.com/private')]);area.dropEvent(event);self.assertEqual(len(received),1)
    def test_background_page_500_entries_and_event_loop_responsive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);paths=[]
            for i in range(500):
                p=root/f'file-{i}.txt';p.write_text(f'capacitores energia fixture {i}');paths.append(str(p))
            page=LibraryPage(db=root/'knowledge.sqlite3',root=root/'library');page.show()
            try:
                self.assertTrue(self.pump(lambda:bool(page.snapshot)))
                page.worker.submit('pause',True);page.enqueue(paths)
                ticks=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start()
                self.assertTrue(self.pump(lambda:page.snapshot.get('files')==500,15));timer.stop()
                self.assertGreater(len(ticks),5);self.assertTrue(page.snapshot['paused']);self.assertEqual(page.queue_table.rowCount(),500)
                self.assertEqual(page.mode.currentData(),'reference');page.search.setText('capacitores');self.app.processEvents()
            finally:
                page.shutdown();self.assertTrue(self.pump(lambda:not page.worker.isRunning(),40));page.close();page.deleteLater();self.app.processEvents()
    def test_low_density_preserves_controls_in_menus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);page=LibraryPage(db=root/'knowledge.sqlite3',root=root/'library');page.show()
            try:
                self.assertTrue(self.pump(lambda:bool(page.snapshot)))
                visible=[button for button in page.findChildren(QPushButton) if button.isVisibleTo(page)]
                self.assertLessEqual(len(visible),3)
                self.assertFalse(page.queue_panel.isVisibleTo(page));self.assertFalse(page.progress.isVisibleTo(page));self.assertFalse(page.paging.isVisibleTo(page))
                self.assertEqual(page.table.columnCount(),4)
                labels=[action.text() for action in page.more_button.menu().actions()]
                self.assertIn('Opções de importação…',labels);self.assertIn('Espaço usado…',labels);self.assertIn('Reprocessar arquivos com erro',labels)
                page.queue_toggle.setChecked(True);self.app.processEvents();self.assertTrue(page.queue_panel.isVisibleTo(page));page.queue_toggle.setChecked(False)
                self.assertEqual(page.mode.currentData(),'reference')
            finally:page.shutdown();self.assertTrue(self.pump(lambda:not page.worker.isRunning(),40));page.close();page.deleteLater();self.app.processEvents()
    def test_window_library_and_chat_navigation(self):
        with patch.object(desktop.MarkWindow,'_load_history'),patch.object(desktop.MarkWindow,'refresh_status'):
            window=desktop.MarkWindow();self.assertTrue(hasattr(window,'chat_page'));self.assertTrue(hasattr(window,'content_layout'))
            window.status_timer.stop();window.history_timer.stop();window.close();window.deleteLater();self.app.processEvents()

    def test_work_areas_keep_navigation_and_project_context(self):
        with patch.object(desktop.MarkWindow,'_load_history'),patch.object(desktop.MarkWindow,'refresh_status'):
            window=desktop.MarkWindow()
        try:
            window.status_timer.stop();window.history_timer.stop()
            self.assertEqual(set(window.area_buttons),{'general','engineer','professor','library','evolution','settings'})
            self.assertEqual(window.page_stack.currentWidget(),window.chat_page)
            self.assertEqual(window.page_stack.count(),5)
            window.engineer_project_input.setText('mark-core')
            window.open_area('professor')
            self.assertEqual(window.page_stack.currentWidget(),window.area_pages['professor'])
            self.assertEqual(window.current_specialization,'general')
            window.open_area('engineer')
            self.assertEqual(window.page_stack.currentWidget(),window.area_pages['engineer'])
            window.open_specialized_chat('engineer')
            self.assertEqual(window.page_stack.currentWidget(),window.chat_page)
            self.assertEqual(window.current_specialization,'engineer')
            self.assertEqual(window.current_project_id,'mark-core')
            self.assertEqual(window.area_buttons['engineer'].objectName(),'navButtonActive')
            window.open_area('settings')
            self.assertEqual(window.page_stack.currentWidget(),window.area_pages['settings'])
            self.assertEqual(window.current_project_id,'mark-core')
        finally:
            window.close();window.deleteLater();self.app.processEvents()

if __name__=='__main__':unittest.main()

class DesktopChatRefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)
    def window(self):
        with patch.object(desktop.MarkWindow,'_load_history'),patch.object(desktop.MarkWindow,'refresh_status'):
            window=desktop.MarkWindow()
        window.status_timer.stop();window.history_timer.stop();window.resize(1000,680);window.show();return window
    def settle(self,seconds=.25):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.app.processEvents();time.sleep(.005)
    def test_links_safe_clickable_and_code_escaped(self):
        from mark.desktop_theme import message_html
        text=message_html('[MARK](https://github.com/thu12dta3/MARK) https://example.com/page. `https://code.example` <img src=x> [x](javascript:alert(1))')
        self.assertIn('href="https://github.com/thu12dta3/MARK"',text);self.assertIn('href="https://example.com/page"',text)
        self.assertNotIn('href="https://code.example"',text);self.assertNotIn('href="javascript',text);self.assertIn('&lt;img',text)
        window=self.window()
        try:
            with patch.object(desktop.QDesktopServices,'openUrl',return_value=True) as open_url:
                window._open_message_link('https://github.com/thu12dta3/MARK');window._open_message_link('file:///etc/passwd');window._open_message_link('javascript:alert(1)')
                self.assertEqual(open_url.call_count,1)
        finally:window.close();window.deleteLater();self.settle(.02)
    def test_answer_bubble_no_repeated_brand_and_auto_scroll_after_layout(self):
        from PyQt6.QtWidgets import QFrame,QLabel
        window=self.window()
        try:
            for i in range(15):window._append_bubble(('Resposta sintética com várias linhas.\n'*12)+str(i),'assistant')
            self.settle(.4);bar=window.scroll.verticalScrollBar();self.assertGreater(bar.maximum(),0);self.assertLessEqual(bar.maximum()-bar.value(),2)
            bubble=window.findChild(QFrame,'assistantBubble');self.assertIn('border-radius',bubble.styleSheet());self.assertNotIn('transparent',bubble.styleSheet())
            self.assertFalse(any(label.text()=='MARK' for label in bubble.findChildren(QLabel)))
            bar.setValue(0);window.follow_latest=False;self.settle(.1);self.assertEqual(bar.value(),0)
            window._append_bubble('Nova mensagem de conversa em andamento','assistant');self.settle(.3);self.assertLessEqual(bar.maximum()-bar.value(),2)
        finally:window.close();window.deleteLater();self.settle(.02)
    def test_pc_theme_monochrome_and_evolution_icon(self):
        from mark.desktop_theme import STYLESHEET, THEME_PALETTE
        self.assertIn(THEME_PALETTE['bg_canvas'], STYLESHEET)
        self.assertIn(THEME_PALETTE['accent_cobalt'], STYLESHEET)
        self.assertIn(THEME_PALETTE['accent_cyan'], STYLESHEET)
        self.assertTrue((desktop.ICON_DIR/'evolution.svg').is_file())
        self.assertIn('Evolução',Path(desktop.__file__).read_text())
