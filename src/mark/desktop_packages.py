from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMessageBox

from .desktop_helpers import _mark_icon
from .package_install import inspect as inspect_package, install as install_package


class PackagesMixin:
    def _begin_package_install(self, request):

        self.busy = True
        self.send_button.setDisabled(True)
        self.attach_button.setDisabled(True)
        self.send_button.setText("…")
        self.send_button.setIcon(QIcon())
        self.connection_label.setText("Verificando pacote e origem…")

        def ready(info):
            details = (f"Pacote: {info['package']}\nVersão: {info['version']}\nOrigem: {info['source']}"
                       + (f"\nDescrição: {info['description']}" if info.get("description") else "")
                       + (f"\nAviso: {info['warning']}" if info.get("warning") else "")
                       + ("\nPacotes na transação: " + str(info["transaction_count"])
                          + ("\nPrévia: " + ", ".join(info["transaction_preview"])
                             + (" …" if info["transaction_count"] > len(info["transaction_preview"]) else "")
                             if info.get("transaction_preview") else "")
                          if info.get("transaction_count") is not None else "")
                       + "\n\nAntes de instalar, o MARK salvará um backup privado do inventário atual de programas. "
                         "Se não conseguir salvar o backup, a instalação será cancelada. "
                         "Se autorizar, o MARK instalará somente este pacote. Dependências serão tratadas pelo gerenciador. "
                         "No caso do pacman, o Polkit também pedirá autenticação do sistema."
                       + "\n\nO MARK não usa AUR, scripts arbitrários nem instruções vindas de páginas.")
            box = QMessageBox(self)
            box.setWindowTitle("Autorizar instalação")
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText("O MARK encontrou este pacote:")
            box.setInformativeText(details)
            box.setIcon(QMessageBox.Icon.Question)
            approve = box.addButton("Autorizar instalação", QMessageBox.ButtonRole.AcceptRole)
            cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() != approve:
                self._finish_package_install("Instalação cancelada; nada foi instalado.")
                return
            self.connection_label.setText("Aguardando o gerenciador de pacotes…")
            self._task(lambda: install_package(request), self._finish_package_install,
                       lambda error: self._finish_package_install("Instalação não concluída: " + error))

        self._task(lambda: inspect_package(request), ready,
                   lambda error: self._finish_package_install("Não iniciei a instalação: " + error))

    def _finish_package_install(self, reply):
        self.busy = False
        self.send_button.setDisabled(False)
        self.attach_button.setDisabled(False)
        self.remove_attachment_button.setEnabled(self.attachment_list.currentRow() >= 0)
        self.send_button.setText("")
        self.send_button.setIcon(_mark_icon("send"))
        self._append_bubble(reply, "assistant")
        self.last_assistant_reply = reply
        self.connection_label.setText("Conectado ao MARK · memória compartilhada")
        self.input.setFocus()
