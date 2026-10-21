
from __future__ import annotations

from PyQt6.QtCore import QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from .provider_settings import (
    PROVIDERS, ProviderError, delete_api_key, list_models,
    key_source, provider_settings, set_provider_settings, store_api_key,
)


class _CatalogWorker(QThread):
    ready = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, provider: str, parent=None):
        super().__init__(parent)
        self.provider = provider

    def run(self):
        try:
            self.ready.emit(self.provider, list_models(self.provider))
        except Exception as exc:
            self.failed.emit(self.provider, str(exc)[:300])


class ProviderDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Modelos e APIs")
        self.setMinimumSize(760, 660)
        self._workers: dict[str, _CatalogWorker] = {}
        self._controls = {}
        root = QVBoxLayout(self)
        title = QLabel("Modelos e APIs")
        title.setObjectName("welcomeTitle")
        root.addWidget(title)
        note = QLabel(
            "O Qwen local continua sendo o padrão. Provedores externos ficam desligados e só podem ser usados "
            "pelo comando explícito @free, com perguntas públicas isoladas; memória, histórico, anexos e imagens não são enviados. "
            "O MARK não consulta faturamento nem garante custo zero: a confirmação de conta/modelo sem cobrança é manual. "
            "Consultar a lista de modelos não testa geração nem confirma acesso para gerar."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        for name, spec in PROVIDERS.items():
            group = QGroupBox(spec["label"])
            form = QFormLayout(group)
            key = QLineEdit()
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText("Nova chave; fica vazia depois de salvar")
            status = QLabel()
            status.setWordWrap(True)
            model = QComboBox()
            model.setEditable(False)
            allow = QCheckBox("Permitir somente com @free e conta/modelo confirmado sem cobrança")
            allow.setChecked(False)
            active = QCheckBox("Habilitar este provedor para perguntas públicas explícitas")
            current = provider_settings(name)
            active.setChecked(current["enabled"])
            allow.setChecked(current["free_only_confirmed"])
            if current["model"]:
                model.addItem(current["model"], current["model"])
            model.setCurrentIndex(0 if model.count() else -1)
            row = QHBoxLayout()
            save = QPushButton("Salvar chave")
            test = QPushButton("Consultar modelos")
            remove = QPushButton("Remover chave")
            for button in (save, test, remove):
                row.addWidget(button)
            form.addRow("Chave de API", key)
            form.addRow("Estado", status)
            form.addRow("Modelo permitido", model)
            form.addRow("Uso externo", active)
            form.addRow(allow)
            form.addRow(row)
            root.addWidget(group)
            self._controls[name] = {
                "key": key, "status": status, "model": model,
                "allow": allow, "active": active, "save": save,
                "test": test, "remove": remove,
            }
            save.clicked.connect(lambda _checked=False, provider=name: self.save_key(provider))
            test.clicked.connect(lambda _checked=False, provider=name: self.query_models(provider))
            remove.clicked.connect(lambda _checked=False, provider=name: self.remove_key(provider))
            model.currentIndexChanged.connect(lambda _index, provider=name: self.apply_settings(provider))
            active.toggled.connect(lambda _checked, provider=name: self.apply_settings(provider))
            allow.toggled.connect(lambda _checked, provider=name: self.apply_settings(provider))
            self._refresh_key_state(name)

        close = QPushButton("Fechar")
        close.clicked.connect(self.close)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def _refresh_key_state(self, name: str):
        control = self._controls[name]
        source = key_source(name)
        control["status"].setText(f"Chave disponível via {source} (valor oculto)." if source
                                   else "Sem chave no cofre do sistema ou no ambiente.")

    def save_key(self, name: str):
        control = self._controls[name]
        value = control["key"].text()
        if not value:
            control["status"].setText("Digite uma chave antes de salvar.")
            return
        try:
            store_api_key(name, value)
            control["key"].clear()
            self._refresh_key_state(name)
            control["status"].setText("Chave salva no cofre do sistema. Ela não foi adicionada a arquivo ou log.")
        except Exception as exc:
            control["status"].setText(str(exc)[:300])

    def query_models(self, name: str):
        control = self._controls[name]
        if name in self._workers and self._workers[name].isRunning():
            return
        control["test"].setEnabled(False)
        control["status"].setText("Consultando apenas o catálogo do provedor; nenhuma pergunta foi enviada…")
        worker = _CatalogWorker(name, self)
        self._workers[name] = worker
        worker.ready.connect(self._models_ready)
        worker.failed.connect(self._models_failed)
        worker.finished.connect(lambda provider=name: self._catalog_finished(provider))
        worker.start()

    @pyqtSlot(str, object)
    def _models_ready(self, name: str, models: list[dict]):
        control = self._controls[name]
        current = control["model"].currentData() or control["model"].currentText()
        control["model"].clear()
        for item in models:
            if name == "openrouter" and not item["free_candidate"]:
                continue
            control["model"].addItem(item["id"], item["id"])
        index = control["model"].findData(current)
        if index >= 0:
            control["model"].setCurrentIndex(index)
        elif control["model"].count():
            control["model"].setCurrentIndex(0)
        self._refresh_key_state(name)
        control["status"].setText(
            f"Catálogo consultado: {control['model'].count()} modelo(s) selecionável(is). "
            "Isso não confirma autorização para gerar; a geração pode consumir cota."
        )
        self.apply_settings(name)

    @pyqtSlot(str, str)
    def _models_failed(self, name: str, message: str):
        control = self._controls[name]
        self._refresh_key_state(name)
        control["status"].setText(message)

    def _catalog_finished(self, name: str):
        self._workers.pop(name, None)
        self._controls[name]["test"].setEnabled(True)

    def apply_settings(self, name: str):
        control = self._controls[name]
        model = control["model"].currentData() or control["model"].currentText()
        enabled = control["active"].isChecked()
        allow = control["allow"].isChecked()
        try:
            set_provider_settings(name, model=model or "", enabled=enabled,
                                  free_only_confirmed=allow)
        except (OSError, ProviderError, ValueError) as exc:

            if enabled or allow:
                control["status"].setText(str(exc)[:300])

    def remove_key(self, name: str):
        if QMessageBox.question(
            self, "Remover chave", f"Apagar do cofre do sistema a chave de {PROVIDERS[name]['label']}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_api_key(name)
            control = self._controls[name]
            control["active"].setChecked(False)
            control["allow"].setChecked(False)
            self._refresh_key_state(name)
            source = key_source(name)
            control["status"].setText(
                "Chave removida do cofre; uma variável de ambiente continua configurada e prioritária."
                if source == "ambiente" else "Chave removida do cofre. Nenhuma chave foi exibida."
            )
        except Exception as exc:
            self._controls[name]["status"].setText(str(exc)[:300])

    def closeEvent(self, event):
        active = [worker for worker in self._workers.values() if worker.isRunning()]
        if active:
            QMessageBox.information(self, "Consulta em andamento", "Aguarde a consulta de catálogo terminar.")
            event.ignore()
            return
        super().closeEvent(event)
