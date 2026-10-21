
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from .config import load_config
from .plugins import PLUGINS, enabled, save_enabled


class PluginsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugins do MARK")
        self.setMinimumWidth(520)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(13)
        title = QLabel("Plugins")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(title)
        intro = QLabel("Ative as integrações que o MARK pode usar. Cada uma mostra as permissões que recebe.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.switches = {}
        cfg = load_config()
        for spec in PLUGINS:
            card = QWidget()
            card.setStyleSheet("QWidget { background: #17191a; border: 1px solid #343839; border-radius: 10px; }")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            row = QHBoxLayout()
            name = QLabel(spec["name"])
            name.setStyleSheet("font-size: 15px; font-weight: 650; border: 0; background: transparent;")
            row.addWidget(name)
            row.addStretch(1)
            toggle = QCheckBox("Ativo")
            toggle.setChecked(enabled(spec["id"], cfg))
            toggle.setAccessibleName(f"Ativar plugin {spec['name']}")
            row.addWidget(toggle)
            card_layout.addLayout(row)
            description = QLabel(spec["description"])
            description.setWordWrap(True)
            description.setStyleSheet("border: 0; background: transparent;")
            card_layout.addWidget(description)
            permissions = QLabel("Permissões: " + " · ".join(spec["permissions"]))
            permissions.setWordWrap(True)
            permissions.setStyleSheet("color: #a8b0b2; font-size: 11px; border: 0; background: transparent;")
            card_layout.addWidget(permissions)
            source = QLabel(spec["source"])
            source.setStyleSheet("color: #899295; font-size: 11px; border: 0; background: transparent;")
            card_layout.addWidget(source)
            layout.addWidget(card)
            self.switches[spec["id"]] = toggle
            toggle.toggled.connect(lambda value, plugin_id=spec["id"]: self._save(plugin_id, value))
        note = QLabel("Plugins externos ainda não são executados. O MARK só carrega integrações nativas e revisadas.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #a8b0b2; font-size: 11px;")
        layout.addWidget(note)
        close = QPushButton("Concluído")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def _save(self, plugin_id: str, value: bool):
        try:
            save_enabled(plugin_id, value)
        except ValueError as exc:
            toggle = self.switches[plugin_id]
            toggle.blockSignals(True)
            toggle.setChecked(not value)
            toggle.blockSignals(False)
            QMessageBox.warning(self, "Plugins", str(exc))
