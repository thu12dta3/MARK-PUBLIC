
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import unittest
from unittest.mock import Mock,patch
from PyQt6.QtWidgets import QApplication
from mark.desktop_evolution import EvolutionDialog,progress_text


class EvolutionProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
    def test_progress_is_staged_and_interrupted_is_not_running(self):
        value={'status':'RUNNING','step':3,'total':7,'label':'Gerando candidato','started_at':time.time()-10}
        self.assertIn('Etapa 3 de 7',progress_text(value))
        self.assertIn('interrompida',progress_text(value|{'status':'INTERRUPTED'}))
        self.assertIn('Adiado',progress_text(value|{'status':'DEFERRED'}))
    def test_poll_updates_stage_without_resetting_selected_report(self):
        with patch.object(EvolutionDialog,'refresh'):
            dialog=EvolutionDialog()
        dialog.poll.stop()
        try:
            data={'status':{'paused':False,'enabled':True,'backlog':1,'schedule_enabled':True,
                  'progress':{'status':'RUNNING','step':4,'total':7,'label':'Testando versão atual','started_at':time.time()}},
                  'reports':[{'job_id':'JOB-a','experiment_id':'EXP-0001','status':'REJECTED'},
                             {'job_id':'JOB-b','experiment_id':'EXP-0002','status':'REVIEW_REQUIRED'}]}
            dialog.worker=Mock();dialog.done_refresh(data)
            self.assertTrue(dialog.history_panel.isHidden())
            self.assertTrue(dialog.diff.isHidden())
            self.assertEqual(dialog.result_label.text(),'Pronto para sua revisão')
            dialog.selector.setCurrentIndex(0)
            self.assertEqual(dialog.result_label.text(),'Pronto para sua revisão')
            before=dialog.details.toPlainText()
            data['status']['progress']['step']=5
            data['status']['progress']['label']='Testando candidato'
            dialog.worker=Mock();dialog.done_refresh(data)
            self.assertEqual(dialog.selector.currentIndex(),0)
            self.assertEqual(dialog.details.toPlainText(),before)
            self.assertEqual(dialog.progress_bar.value(),5)
            self.assertIn('Testando candidato',dialog.progress_label.text())
            dialog.history_toggle.setChecked(True);self.app.processEvents()
            self.assertFalse(dialog.history_panel.isHidden())
            dialog.history_toggle.setChecked(False);self.app.processEvents()
            self.assertTrue(dialog.history_panel.isHidden())
            self.assertEqual(dialog.selector.currentIndex(),0)
        finally:dialog.close();dialog.deleteLater();self.app.processEvents()
    def test_log_and_phase_report_persist_across_refresh_and_history_selection(self):
        with patch.object(EvolutionDialog,'refresh'):dialog=EvolutionDialog()
        dialog.poll.stop()
        try:
            records=[{'id':'new','kind':'phase_finished','phase':'CANDIDATE','status':'DONE','time':time.time(),'duration_seconds':2,'summary':{'passed':16,'total':16}},
                     {'id':'old','kind':'phase_finished','phase':'BASELINE','status':'DONE','time':time.time(),'summary':{'passed':12,'total':16}}]
            data={'status':{'paused':False,'enabled':True,'backlog':1,'schedule_enabled':True,'activity':list(reversed(records))},'reports':[],'phase_reports':records}
            dialog.worker=Mock();dialog.done_refresh(data)
            self.assertEqual(dialog.start_button.text(),'Iniciar');self.assertEqual(dialog.stop_button.text(),'Parar')
            self.assertIn('Testando candidato',dialog.log_view.toPlainText());self.assertIn('16/16',dialog.phase_report_view.text())
            dialog.phase_selector.setCurrentIndex(1)
            self.assertIn('12/16',dialog.phase_details.toPlainText());self.assertIn('16/16',dialog.phase_report_view.text())
            dialog.worker=Mock();dialog.done_refresh(data)
            self.assertEqual(dialog.phase_selector.currentIndex(),1);self.assertEqual(dialog.log_view.toPlainText().count('Testando candidato'),1)
        finally:dialog.close();dialog.deleteLater();self.app.processEvents()
