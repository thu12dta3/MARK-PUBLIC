
from __future__ import annotations

import os
import re
import sqlite3
import stat
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local/state/mark/agenda.sqlite3"
WEEKDAYS = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3,
            "sexta": 4, "sabado": 5, "domingo": 6}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


class Agenda:
    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_PATH
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.stat().st_uid != os.getuid():
            raise PermissionError("A pasta da agenda do MARK precisa pertencer ao usuário.")
        self.path.parent.chmod(0o700)
        if not self.path.exists():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                os.close(os.open(self.path, flags, 0o600))
            except FileExistsError:
                pass
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("A agenda precisa ser um arquivo privado do usuário (0600).")
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, due_at TEXT,
                created_at INTEGER NOT NULL, completed_at INTEGER, notified_at INTEGER)""")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, title: str, due_at: datetime | None = None) -> int:
        title = " ".join(title.split())[:240]
        if not title:
            raise ValueError("Escreva o que devo colocar na agenda.")
        with self._db() as db:
            cur = db.execute("INSERT INTO items (title, due_at, created_at) VALUES (?, ?, ?)",
                             (title, due_at.isoformat() if due_at else None, int(time.time())))
            return int(cur.lastrowid)

    def list_items(self, limit: int = 100) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT id,title,due_at,completed_at FROM items "
                               "ORDER BY completed_at IS NOT NULL, due_at IS NULL, due_at, id LIMIT ?",
                               (max(1, min(limit, 200)),)).fetchall()
        return [{"id": row[0], "title": row[1], "due_at": row[2], "completed": row[3] is not None}
                for row in rows]

    def complete(self, item_id: int) -> bool:
        with self._db() as db:
            cur = db.execute("UPDATE items SET completed_at=? WHERE id=? AND completed_at IS NULL",
                             (int(time.time()), item_id))
            return cur.rowcount == 1

    def due_unnotified(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now().astimezone()
        with self._db() as db:
            rows = db.execute("SELECT id,title,due_at FROM items WHERE completed_at IS NULL "
                              "AND notified_at IS NULL AND due_at IS NOT NULL ORDER BY due_at LIMIT 20").fetchall()
        result = []
        for item_id, title, due_at in rows:
            try:
                when = datetime.fromisoformat(due_at)
            except (TypeError, ValueError):
                continue
            if when <= now:
                result.append({"id": item_id, "title": title, "due_at": due_at})
        return result

    def mark_notified(self, item_id: int) -> None:
        with self._db() as db:
            db.execute("UPDATE items SET notified_at=? WHERE id=? AND notified_at IS NULL",
                       (int(time.time()), item_id))


def _due_date(command: str, now: datetime) -> datetime | None:
    n = normalize(command)
    due_day = None
    if "amanha" in n:
        due_day = now.date() + timedelta(days=1)
    elif "hoje" in n:
        due_day = now.date()
    else:
        for name, weekday in WEEKDAYS.items():
            if re.search(rf"\b{name}(?: feira)?\b", n):
                days = (weekday - now.weekday()) % 7 or 7
                due_day = now.date() + timedelta(days=days)
                break
        date_match = re.search(r"\bdia\s+(\d{1,2})(?:/(\d{1,2})(?:/(\d{2,4}))?)?\b", n)
        if date_match:
            day = int(date_match.group(1))
            month = int(date_match.group(2) or now.month)
            year = int(date_match.group(3) or now.year)
            if year < 100:
                year += 2000
            try:
                due_day = datetime(year, month, day).date()
            except ValueError:
                return None
            if due_day < now.date() and not date_match.group(3):
                try:
                    due_day = due_day.replace(year=due_day.year + 1)
                except ValueError:
                    return None
    relative = re.search(r"\b(?:daqui a|em)\s+(\d+)\s*(minutos?|mins?|horas?|dias?)\b", n)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = timedelta(minutes=amount) if unit.startswith("min") else timedelta(hours=amount) if unit.startswith("hora") else timedelta(days=amount)
        return now + delta
    clock = re.search(r"\b(?:as?\s*)?(\d{1,2})(?::(\d{2})|\s*h(?:\s*(\d{2}))?)\b", n)
    hour = minute = None
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or clock.group(3) or 0)
        if hour > 23 or minute > 59:
            return None
    if due_day is None and hour is None:
        return None
    if due_day is None:
        due_day = now.date()
        if (hour, minute) <= (now.hour, now.minute):
            due_day += timedelta(days=1)
    if hour is None:
        hour, minute = 9, 0
    return datetime.combine(due_day, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=hour, minute=minute)


def handle_command(text: str, agenda: Agenda | None = None, now: datetime | None = None) -> str | None:

    agenda = agenda or Agenda()
    now = now or datetime.now().astimezone()
    n = normalize(text).strip(" ?!.,")
    if re.search(r"\b(?:minha agenda|o que tenho|quais (?:sao )?(?:meus )?(?:compromissos|eventos|tarefas)|proximos compromissos|agenda de hoje|agenda para hoje|agenda amanha|o que esta marcado)\b", n):
        items = [item for item in agenda.list_items() if not item["completed"]]
        if not items:
            return "Sua agenda está vazia. Você pode dizer, por exemplo: ‘me lembre de pagar a conta amanhã às 15h’."
        lines = []
        for item in items[:12]:
            if item["due_at"]:
                when = datetime.fromisoformat(item["due_at"]).astimezone().strftime("%d/%m às %H:%M")
                lines.append(f"{when} — {item['title']}")
            else:
                lines.append(f"Sem horário — {item['title']}")
        return "Na sua agenda:\n" + "\n".join(lines)
    if re.search(r"\b(?:conclua|concluir|finalize|marque como feito|complete)\b", n):
        items = [item for item in agenda.list_items() if not item["completed"]]
        needle = re.sub(r".*?\b(?:conclua|concluir|finalize|marque como feito|complete)\b", "", n).strip()
        match = next((item for item in items if needle and needle in normalize(item["title"])), None)
        if match and agenda.complete(match["id"]):
            return f"Pronto, marquei ‘{match['title']}’ como concluído."
        return "Não encontrei esse item pendente na agenda."
    add_prefix = re.match(r"^(?:por favor )?(?:me lembre(?: de)?|lembre me(?: de)?|me lembra(?: de)?|lembra me(?: de)?|lembra(?: de)?|agende(?: para)?|marque(?: para)?|anote(?: para)?|adicione(?: na agenda)?(?: para)?|crie um lembrete(?: para)?)\s+", n)
    if add_prefix:
        title = text[add_prefix.end():]
        title = re.sub(r"\b(?:hoje|amanh[ãa]|segunda(?:-feira)?|terça(?:-feira)?|terca(?:-feira)?|quarta(?:-feira)?|quinta(?:-feira)?|sexta(?:-feira)?|sábado|sabado|domingo)\b", " ", title, flags=re.I)
        title = re.sub(r"\b(?:dia\s+\d{1,2}(?:/\d{1,2}(?:/\d{2,4})?)?|daqui a\s+\d+\s*(?:minutos?|mins?|horas?|dias?)|em\s+\d+\s*(?:minutos?|mins?|horas?|dias?))\b", " ", title, flags=re.I)
        title = re.sub(r"\b(?:às?|as)\s*\d{1,2}(?::\d{2}|\s*h(?:\s*\d{2})?)\b", " ", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip(" ,.;:-")
        if not title:
            return "O que você quer que eu coloque na agenda?"
        due = _due_date(text, now)
        agenda.add(title, due)
        if due:
            return f"Anotei ‘{title}’ para {due.astimezone().strftime('%d/%m às %H:%M')}."
        return f"Anotei ‘{title}’ na sua agenda."
    return None
