
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from PyQt6.QtCore import QTimer,Qt
from PyQt6.QtWidgets import QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QPlainTextEdit,QProgressBar,QWidget,QFrame,QSizePolicy
from .desktop_dialogs import WorkerDialog
from .desktop_task import Task
from .evolution.state import State
from .evolution.engine import Engine
from .evolution.activity import log_line,phase_report_text,PROGRESS_STAGES

ROOT=Path(__file__).resolve().parents[2]
RESULT_LABELS={'REVIEW_REQUIRED':'Pronto para sua revisão','REJECTED':'Candidato descartado',
    'FAILED':'Teste não concluído','CANCELLED':'Experimento cancelado','RUNNING':'Em andamento'}


def progress_text(progress):
    status=progress.get('status')
    if not status:return 'Aguardando execução.'
    if status=='DEFERRED' and progress.get('wait_reason')=='training_interval':return 'Aguardando o intervalo de 24 horas entre rodadas de treino.'
    if status=='DEFERRED' and progress.get('wait_reason')=='repository':return 'Aguardando uma versão estável do repositório. Nova tentativa automática no próximo ciclo.'
    if status=='DEFERRED':return 'Aguardando intervalo entre execuções.' if progress.get('wait_reason')=='interval' else 'Adiado: você está usando o modelo. Nova tentativa no próximo ciclo.'
    if status=='INTERRUPTED':return 'Execução interrompida. O último estágio foi: '+progress.get('label','desconhecido')+'.'
    if status=='FAILED':return 'A execução falhou em: '+progress.get('label','desconhecido')+'. Consulte o resultado abaixo.'
    if status=='CANCELLED':return 'Experimento cancelado.'
    end=time.time() if status=='RUNNING' else progress.get('updated_at',time.time())
    elapsed=max(0,int(end-progress.get('started_at',end)))
    duration=f'{elapsed//60} min {elapsed%60:02d} s' if elapsed>=60 else f'{elapsed} s'
    if status=='DONE':return 'Ciclo concluído · '+duration
    return f"Etapa {progress.get('step',0)} de {progress.get('total',7)} · {progress.get('label','Em andamento')} · {duration}"


