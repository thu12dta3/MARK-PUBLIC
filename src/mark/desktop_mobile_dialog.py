"""Diálogo de aparelhos móveis e pareamento seguro do MARK Desktop (Hub Simplificado).

Implementa fluxo direto de QR Code de uso único, eliminando teia de menus,
com suporte a payload criptográfico estruturado (IP/Chave/Token) e aprovação em 1 clique.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .desktop_dialogs import WorkerDialog
from .desktop_task import Task
from .desktop_helpers import _mobile_pairing
from .network.sync import PairingSession, get_best_local_endpoint


class MobileDevicesDialog(WorkerDialog):
    """Hub de pareamento limpo e direto no PC."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dispositivos Móveis e Segurança · MARK")
        self.resize(620, 680)
        self.worker = None
        self.pair_expiry_timer = QTimer(self)
        self.pair_expiry_timer.setSingleShot(True)
        self.pair_expiry_timer.timeout.connect(self._expire_pairing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title_label = QLabel("Conexão Protegida do MARK (PC Hub)")
        title_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #EAEAEA;")
        layout.addWidget(title_label)

        self.status = QLabel("Pronto para emparelhamento. Clique abaixo para gerar o QR Code de uso único.")
        self.status.setStyleSheet("font-size: 12px; color: #8B949E;")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # Botão único e proeminente para Adicionar Dispositivo
        self.pair_button = QPushButton("＋ Adicionar Dispositivo Móvel")
        self.pair_button.setObjectName("addDeviceButton")
        self.pair_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pair_button.setStyleSheet("""
            QPushButton#addDeviceButton {
                background: #238636;
                color: #FFFFFF;
                font-size: 13px;
                font-weight: 600;
                padding: 10px 16px;
                border-radius: 6px;
                border: 1px solid rgba(240, 246, 252, 0.1);
            }
            QPushButton#addDeviceButton:hover {
                background: #2EA043;
            }
            QPushButton#addDeviceButton:disabled {
                background: #21262D;
                color: #484F58;
            }
        """)
        self.pair_button.clicked.connect(self.open_pairing)
        layout.addWidget(self.pair_button)

        # Exibição do QR Code
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setMinimumHeight(220)
        self.qr.setStyleSheet("background: #0D1117; border: 1px solid #30363D; border-radius: 6px;")
        layout.addWidget(self.qr)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(90)
        self.details.setStyleSheet("font-family: monospace; font-size: 11px; color: #8B949E; background: #0D1117;")
        layout.addWidget(self.details)

        layout.addWidget(QLabel("Aparelhos Registrados no PC:"))
        self.devices_status = QLabel()
        self.devices_status.setStyleSheet("font-size: 12px; color: #8B949E;")
        self.devices_status.setWordWrap(True)
        layout.addWidget(self.devices_status)

        self.devices = QListWidget()
        self.devices.setStyleSheet("background: #0D1117; border: 1px solid #30363D; border-radius: 6px; padding: 6px;")
        layout.addWidget(self.devices)

        controls = QHBoxLayout()
        for label, callback, style in (
            ("Atualizar Lista", self.refresh_devices, "background: #21262D; color: #C9D1D9;"),
            ("Aprovar Novo", self.approve, "background: #238636; color: #FFFFFF; font-weight: 600;"),
            ("Revogar Acesso", self.revoke, "background: #DA3633; color: #FFFFFF; font-weight: 600;"),
        ):
            button = QPushButton(label)
            button.setStyleSheet(f"{style} padding: 8px 14px; border-radius: 6px; border: 1px solid rgba(240, 246, 252, 0.1);")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)

        self.refresh_devices()
        self.devices_timer = QTimer(self)
        self.devices_timer.timeout.connect(self.refresh_devices)
        self.devices_timer.start(3000)

    def refresh_devices(self):
        from .device_auth import DeviceRegistry
        selected = self.devices.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole)[0] if selected else None
        registry = DeviceRegistry()
        try:
            rows = registry.list_devices()
            lockdown = registry.lockdown()
        finally:
            registry.close()
        self.devices.clear()
        for device_id, name, status in rows:
            icon_status = "●" if status == "authorized" else ("○" if status == "pending" else "✕")
            item = QListWidgetItem(f"{icon_status} [{status.upper()}] · {name} · ({device_id[:16]}…)")
            item.setData(Qt.ItemDataRole.UserRole, (device_id, status))
            self.devices.addItem(item)
            if device_id == selected_id:
                self.devices.setCurrentItem(item)
        self.devices_status.setText(
            "MARK em lockdown: acesso móvel bloqueado." if lockdown else
            f"{len(rows)} aparelho(s) registrados no ecossistema."
        )

    def open_pairing(self):
        if self.worker is not None and self.worker.isRunning():
            return
        self.pair_expiry_timer.stop()
        self.qr.clear()
        self.details.clear()
        self.status.setText("Gerando credenciais efêmeras (ECDH P-256 + desafio de uso único)…")
        self.pair_button.setDisabled(True)

        def generate_ephemeral_payload():
            from .device_auth import DeviceRegistry
            registry = DeviceRegistry()
            try:
                session = PairingSession(device_registry=registry, validity_seconds=180)
                payload = session.get_qr_payload()
                return {
                    "url": payload.url,
                    "ip": payload.ip,
                    "port": payload.port,
                    "code": payload.code,
                    "pin": payload.pin,
                    "challenge": payload.challenge,
                    "ephemeral_pk": payload.ephemeral_pk,
                    "expires_at": payload.expires_at,
                }
            finally:
                registry.close()

        self.worker = Task(generate_ephemeral_payload, self)
        self.worker.completed.connect(self._show_pairing)
        self.worker.failed.connect(lambda message: self.status.setText("Pareamento não aberto: " + message))
        self.worker.finished.connect(lambda: self.pair_button.setEnabled(True))
        self.worker.start()

    def _show_pairing(self, data):
        from . import desktop as desktop_module
        payload = json.dumps(data, separators=(",", ":"))
        self.details.setPlainText(
            f"Endereço: {data.get('url')}\n"
            f"IP Local: {data.get('ip')}:{data.get('port')}\n"
            f"Código efêmero: {data.get('code')}\n"
            f"PIN PKI: {data.get('pin', '')[:16]}…"
        )
        result = desktop_module.subprocess.run(
            ["qrencode", "-t", "PNG", "-o", "-"],
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=5,
            check=False,
        )
        image = QPixmap()
        if result.returncode == 0 and image.loadFromData(result.stdout):
            self.qr.setPixmap(image.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
            self.status.setText("Aponte a câmera do MARK Mobile para o QR Code efêmero.")
        else:
            self.qr.clear()
            self.status.setText("Não consegui gerar o QR. Informe os dados acima manualmente no celular.")
        self.refresh_devices()
        self.pair_expiry_timer.start(180_000)

    def _expire_pairing(self):
        self.qr.clear()
        self.details.clear()
        self.status.setText("O QR Code efêmero expirou. Clique em '＋ Adicionar Dispositivo Móvel' para gerar outro.")

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("Aguarde a verificação da conexão antes de fechar esta janela.")
            event.ignore()
            return
        self.devices_timer.stop()
        self.pair_expiry_timer.stop()
        self.qr.clear()
        self.details.clear()
        event.accept()

    def _selected(self):
        item = self.devices.currentItem()
        if item is None:
            QMessageBox.information(self, "MARK Mobile", "Selecione um aparelho na lista.")
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def approve(self):
        selected = self._selected()
        if selected is None:
            return
        device_id, status = selected
        if status != "pending":
            QMessageBox.information(self, "MARK Mobile", "Somente um aparelho pendente pode ser aprovado.")
            return
        if QMessageBox.question(self, "Aprovar aparelho", f"Autorizar este aparelho?\n{device_id}") != QMessageBox.StandardButton.Yes:
            return
        from .device_auth import DeviceRegistry
        registry = DeviceRegistry()
        try:
            approved = registry.update_device(device_id, "authorized")
        finally:
            registry.close()
        self.refresh_devices()
        self.status.setText("Aparelho aprovado; sessão protegida ativa." if approved else "O aparelho não estava mais pendente.")

    def revoke(self):
        selected = self._selected()
        if selected is None:
            return
        device_id, status = selected
        if status == "revoked":
            return
        if QMessageBox.question(self, "Revogar aparelho", f"Cortar imediatamente o acesso deste aparelho?\n{device_id}") != QMessageBox.StandardButton.Yes:
            return
        from .network.sync import NetworkDeviceManager
        mgr = NetworkDeviceManager()
        try:
            mgr.revoke_device_access(device_id)
        finally:
            mgr.close()
        self.refresh_devices()
        self.status.setText(f"Aparelho {device_id[:16]}… revogado.")
