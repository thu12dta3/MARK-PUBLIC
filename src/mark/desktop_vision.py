
from __future__ import annotations

import base64
import copy
import threading
import time
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QPlainTextEdit
from .config import load_config
from .vision_live import LatestCamera
from .vision_quality import inspect_frame, scene_difference, visual_instructions
from .vision_focus import crop_focus, focus_instructions, validate_focus
from .desktop_vision_preview import VisionPreview
from .desktop_dialogs import WorkerDialog
from .brain_model import _ollama
from .brain_prompt import SYSTEM
from .model_state import ModelSession


class VisionWorker(QThread):
    status = pyqtSignal(str)
    result = pyqtSignal(str, float, float, int)
    failed = pyqtSignal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
        self.cfg['ollama'].update(model=cfg['models']['call'], think=False, num_predict=220,
                                  timeout=min(60, cfg['ollama'].get('timeout',60)))
        self.camera = LatestCamera(self.cfg)
        self.stop_requested = threading.Event()
        self._lock = threading.Lock()
        self._prompt = 'Descreva os objetos principais e sua posição. Leia somente o texto nítido.'
        self._continuous = True
        self._force = True
        self._focus = None
        self._revision = 0

    def request(self, prompt: str, continuous: bool, force=False, focus=None):
        focus = validate_focus(focus)
        prompt = prompt[:1000].strip() or 'Descreva os objetos principais e sua posição.'
        with self._lock:
            if force or prompt != self._prompt or focus != self._focus:
                self._revision += 1
            self._prompt = prompt
            self._focus = focus
            self._continuous = continuous
            self._force |= force
            return self._revision

    def stop(self):
        self.stop_requested.set()

    def run(self):
        session = ModelSession(self.cfg)
        signature = b''
        last_analysis = 0.0
        try:
            if self.stop_requested.is_set(): return
            self.camera.start()
            self.camera.wait_frame(min(20, self.cfg['camera'].get('timeout',20)))
            self.status.emit('Câmera ativa · preparando Qwen rápido…')
            session.wake()
            while not self.stop_requested.is_set():
                frame = self.camera.latest()
                now = time.monotonic()
                with self._lock:
                    force, continuous, prompt = self._force, self._continuous, self._prompt
                    focus, revision = self._focus, self._revision
                    self._force = False
                ready = continuous and now - last_analysis >= 1.5
                if force or ready:
                    focused = crop_focus(frame.jpeg, focus) if focus else None
                    current_signature = inspect_frame(focused).signature if focused else frame.quality.signature
                    changed = scene_difference(signature, current_signature) >= 3.0
                    due = ready and (changed or now-last_analysis >= 10)
                    if force or due:
                        self.status.emit('Analisando um quadro recente · a prévia continua ao vivo…')
                        begin = time.monotonic()
                        images = [base64.b64encode(frame.jpeg).decode('ascii')]
                        if focused:
                            images.append(base64.b64encode(focused).decode('ascii'))
                        reply = _ollama(prompt, self.cfg, SYSTEM + visual_instructions() +
                            focus_instructions(focus) + ' Responda em português, em até três frases concretas.',
                            images=images)
                        if self.stop_requested.is_set(): break
                        self.camera.latest()
                        with self._lock:
                            superseded = revision != self._revision
                        if superseded:

                            continue
                        self.result.emit(reply, time.monotonic()-begin, frame.captured_at, revision)
                        signature = current_signature
                        last_analysis = time.monotonic()
                        self.status.emit('Visão ativa · aguardando mudança da cena…' if continuous else 'Visão ativa · análise automática pausada')
                self.stop_requested.wait(.2)
        except Exception as exc:

            if not self.stop_requested.is_set():
                if isinstance(exc, RuntimeError) and str(exc).startswith(('Sem quadro', 'A câmera', 'O fluxo', 'Não consegui abrir')):
                    self.failed.emit(str(exc))
                else:
                    self.failed.emit('A visão foi interrompida. Confira a câmera e o Qwen local e inicie novamente.')
        finally:
            self.camera.close()
            if session.awake: session.sleep()


