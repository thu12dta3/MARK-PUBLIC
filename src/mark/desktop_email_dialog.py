from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout


class EmailComposerDialog(QDialog):

    def __init__(self, body: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preparar email no Tuta")
        self.setMinimumSize(560, 520)
        layout = QVBoxLayout(self)
        hint = QLabel("Confira destinatário, assunto e texto. O Tuta abrirá o rascunho para você revisar e enviar.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(QLabel("Para"))
        self.recipient = QLineEdit()
        self.recipient.setPlaceholderText("pessoa@exemplo.com")
        layout.addWidget(self.recipient)
        layout.addWidget(QLabel("Assunto"))
        self.subject = QLineEdit()
        layout.addWidget(self.subject)
        layout.addWidget(QLabel("Mensagem"))
        self.body = QPlainTextEdit()
        self.body.setPlainText(body)
        self.body.setPlaceholderText("Escreva a mensagem ou use a resposta mais recente do MARK.")
        layout.addWidget(self.body, 1)
        self.open_button = QPushButton("Abrir rascunho no Tuta")
        self.open_button.clicked.connect(self.open_draft)
        layout.addWidget(self.open_button)

    def open_draft(self):
        from . import desktop as desktop_module
        recipient = self.recipient.text().strip()
        if not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", recipient):
            desktop_module.QMessageBox.warning(self, "Email", "Informe um único endereço de email válido.")
            return
        query = urlencode({"subject": self.subject.text().strip(), "body": self.body.toPlainText()},
                          quote_via=quote)
        url = QUrl("mailto:" + quote(recipient, safe="@._+-") + "?" + query)
        if not desktop_module.QDesktopServices.openUrl(url):
            desktop_module.QMessageBox.warning(
                self, "Email", "Não há um aplicativo padrão configurado para abrir rascunhos mailto. "
                "Defina o Tuta Mail como manipulador de links mailto no Linux e tente novamente.",
            )
            return
        self.accept()