class EvolutionDialog(WorkerDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('MARK · Autoevolução');self.resize(640,570)
        self.worker=None;self.closing=False;self.reports=[];self.advanced=False
        self.display_key=None;self.paused=False;self.phase_reports=[];self.log_text=None
        from .evolution import cli as evolution_cli
        self.run_control=getattr(evolution_cli,'control_run',None)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,20,20,20);layout.setSpacing(12)
        header=QHBoxLayout();title=QLabel('Autoevolução');title.setStyleSheet('font-size: 22px; font-weight: 600;')
        header.addWidget(title);header.addStretch()
        self.state_label=QLabel('Carregando…');self.state_label.setStyleSheet('color: #aaaaaa;');header.addWidget(self.state_label);layout.addLayout(header)
        self.progress_label=QLabel('Aguardando execução.');self.progress_label.setWordWrap(True);layout.addWidget(self.progress_label)
        self.progress_bar=QProgressBar();self.progress_bar.setRange(0,7);self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet('QProgressBar { min-height: 6px; max-height: 6px; border: none; background: #292929; border-radius: 3px; } QProgressBar::chunk { background: #aaaaaa; border-radius: 3px; }')
        layout.addWidget(self.progress_bar)
        self.metadata=QLabel('');self.metadata.setWordWrap(True);self.metadata.setStyleSheet('color: #999999; font-size: 12px;');layout.addWidget(self.metadata)
        controls=QHBoxLayout()
        self.start_button=QPushButton('Iniciar');self.start_button.clicked.connect(lambda:self.refresh('start'));controls.addWidget(self.start_button)
        self.stop_button=QPushButton('Parar');self.stop_button.clicked.connect(lambda:self.refresh('stop'));controls.addWidget(self.stop_button)
        controls.addStretch()
        self.history_toggle=QPushButton('Histórico');self.history_toggle.setCheckable(True);self.history_toggle.toggled.connect(self.toggle_history);controls.addWidget(self.history_toggle)
        layout.addLayout(controls)
        layout.addWidget(QLabel('Log'))
        self.log_view=QPlainTextEdit();self.log_view.setReadOnly(True);self.log_view.setMaximumBlockCount(40);self.log_view.setFixedHeight(105)
        self.log_view.setStyleSheet('font-size: 12px;');layout.addWidget(self.log_view)
        card=QFrame();card.setStyleSheet('QFrame { background: #191919; border-radius: 10px; }')
        card.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        card_layout=QVBoxLayout(card);card_layout.setContentsMargins(16,14,16,14);card_layout.setSpacing(6)
        caption=QLabel('Relatório da última etapa');caption.setStyleSheet('color: #aaaaaa; font-size: 12px;');card_layout.addWidget(caption)
        self.result_label=QLabel('Nenhum experimento concluído.');self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        self.result_label.setWordWrap(True);self.result_label.setStyleSheet('font-size: 16px; font-weight: 500;');card_layout.addWidget(self.result_label)
        self.result_metrics=QLabel('');self.result_metrics.setStyleSheet('color: #aaaaaa;');card_layout.addWidget(self.result_metrics)
        self.phase_report_view=QLabel();self.phase_report_view.setWordWrap(True);self.phase_report_view.setTextFormat(Qt.TextFormat.PlainText)
        self.phase_report_view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.phase_report_view.setStyleSheet('font-size: 13px;');card_layout.addWidget(self.phase_report_view);self.phase_report_view.hide()
        layout.addWidget(card)
        self.history_panel=QWidget();history=QVBoxLayout(self.history_panel);history.setContentsMargins(0,0,0,0)
        self.phase_selector=QComboBox();self.phase_selector.currentIndexChanged.connect(self.show_phase_report);history.addWidget(self.phase_selector)
        self.phase_details=QPlainTextEdit();self.phase_details.setReadOnly(True);self.phase_details.setFixedHeight(110);history.addWidget(self.phase_details)
        self.selector=QComboBox();self.selector.setObjectName('modelSelector');self.selector.currentIndexChanged.connect(self.show_report);history.addWidget(self.selector)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMinimumHeight(110);history.addWidget(self.details)
        advanced=QPushButton('Ver dados técnicos');advanced.setCheckable(True);advanced.toggled.connect(self.toggle_advanced);history.addWidget(advanced)
        self.diff=QPlainTextEdit();self.diff.setReadOnly(True);self.diff.setPlaceholderText('Diff do candidato — apenas revisão');history.addWidget(self.diff);self.diff.hide()
        layout.addWidget(self.history_panel);self.history_panel.hide()
        note=QLabel('Cada etapa gera um relatório. Melhorias passam pela sua revisão.');note.setWordWrap(True);note.setStyleSheet('color: #888888; font-size: 11px;');layout.addWidget(note)
        self.refresh()
        self.poll=QTimer(self);self.poll.setInterval(2000);self.poll.timeout.connect(self.refresh);self.poll.start()
    def refresh(self,action=None):
        if self.worker is not None:return
        def operation():
            with State() as state:
                if action in {'start','stop'}:
                    if self.run_control:self.run_control(state,action)
                    elif action=='stop':state.control('pause');state.control('cancel')
                    else:raise RuntimeError('A instalação da rotina automática ainda está pendente.')
                elif action:state.control(action)
                phases=state.phase_reports() if hasattr(state,'phase_reports') else []
                return {'status':state.status(),'backlog':state.backlog(),'reports':Engine(ROOT,state).reports(),'phase_reports':phases}
        self.worker=Task(operation,self);self.worker.completed.connect(self.done_refresh)
        self.worker.failed.connect(self.failed_refresh);self.worker.start()
    def done_refresh(self,data):
        self.worker.deleteLater();self.worker=None
        status=data['status'];self.paused=status['paused']
        progress=status.get('progress',{})
        running=progress.get('status')=='RUNNING'
        self.state_label.setText('Parando…' if status['paused'] and running else 'Parada' if status['paused'] or not status['schedule_enabled'] else 'Em execução' if running else 'Aguardando')
        self.progress_label.setText('Autoevolução parada.' if status['paused'] and not running else progress_text(progress))
        self.progress_bar.setValue(progress.get('step',0))
        self.progress_bar.setVisible(bool(progress))
        units=Path.home()/'.config/systemd/user'
        installed=self.run_control is not None and all((units/name).is_file() and not (units/name).is_symlink() for name in ('mark-evolution.service','mark-evolution.timer'))
        self.start_button.setEnabled(installed and not status['schedule_enabled'] and not running)
        self.start_button.setToolTip('Iniciar a rotina e executar um ciclo permitido.' if installed else 'A instalação da rotina automática está pendente.')
        self.stop_button.setEnabled(status['schedule_enabled'] or running)
        metadata='Rotina automática ativa' if status['schedule_enabled'] else 'Rotina automática desativada'
        if status.get('last_cycle'):
            metadata+=' · último ciclo '+datetime.fromtimestamp(status['last_cycle'],ZoneInfo('America/Sao_Paulo')).strftime('%d/%m %H:%M')
        self.metadata.setText(metadata)
        activity=status.get('activity',[])
        lines='\n'.join(log_line(item) for item in activity[-24:]) if activity else ('Aguardando Iniciar.' if installed else 'Rotina aguardando instalação e ativação.')
        if lines!=self.log_text:
            bar=self.log_view.verticalScrollBar();following=bar.value()>=bar.maximum();position=bar.value()
            self.log_view.setPlainText(lines);bar.setValue(bar.maximum() if following else min(position,bar.maximum()));self.log_text=lines
        selected_phase=self.phase_reports[self.phase_selector.currentIndex()].get('id') if 0<=self.phase_selector.currentIndex()<len(self.phase_reports) else None
        self.phase_reports=data.get('phase_reports',[]);self.phase_selector.blockSignals(True);self.phase_selector.clear()
        for record in self.phase_reports:self.phase_selector.addItem(PROGRESS_STAGES.get(record.get('phase'),(0,'Etapa'))[1])
        if self.phase_reports:self.phase_selector.setCurrentIndex(next((i for i,r in enumerate(self.phase_reports) if r.get('id')==selected_phase),0))
        self.phase_selector.blockSignals(False);self.show_phase_report()
        current=self.selector.currentIndex()
        selected=self.reports[current].get('job_id') if 0<=current<len(self.reports) else None
        self.reports=data['reports'];self.selector.blockSignals(True);self.selector.clear()
        for report in self.reports:self.selector.addItem(str(report.get('experiment_id','Em andamento'))+' · '+RESULT_LABELS.get(report.get('status'),'Em andamento'))
        if self.reports:self.selector.setCurrentIndex(next((i for i,r in enumerate(self.reports) if r.get('job_id')==selected),len(self.reports)-1))
        self.selector.blockSignals(False)
        completed=[r for r in self.reports if r.get('experiment_id')]
        if completed:
            latest=max(completed,key=lambda r:r['experiment_id']);before=latest.get('before',{});after=latest.get('after',{})
            self.result_label.setText(RESULT_LABELS.get(latest.get('status'),'Em andamento'))
            self.result_metrics.setText(f"{before.get('passed','—')}/{before.get('total','—')} → {after.get('passed','—')}/{after.get('total','—')} testes" if before and after else '')
        else:self.result_label.setText('Nenhum experimento concluído.');self.result_metrics.clear()
        self.phase_report_view.setVisible(bool(self.phase_reports));self.result_metrics.setVisible(not self.phase_reports)
        if self.phase_reports:
            latest=self.phase_reports[0];text=phase_report_text(latest)
            self.result_label.setText(text.split('\n',1)[0]);self.phase_report_view.setText(text.split('\n',1)[-1])
        if not self.reports:self.details.setPlainText('Os experimentos e seus resultados aparecerão aqui.')
        self.show_report()
        if self.closing:self.close()
    def failed_refresh(self,message):
        self.worker.deleteLater();self.worker=None;self.state_label.setText('Não consegui carregar o estado: '+message)
        self.log_view.appendPlainText('Não foi possível concluir a ação: '+message)
        if self.closing:self.close()
    def toggle_advanced(self,value):
        self.advanced=value;self.diff.setVisible(value);self.show_report()

    def toggle_history(self,value):
        self.history_panel.setVisible(value)
        self.resize(self.width(),850 if value else 570)

    def show_phase_report(self,*_):
        index=self.phase_selector.currentIndex()
        self.phase_details.setPlainText(phase_report_text(self.phase_reports[index]) if 0<=index<len(self.phase_reports) else 'Relatórios aparecerão ao concluir as etapas.')

    def show_report(self,*_):
        index=self.selector.currentIndex()
        if index<0 or index>=len(self.reports):self.diff.clear();return
        report=self.reports[index]
        key=(json.dumps(report,sort_keys=True),self.advanced)
        if key==self.display_key:return
        self.display_key=key
        if self.advanced:self.details.setPlainText(json.dumps(report,ensure_ascii=False,indent=2))
        else:
            before=report.get('before',{});after=report.get('after',{})
            self.details.setPlainText(RESULT_LABELS.get(report.get('status'),'Em andamento')+f"\n\nAntes: {before.get('passed','—')}/{before.get('total','—')}\nCandidato: {after.get('passed','—')}/{after.get('total','—')}")
        from .evolution.state import DEFAULT_ROOT
        path=DEFAULT_ROOT/'candidates'/report.get('job_id','none')/'candidate.patch'
        if path.is_file() and not path.is_symlink() and path.stat().st_size<=100000:self.diff.setPlainText(path.read_text())
        else:self.diff.clear()
    def closeEvent(self,event):
        self.poll.stop()
        if self.worker is not None:self.closing=True;event.ignore();return
        event.accept()
