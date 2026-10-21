"""Diálogo de chamada de voz do MARK Desktop."""

from __future__ import annotations

from array import array
from collections import deque

from PyQt6.QtCore import Qt, QTimer, QUrl, QBuffer, QIODevice
from PyQt6.QtMultimedia import QAudioFormat, QAudioOutput, QAudioSource, QMediaDevices, QMediaPlayer
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .desktop_dialogs import WorkerDialog
from .desktop_task import Task
from .desktop_helpers import _request, _voice_request


class CallDialog(WorkerDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chamada com MARK")
        self.setMinimumSize(390, 330)
        self.active = False
        self.closing_when_idle = False
        self.worker = None
        self.source = None
        self.microphone = None
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.errorOccurred.connect(self._playback_error)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self._refresh_audio_outputs)
        self.audio_buffer = None
        self.sleep_after_playback = False
        self.pending = bytearray()
        self.pre_frames = deque(maxlen=3)
        self.samples = bytearray()
        self.heard = False
        self.silent_frames = 0
        self.elapsed_frames = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        title = QLabel("◉  MARK")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #bdf5f6; font-size: 29px; font-weight: bold;")
        layout.addWidget(title)
        self.state = QLabel("Pronto para conversar")
        self.state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state.setWordWrap(True)
        self.state.setStyleSheet("font-size: 19px; color: #e6edf3;")
        layout.addWidget(self.state, 1)
        self.detail = QLabel("Fale normalmente. O MARK responde pelo fone ou alto-falante do PC.")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: #91a4b4;")
        layout.addWidget(self.detail)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Saída de áudio"))
        self.output_selector = QComboBox()
        self.output_selector.setToolTip("Escolha onde o MARK reproduz a resposta falada")
        self.output_selector.currentIndexChanged.connect(self._select_audio_output)
        output_row.addWidget(self.output_selector, 1)
        layout.addLayout(output_row)
        self.toggle = QPushButton("Iniciar chamada")
        self.toggle.clicked.connect(self.toggle_call)
        layout.addWidget(self.toggle)
        self.interrupt = QPushButton("Interromper resposta e falar")
        self.interrupt.clicked.connect(self._interrupt_playback)
        self.interrupt.hide()
        layout.addWidget(self.interrupt)
        self.setStyleSheet("QDialog { background: #0c1219; } QPushButton { background: #143943; "
                           "color: #bdf5f6; border: 1px solid #20525a; border-radius: 13px; padding: 12px; }")
        self._refresh_audio_outputs()

    def _refresh_audio_outputs(self):
        outputs = QMediaDevices.audioOutputs()
        current_id = bytes(self.audio_output.device().id())
        self.output_selector.blockSignals(True)
        self.output_selector.clear()
        for device in outputs:
            self.output_selector.addItem(device.description(), bytes(device.id()))
        self.output_selector.setEnabled(bool(outputs))
        index = self.output_selector.findData(current_id)
        if index < 0 and outputs:
            default_id = bytes(QMediaDevices.defaultAudioOutput().id())
            index = self.output_selector.findData(default_id)
        if index < 0 and outputs:
            index = 0
        if index >= 0:
            self.output_selector.setCurrentIndex(index)
        self.output_selector.blockSignals(False)
        if index >= 0:
            self._select_audio_output(index)
        else:
            self.output_selector.addItem("Nenhuma saída disponível")

    def _select_audio_output(self, index):
        device_id = self.output_selector.itemData(index) if index >= 0 else None
        if device_id is None:
            return
        device = next((item for item in QMediaDevices.audioOutputs()
                       if bytes(item.id()) == bytes(device_id)), None)
        if device is None:
            self._refresh_audio_outputs()
            return
        self.audio_output.setDevice(device)
        if self.active:
            self.detail.setText(f"Saída: {device.description()}. A chamada continua ativa.")

    def _playback_error(self, error, message):
        if error == QMediaPlayer.Error.NoError and not message:
            return
        self.interrupt.hide()
        self.audio_buffer = None
        self.state.setText("Falha ao reproduzir a resposta")
        detail = message.strip() if message and message.strip() else "O dispositivo de saída recusou o áudio."
        self.detail.setText(f"{detail} Escolha outra saída de áudio e tente falar novamente.")
        if self.active:
            QTimer.singleShot(1200, self._listen)

    def toggle_call(self):
        if self.active:
            self._stop_call()
        else:
            self.active = True
            self.toggle.setText("Encerrar chamada")
            self.state.setText("Conferindo conexão com o MARK…")
            self.detail.setText("O microfone só será ativado depois que o servidor responder.")
            self.worker = Task(lambda: _request("/health", timeout=8), self)
            self.worker.completed.connect(self._connection_ready)
            self.worker.failed.connect(self._connection_failed)
            self.worker.start()

    def _finish_connection_check(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        if self.closing_when_idle:
            self.close()
            return False
        return self.active

    def _connection_ready(self, result):
        if not self._finish_connection_check():
            return
        status, _ = result
        if status == 200:
            self._listen()
        else:
            self._connection_failed(f"O servidor respondeu HTTP {status}.")

    def _connection_failed(self, message):
        if self.worker is not None:
            worker, self.worker = self.worker, None
            worker.deleteLater()
        if self.closing_when_idle:
            self.close()
            return
        self.active = False
        self.toggle.setText("Tentar chamada novamente")
        self.state.setText("Chat indisponível")
        self.detail.setText("Verifique a conexão com o servidor MARK e tente novamente. " + str(message))

    def _listen(self):
        if not self.active or self.closing_when_idle:
            return
        device = QMediaDevices.defaultAudioInput()
        audio_format = QAudioFormat()
        audio_format.setSampleRate(16_000)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isNull() or not device.isFormatSupported(audio_format):
            self._stop_call("Microfone indisponível ou sem suporte a 16 kHz mono.")
            return
        self.pending.clear()
        self.pre_frames.clear()
        self.samples.clear()
        self.heard = False
        self.silent_frames = 0
        self.elapsed_frames = 0
        self.source = QAudioSource(device, audio_format, self)
        self.microphone = self.source.start()
        if self.microphone is None:
            self._stop_call("Não consegui abrir o microfone do PC.")
            return
        self.microphone.readyRead.connect(self._audio_ready)
        self.state.setText("Ouvindo…")
        self.detail.setText("Fale naturalmente; o MARK escuta até você fazer uma pausa.")

    def _audio_ready(self):
        if not self.active or self.microphone is None or self.worker is not None:
            return
        self.pending.extend(bytes(self.microphone.readAll()))
        while len(self.pending) >= 3200:
            frame = bytes(self.pending[:3200])
            del self.pending[:3200]
            values = array("h")
            values.frombytes(frame)
            energy = sum(abs(sample) for sample in values) // len(values)
            voice = energy > 480
            self.elapsed_frames += 1
            if not self.heard:
                self.pre_frames.append(frame)
                if voice:
                    self.heard = True
                    for previous in self.pre_frames:
                        self.samples.extend(previous)
                    self.pre_frames.clear()
            else:
                self.samples.extend(frame)
                self.silent_frames = 0 if voice else self.silent_frames + 1
                if (self.silent_frames >= 5 and len(self.samples) >= 5760) or len(self.samples) >= 480_000:
                    self._send_turn(bytes(self.samples))
                    return
            if self.elapsed_frames >= 100 and not self.heard:
                self.elapsed_frames = 0
                self.pre_frames.clear()

    def _stop_microphone(self):
        if self.source is not None:
            self.source.stop()
            self.source.deleteLater()
            self.source = None
            self.microphone = None

    def _send_turn(self, pcm: bytes):
        self._stop_microphone()
        self.state.setText("MARK está pensando…")
        self.detail.setText("Aguarde a resposta da conversa.")
        self.worker = Task(lambda: _voice_request(pcm), self)
        self.worker.completed.connect(self._received)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _received(self, result):
        self.worker.deleteLater()
        self.worker = None
        if self.closing_when_idle:
            self.close()
            return
        if not self.active:
            return
        wav, self.sleep_after_playback = result
        self.audio_buffer = QBuffer(self)
        self.audio_buffer.setData(wav)
        self.audio_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self.audio_buffer, QUrl("file:///mark-answer.wav"))
        self.state.setText("MARK está falando…")
        self.detail.setText("Você pode interromper para voltar a falar.")
        self.interrupt.show()
        self.player.play()

    def _failed(self, message):
        self.worker.deleteLater()
        self.worker = None
        if self.closing_when_idle:
            self.close()
            return
        self.state.setText("Não consegui concluir a fala")
        self.detail.setText(message)
        if self.active:
            QTimer.singleShot(1800, self._listen)

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.active:
            self.interrupt.hide()
            self.audio_buffer = None
            if self.sleep_after_playback:
                self._stop_call("MARK descansando. Chamada encerrada.")
            else:
                self._listen()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia and self.active:
            self._playback_error(self.player.error(), self.player.errorString() or "O formato do áudio não foi reconhecido.")

    def _interrupt_playback(self):
        self.player.stop()
        self.interrupt.hide()
        self.audio_buffer = None
        self._listen()

    def _stop_call(self, message="Chamada encerrada"):
        self.active = False
        self._stop_microphone()
        self.player.stop()
        self.interrupt.hide()
        self.audio_buffer = None
        self.toggle.setText("Iniciar chamada")
        self.state.setText(message)

    def closeEvent(self, event):
        self._stop_call()
        if self.worker is not None:
            self.closing_when_idle = True
            self.state.setText("Encerrando a chamada…")
            event.ignore()
            return
        event.accept()
