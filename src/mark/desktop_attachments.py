from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from .attachment_limits import MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS_TOTAL
from .desktop_helpers import TEXT_ATTACHMENT_EXTENSIONS


class AttachmentsMixin:
    def add_attachments(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Anexar arquivos", str(Path.home()),
            "Arquivos aceitos (*.jpg *.jpeg *.png *.webp *.pdf *.docx *.txt *.md *.csv *.json *.yaml *.yml *.xml *.html *.py *.log *.toml *.ini *.cfg);;Todos os arquivos (*)",
        )
        if not paths:
            return
        self._add_attachment_paths(paths)

    def _set_drop_target_state(self, active: bool):
        self.composer_box.setProperty("dropActive", active)
        self.composer_box.style().unpolish(self.composer_box)
        self.composer_box.style().polish(self.composer_box)
        self.composer_box.update()
        if active:
            self.composer_hint.setText("Solte para anexar · até 5 arquivos")
        else:
            self.composer_hint.setText("Enter envia · Shift+Enter quebra a linha")

    def _add_attachment_paths(self, paths):
        accepted = set(self.attachments)
        for name in paths:
            path = Path(name)
            if path in accepted:
                continue
            if len(self.attachments) >= MAX_ATTACHMENTS:
                QMessageBox.information(self, "Anexos", f"O limite é de {MAX_ATTACHMENTS} arquivos por mensagem.")
                break
            if not path.is_file():
                QMessageBox.warning(self, "Anexos", f"{path.name} não é um arquivo regular.")
                continue
            if path.suffix.lower() not in TEXT_ATTACHMENT_EXTENSIONS | {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp"}:
                QMessageBox.warning(self, "Anexos", f"Formato não suportado: {path.name}.")
                continue
            try:
                new_size = path.stat().st_size
                current_size = sum(item.stat().st_size for item in self.attachments)
            except OSError:
                QMessageBox.warning(self, "Anexos", f"Não consegui acessar {path.name}.")
                continue
            if new_size > MAX_ATTACHMENT_BYTES:
                QMessageBox.warning(self, "Anexos", f"{path.name} excede o limite de 6 MB por arquivo.")
                continue
            if current_size + new_size > MAX_ATTACHMENTS_TOTAL:
                QMessageBox.warning(self, "Anexos", "Os anexos juntos não podem passar de 8 MB.")
                continue
            self.attachments.append(path)
            accepted.add(path)
        self._refresh_attachment_list()

    def remove_selected_attachment(self):
        index = self.attachment_list.currentRow()
        if 0 <= index < len(self.attachments):
            self.attachments.pop(index)
            self._refresh_attachment_list()

    def _refresh_attachment_list(self):
        self.attachment_list.clear()
        for path in self.attachments:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            label = f"{path.name}  ·  {size / 1024:.0f} KB"
            self.attachment_list.addItem(label)
        self.attachment_list.setVisible(bool(self.attachments))
        self.remove_attachment_button.setVisible(bool(self.attachments))
        self.remove_attachment_button.setEnabled(
            bool(self.attachments) and self.attachment_list.currentRow() >= 0
        )
