import os
import sqlite3
import stat


class HistoryMixin:
    def _load_history(self):
        self._ensure_conversation_store()
        self._load_active_conversation()

    def _conversation_db(self):
        path = self._conversations_file()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.stat()
        if parent.st_uid != os.getuid():
            raise PermissionError("A pasta das conversas do MARK precisa pertencer ao usuário.")
        path.parent.chmod(0o700)
        if not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("O histórico de conversas do MARK precisa ser privado (0600).")
        return sqlite3.connect(path, timeout=2)

    def _conversation_rows(self, specialization):
        db = self._conversation_db()
        try:
            try:
                return db.execute(
                    "SELECT id,title FROM conversations WHERE specialization=? ORDER BY updated_at DESC,id",
                    (specialization,),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        finally:
            db.close()

    def _refresh_conversation_selector(self, select_id=None):
        if not hasattr(self, "conversation_selector"):
            return
        self._conversation_switching = True
        try:
            self.conversation_selector.clear()
            rows = self._conversation_rows(self.current_specialization)
            for conversation_id, title in rows:
                self.conversation_selector.addItem(title, conversation_id)
            if self.current_specialization == "general":
                self.conversation_selector.setCurrentIndex(0)
                self.conversation_selector.hide()
                self.new_conversation_button.hide()
                self.current_conversation_id = "general-main"
            else:
                self.conversation_selector.show()
                self.new_conversation_button.show()
                wanted = select_id or self.current_conversation_id
                index = self.conversation_selector.findData(wanted)
                if index < 0 and self.conversation_selector.count():
                    index = 0
                if index >= 0:
                    self.conversation_selector.setCurrentIndex(index)
                    self.current_conversation_id = self.conversation_selector.currentData()
                    self.specialization_conversations[self.current_specialization] = self.current_conversation_id
        finally:
            self._conversation_switching = False

    def _sync_history(self, force: bool = False):
        if self.busy and not force:
            return
        try:
            self._ensure_conversation_store()
            self._import_shared_turns()
            db = self._conversation_db()
            try:
                rows = db.execute(
                    "SELECT id,role,content,created_at FROM conversation_messages WHERE conversation_id=? ORDER BY id DESC LIMIT 64",
                    (self.current_conversation_id,),
                ).fetchall()
            finally:
                db.close()
            signature = tuple(rows)
            if signature == self.history_signature:
                return
            self._load_active_conversation()
            self.history_signature = signature
        except (OSError, sqlite3.Error, PermissionError):
            return
