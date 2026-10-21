"""Hub de Conexão e Dispositivos Multiplataforma do MARK (PC).

Implementa a interface limpa e direta para pareamento seguro via QR Code efêmero
(AES-256-GCM + ECDH P-256), aprovação em tempo real e revogação granular.
Estilo: Minimalismo Técnico Radical / Soft Minimal Tech.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any

from PyQt6.QtCore import QObject, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..network.sync import (
    NetworkDeviceManager,
    PairingPayload,
    PairingSession,
    ResilientSyncProtocol,
)
from .theme_manager import ThemeManager

logger = logging.getLogger("mark.ui.device_hub")


class PairingWatcherThread(QThread):
    """Monitora em segundo plano a chegada do handshake de um novo dispositivo."""
    device_handshake_detected = pyqtSignal(str, str)  # device_id, name

    def __init__(self, pairing_session: PairingSession, parent: QObject | None = None):
        super().__init__(parent)
        self.pairing = pairing_session
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running and not self.pairing.is_expired():
            # Inspeciona se há dispositivo em estado 'pending' no registro
            rows = self.pairing.device_registry.list_devices()
            for dev_id, name, status in rows:
                if status == "pending":
                    self.device_handshake_detected.emit(dev_id, name)
                    return
            time.sleep(1.0)


class DeviceHubWidget(QWidget):
    """Painel do Hub de Dispositivos e Pareamento Simplificado (Fim da teia de menus)."""

    back_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.device_manager = NetworkDeviceManager()
        self.active_pairing: PairingSession | None = None
        self.watcher_thread: PairingWatcherThread | None = None
        self.countdown_seconds = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)

        self._build_ui()
        self.refresh_devices_table()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(20)

        # Cabeçalho
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title_lbl = QLabel("Dispositivos & Ecossistema")
        title_lbl.setStyleSheet("font-size: 20px; font-weight: 700; color: #EAEAEA; letter-spacing: 0.5px;")
        sub_lbl = QLabel("Conexão protegida e emparelhamento direto com smartphone e smartwatch.")
        sub_lbl.setStyleSheet("font-size: 13px; color: #8B949E;")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        header_row.addLayout(title_box)
        header_row.addStretch(1)

        self.add_device_btn = QPushButton("＋ Adicionar Dispositivo")
        self.add_device_btn.setObjectName("primaryActionButton")
        self.add_device_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_device_btn.setStyleSheet("""
            QPushButton#primaryActionButton {
                background: #238636;
                color: #FFFFFF;
                font-size: 13px;
                font-weight: 600;
                padding: 10px 20px;
                border-radius: 6px;
                border: 1px solid rgba(240, 246, 252, 0.1);
            }
            QPushButton#primaryActionButton:hover {
                background: #2EA043;
            }
            QPushButton#primaryActionButton:disabled {
                background: #21262D;
                color: #484F58;
            }
        """)
        self.add_device_btn.clicked.connect(self.start_pairing_flow)
        header_row.addWidget(self.add_device_btn)
        layout.addLayout(header_row)

        # Card de Pareamento Ativo (QR Code Efêmero) - inicialmente oculto
        self.pairing_card = QFrame()
        self.pairing_card.setObjectName("pairingCard")
        self.pairing_card.setStyleSheet("""
            QFrame#pairingCard {
                background: #161B22;
                border: 1px solid #30363D;
                border-radius: 8px;
                padding: 16px;
            }
        """)
        pairing_layout = QVBoxLayout(self.pairing_card)
        pairing_layout.setSpacing(12)

        card_header = QHBoxLayout()
        card_title = QLabel("Pareamento de Uso Único")
        card_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #58A6FF;")
        card_header.addWidget(card_title)
        card_header.addStretch(1)

        self.cancel_pairing_btn = QPushButton("Cancelar")
        self.cancel_pairing_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_pairing_btn.setStyleSheet("background: transparent; color: #F85149; border: none; font-size: 12px; font-weight: 600;")
        self.cancel_pairing_btn.clicked.connect(self.cancel_pairing_flow)
        card_header.addWidget(self.cancel_pairing_btn)
        pairing_layout.addLayout(card_header)

        # Instruções e QR
        content_row = QHBoxLayout()
        content_row.setSpacing(24)

        self.qr_label = QLabel()
        self.qr_label.setFixedSize(220, 220)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setStyleSheet("background: #0D1117; border: 1px solid #30363D; border-radius: 6px;")
        content_row.addWidget(self.qr_label)

        info_col = QVBoxLayout()
        info_col.setSpacing(8)

        self.qr_status_lbl = QLabel("Aponte a câmera do aplicativo MARK Mobile para o código ao lado.")
        self.qr_status_lbl.setStyleSheet("font-size: 13px; color: #EAEAEA;")
        self.qr_status_lbl.setWordWrap(True)
        info_col.addWidget(self.qr_status_lbl)

        self.endpoint_lbl = QLabel()
        self.endpoint_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #8B949E; background: #0D1117; padding: 8px; border-radius: 4px;")
        self.endpoint_lbl.setWordWrap(True)
        info_col.addWidget(self.endpoint_lbl)

        self.timer_lbl = QLabel("Expira em: 03:00")
        self.timer_lbl.setStyleSheet("font-size: 12px; color: #D29922; font-weight: 600;")
        info_col.addWidget(self.timer_lbl)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 180)
        self.progress_bar.setValue(180)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: #21262D;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: #58A6FF;
                border-radius: 2px;
            }
        """)
        info_col.addWidget(self.progress_bar)
        info_col.addStretch(1)

        content_row.addLayout(info_col)
        pairing_layout.addLayout(content_row)

        self.pairing_card.setVisible(False)
        layout.addWidget(self.pairing_card)

        # Seção de Dispositivos Emparelhados
        devices_header = QHBoxLayout()
        sec_title = QLabel("Dispositivos Registrados")
        sec_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #EAEAEA;")
        devices_header.addWidget(sec_title)
        devices_header.addStretch(1)

        refresh_btn = QPushButton("Atualizar lista")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet("background: transparent; color: #58A6FF; border: none; font-size: 12px;")
        refresh_btn.clicked.connect(self.refresh_devices_table)
        devices_header.addWidget(refresh_btn)
        layout.addLayout(devices_header)

        # Tabela de dispositivos
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Dispositivo", "ID", "Status", "Sessão", "Ações"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setStyleSheet("""
            QTableWidget {
                background: #0D1117;
                border: 1px solid #30363D;
                border-radius: 6px;
                gridline-color: #21262D;
                color: #C9D1D9;
            }
            QHeaderView::section {
                background: #161B22;
                color: #8B949E;
                font-size: 11px;
                font-weight: 600;
                padding: 8px 12px;
                border: none;
                border-bottom: 1px solid #30363D;
            }
        """)
        layout.addWidget(self.table)

    def start_pairing_flow(self):
        """Inicia o fluxo de pareamento gerando QR Code de uso único."""
        self.active_pairing = PairingSession(
            device_registry=self.device_manager.registry,
            validity_seconds=180,
        )
        payload = self.active_pairing.get_qr_payload()
        payload_json = payload.to_json()

        # Renderiza imagem QR via qrencode
        try:
            res = subprocess.run(
                ["qrencode", "-t", "PNG", "-s", "6", "-o", "-"],
                input=payload_json.encode("utf-8"),
                capture_output=True,
                timeout=5,
                check=True,
            )
            pix = QPixmap()
            if pix.loadFromData(res.stdout):
                self.qr_label.setPixmap(
                    pix.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
            else:
                self.qr_label.setText("Erro ao carregar imagem QR.")
        except Exception as exc:
            logger.error("Falha ao invocar qrencode: %s", exc)
            self.qr_label.setText("qrencode indisponível.")

        self.endpoint_lbl.setText(
            f"Endereço: {payload.url}\n"
            f"IP Local: {payload.ip}:{payload.port}\n"
            f"PIN PKI: {payload.pin[:16]}…\n"
            f"Chave Efêmera: {payload.ephemeral_pk[:16]}…"
        )
        self.countdown_seconds = 180
        self.progress_bar.setValue(180)
        self.timer_lbl.setText("Expira em: 03:00")
        self.timer.start(1000)

        self.pairing_card.setVisible(True)
        self.add_device_btn.setEnabled(False)

        # Inicia watcher thread para detectar handshake
        self.watcher_thread = PairingWatcherThread(self.active_pairing, self)
        self.watcher_thread.device_handshake_detected.connect(self._on_device_handshake_detected)
        self.watcher_thread.start()

    def cancel_pairing_flow(self):
        """Cancela o fluxo de pareamento ativo."""
        self.timer.stop()
        if self.watcher_thread and self.watcher_thread.isRunning():
            self.watcher_thread.stop()
            self.watcher_thread.wait(1000)
        self.active_pairing = None
        self.pairing_card.setVisible(False)
        self.add_device_btn.setEnabled(True)

    def _on_tick(self):
        self.countdown_seconds -= 1
        if self.countdown_seconds <= 0:
            self.cancel_pairing_flow()
            QMessageBox.information(
                self,
                "Pareamento Expirado",
                "A janela de 3 minutos do QR Code expirou. Abra um novo código para conectar o dispositivo.",
            )
            return

        mins = self.countdown_seconds // 60
        secs = self.countdown_seconds % 60
        self.timer_lbl.setText(f"Expira em: {mins:02d}:{secs:02d}")
        self.progress_bar.setValue(self.countdown_seconds)

    def _on_device_handshake_detected(self, device_id: str, name: str):
        """Disparado quando o dispositivo móvel lê o QR e realiza o handshake criptográfico."""
        self.cancel_pairing_flow()
        self.refresh_devices_table()

        # Prompt de aprovação explícita ("Aprovar novo dispositivo?")
        reply = QMessageBox.question(
            self,
            "Aprovar Novo Dispositivo?",
            f"O dispositivo '{name}' ({device_id[:16]}…)\n"
            "solicitou autorização para se conectar com este MARK PC.\n\n"
            "Deseja aprovar e permitir o acesso?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.Yes:
            approved = self.device_manager.approve_device(device_id)
            if approved:
                QMessageBox.information(
                    self,
                    "Dispositivo Aprovado",
                    f"✓ Dispositivo '{name}' autorizado com sucesso!\n"
                    "A sessão protegida com criptografia ponta a ponta está pronta.",
                )
            else:
                QMessageBox.warning(self, "Aviso", "Não foi possível autorizar o dispositivo.")
        else:
            self.device_manager.revoke_device_access(device_id)
            QMessageBox.information(self, "Acesso Recusado", f"O pedido de '{name}' foi recusado.")

        self.refresh_devices_table()

    def refresh_devices_table(self):
        """Atualiza a tabela com os dispositivos e opções de revogação granular."""
        devices = self.device_manager.list_devices_detailed()
        self.table.setRowCount(len(devices))

        for row, dev in enumerate(devices):
            dev_id = dev["device_id"]
            name = dev["name"]
            status = dev["status"].upper()
            session = "Ativa" if dev["session_active"] else "Inativa"

            name_item = QTableWidgetItem(name)
            id_item = QTableWidgetItem(f"{dev_id[:16]}…")
            status_item = QTableWidgetItem(status)
            session_item = QTableWidgetItem(session)

            if status == "AUTHORIZED":
                status_item.setForeground(Qt.GlobalColor.green)
            elif status == "PENDING":
                status_item.setForeground(Qt.GlobalColor.yellow)
            else:
                status_item.setForeground(Qt.GlobalColor.gray)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, id_item)
            self.table.setItem(row, 2, status_item)
            self.table.setItem(row, 3, session_item)

            # Botão de Ação Granular (Aprovar se pendente, Revogar se autorizado)
            action_btn = QPushButton("Revogar" if status != "PENDING" else "Aprovar")
            action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if status == "PENDING":
                action_btn.setStyleSheet("background: #238636; color: white; padding: 4px 8px; border-radius: 4px;")
                action_btn.clicked.connect(lambda _, did=dev_id, n=name: self._approve_pending_device(did, n))
            else:
                action_btn.setStyleSheet("background: #DA3633; color: white; padding: 4px 8px; border-radius: 4px;")
                action_btn.clicked.connect(lambda _, did=dev_id, n=name: self._revoke_device(did, n))

            btn_container = QWidget()
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(4, 2, 4, 2)
            btn_layout.addWidget(action_btn)
            self.table.setCellWidget(row, 4, btn_container)

    def _approve_pending_device(self, device_id: str, name: str):
        if self.device_manager.approve_device(device_id):
            QMessageBox.information(self, "Aprovado", f"Dispositivo '{name}' autorizado.")
            self.refresh_devices_table()

    def _revoke_device(self, device_id: str, name: str):
        reply = QMessageBox.question(
            self,
            "Revogar Dispositivo",
            f"Tem certeza que deseja revogar imediatamente o acesso de '{name}'?\n"
            "Todas as sessões ativas serão encerradas.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.device_manager.revoke_device_access(device_id)
            self.refresh_devices_table()


class DeviceHubDialog(QDialog):
    """Diálogo modal do Hub de Dispositivos para abertura direta."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Dispositivos e Conexão · MARK")
        self.resize(760, 680)
        self.setStyleSheet(ThemeManager.get_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hub_widget = DeviceHubWidget(self)
        layout.addWidget(self.hub_widget)

    def closeEvent(self, event):
        self.hub_widget.cancel_pairing_flow()
        event.accept()