class VisionDialog(WorkerDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('MARK · Visão ao vivo')
        self.resize(860, 700)
        self.worker = None
        self._closing = False
        self._capture_time = None
        self._preview_sequence = -1
        self._request_revision = 0
        self.cfg = load_config()
        layout = QVBoxLayout(self)
        title = QLabel('VISÃO AO VIVO')
        title.setStyleSheet('color: #65e8d8; font-size: 20px; font-weight: 600;')
        layout.addWidget(title)
        layout.addWidget(QLabel('Câmera local · Qwen rápido · sem microfone · sem gravar vídeo'))
        controls = QHBoxLayout()
        self.source = QComboBox()
        self.source.setObjectName("modelSelector")
        self.source.addItem('Câmera USB', 'usb')
        self.source.addItem('Câmera de rede configurada', 'rtsp')
        if self.cfg['camera'].get('source') == 'rtsp': self.source.setCurrentIndex(1)
        self.start_button = QPushButton('Iniciar visão')
        self.start_button.clicked.connect(self.toggle)
        self.continuous = QCheckBox('Análise contínua')
        self.continuous.setChecked(True)
        self.continuous.toggled.connect(self.update_request)
        controls.addWidget(self.source)
        controls.addWidget(self.continuous)
        controls.addWidget(self.start_button)
        layout.addLayout(controls)
        self.preview = VisionPreview()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 240)
        self.preview.setStyleSheet('background: #07151a; border: 1px solid #28505b; border-radius: 12px;')
        self.source.currentIndexChanged.connect(lambda _: self.preview.clear_focus())
        self.preview.focusChanged.connect(self.focus_changed)
        self.preview.selectionFailed.connect(self.state_message)
        layout.addWidget(self.preview, 1)
        focus_controls = QHBoxLayout()
        self.focus_hint = QLabel('Arraste sobre a imagem para marcar um detalhe.')
        self.focus_hint.setWordWrap(True)
        self.reset_focus = QPushButton('Cena inteira')
        self.reset_focus.setEnabled(False)
        self.reset_focus.clicked.connect(self.preview.clear_focus)
        focus_controls.addWidget(self.focus_hint, 1)
        focus_controls.addWidget(self.reset_focus)
        layout.addLayout(focus_controls)
        self.quality = QLabel('')
        self.quality.setWordWrap(True)
        layout.addWidget(self.quality)
        question = QHBoxLayout()
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText('O que observar? Ex.: leia a etiqueta ou conte os objetos')
        self.prompt.returnPressed.connect(self.analyze)
        self.analyze_button = QPushButton('Analisar agora')
        self.analyze_button.setEnabled(False)
        self.analyze_button.clicked.connect(self.analyze)
        question.addWidget(self.prompt, 1)
        question.addWidget(self.analyze_button)
        layout.addLayout(question)
        self.observation = QPlainTextEdit()
        self.observation.setReadOnly(True)
        self.observation.setPlaceholderText('A observação do MARK aparecerá aqui.')
        self.observation.setMaximumHeight(130)
        layout.addWidget(self.observation)
        self.age = QLabel('A análise usa um quadro por vez; a velocidade depende do PC.')
        self.state = QLabel('Câmera desligada')
        layout.addWidget(self.age)
        layout.addWidget(self.state)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refresh_preview)
        self.timer.start()

    def toggle(self):
        if self.worker is not None:
            self.stop()
            return
        cfg = copy.deepcopy(self.cfg)
        cfg['camera']['source'] = self.source.currentData()
        self.worker = VisionWorker(cfg, self)
        self._request_revision = self.worker.request(self.prompt.text(), self.continuous.isChecked(), True, self.preview.focus)
        self.worker.status.connect(self.state.setText)
        self.worker.result.connect(self.show_result)
        self.worker.failed.connect(self.state.setText)
        self.worker.finished.connect(self.stopped)
        self.source.setEnabled(False)
        self.analyze_button.setEnabled(True)
        self.start_button.setText('Parar visão')
        self.state.setText('Abrindo câmera…')
        self.observation.clear()
        self._capture_time = None
        self.worker.start()

    def state_message(self, text):
        self.state.setText(text)

    def focus_changed(self, focus):
        self.reset_focus.setEnabled(focus is not None)
        self.focus_hint.setText('Área marcada: o MARK observa o recorte junto da cena geral.' if focus else
                                'Arraste sobre a imagem para marcar um detalhe.')
        self.observation.clear()
        self._capture_time = None
        if self.worker and not self.worker.stop_requested.is_set():
            self._request_revision = self.worker.request(self.prompt.text(), self.continuous.isChecked(), True, focus)

    def update_request(self, *_):
        if self.worker:
            self._request_revision = self.worker.request(self.prompt.text(), self.continuous.isChecked(), focus=self.preview.focus)

    def analyze(self):
        if self.worker:
            self._request_revision = self.worker.request(self.prompt.text(), self.continuous.isChecked(), True, self.preview.focus)
            self.observation.clear()
            self._capture_time = None

    def stop(self):
        if self.worker:
            self.worker.stop()

            self.worker.camera.stop()
            self.start_button.setEnabled(False)
            self.analyze_button.setEnabled(False)
            self.state.setText('Câmera desligada · finalizando a análise em andamento…')
            self.preview.clear()
            self.preview.setText('Câmera desligada')
            self.quality.clear()
            self._capture_time = None

    def show_result(self, text, elapsed, captured, revision):
        if (not self.worker or self.worker.stop_requested.is_set() or
                revision != self._request_revision): return
        self.observation.setPlainText(text)
        self._capture_time = captured
        self.state.setText(f'Análise concluída em {elapsed:.1f} s')

    def refresh_preview(self):
        if not self.worker or self.worker.stop_requested.is_set(): return
        try:
            frame = self.worker.camera.latest()
        except RuntimeError:

            self.preview.clear()
            self.preview.setText('Aguardando um quadro recente…')
            if self._capture_time is not None:
                self.age.setText('Conexão da câmera interrompida · a observação anterior pode estar desatualizada')
            return
        if frame.sequence != self._preview_sequence:
            self._preview_sequence = frame.sequence
            self.preview.set_frame(frame.jpeg)
            q = frame.quality
            self.quality.setText(f'{q.width} × {q.height} · ' + (' '.join(q.warnings) or 'Quadro disponível'))
        if self._capture_time is not None:
            self.age.setText(f'Observação baseada no quadro recebido há {time.monotonic()-self._capture_time:.1f} s')

    def stopped(self):
        self.worker.deleteLater()
        self.worker = None
        self.source.setEnabled(True)
        self.analyze_button.setEnabled(False)
        self.start_button.setEnabled(True)
        self.start_button.setText('Iniciar visão')
        self._preview_sequence = -1
        self.preview.clear()
        self.preview.setText('Câmera desligada')
        self.quality.clear()
        self.age.setText('Visão encerrada. Os quadros foram descartados da memória.')
        if self._closing: self.close()

    def closeEvent(self, event):
        if self.worker is not None:
            self._closing = True
            self.stop()
            event.ignore()
            return
        self.timer.stop()
        event.accept()
