
from __future__ import annotations
import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from .cognition.storage import private_file

DEFAULT_DB = Path.home() / ".local/state/mark/wear/telemetry.sqlite3"
MAX_EVENTS = 10_000
MAX_BATCH = 32
MAX_BYTES = 8192
ID = re.compile(r"[a-f0-9]{32}\Z")
LIMITS = {"battery": (0,100), "foreground_ms": (0,86_400_000),
          "screen_ms": (0,86_400_000), "sensor_events": (0,1_000_000),
          "microphone_events": (0,10_000), "gps_events": (0,10_000),
          "tx_bytes": (0,10_000_000), "connection_events": (0,100_000),
          "error_events": (0,100_000), "temperature_c": (-20,80)}

def validate_batch(payload, now=None):
    now = int(time.time()) if now is None else now
    if not isinstance(payload,dict) or set(payload)!={"events"}: raise ValueError("Invalid batch")
    rows=payload["events"]
    if not isinstance(rows,list) or not 1<=len(rows)<=MAX_BATCH: raise ValueError("Batch limit")
    valid=[]
    for row in rows:
        if not isinstance(row,dict) or set(row)-({"id","at","charging"}|set(LIMITS)): raise ValueError("Unknown metric")
        if not isinstance(row.get("id"),str) or not ID.fullmatch(row["id"]): raise ValueError("Invalid id")
        stamp=row.get("at")
        if type(stamp) is not int or not now-7*86400<=stamp<=now+300: raise ValueError("Invalid date")
        if "charging" in row and type(row["charging"]) is not bool: raise ValueError("Invalid charging")
        for name,(low,high) in LIMITS.items():
            if name in row:
                v=row[name]
                if type(v) not in (int,float) or not low<=v<=high or not math.isfinite(v): raise ValueError("Invalid metric")
        if "battery" not in row: raise ValueError("Battery required")
        valid.append(row)
    return valid

class WearTelemetryStore:
    def __init__(self,path=DEFAULT_DB):
        self.path=private_file(Path(path)); self.lock=threading.RLock()
        db=self.connection()
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS events(device TEXT NOT NULL, id TEXT NOT NULL, at INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(device,id))")
                db.execute("CREATE INDEX IF NOT EXISTS events_date ON events(at)")
        finally: db.close()
    def connection(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.execute("PRAGMA max_page_count=4096")
        return db
    def record(self,device,payload,now=None):
        now=int(time.time()) if now is None else now
        rows=validate_batch(payload,now)
        with self.lock:
            db=self.connection()
            try:
                with db:
                    db.execute("DELETE FROM events WHERE at<?",(now-30*86400,))
                    before=db.total_changes
                    for row in rows: db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?)",(device,row["id"],row["at"],json.dumps(row,separators=(",",":"),allow_nan=False)))
                    added=db.total_changes-before
                    db.execute("DELETE FROM events WHERE rowid IN (SELECT rowid FROM events ORDER BY at DESC,rowid DESC LIMIT -1 OFFSET ?)",(MAX_EVENTS,))
                return {"accepted":len(rows),"inserted":added,"health_stored":False}
            finally: db.close()
    def summary(self,device,now=None):
        now=int(time.time()) if now is None else now
        with self.lock:
            db=self.connection()
            try: rows=[json.loads(r[0]) for r in db.execute("SELECT record FROM events WHERE device=? AND at>=? ORDER BY at",(device,now-7*86400))]
            finally: db.close()
        daily=[r for r in rows if r["at"]>=now-86400]
        drop=sum(max(0,a["battery"]-b["battery"]) for a,b in zip(daily,daily[1:]) if not a.get("charging") and not b.get("charging"))
        return {"samples_24h":len(daily),"samples_7d":len(rows),"observed_drop_pct":drop if len(daily)>1 else None,
                "foreground_ms_24h":sum(r.get("foreground_ms",0) for r in daily),
                "mark_battery_attribution":"UNAVAILABLE", "autonomy_estimate":"UNAVAILABLE",
                "method":"Sparse on-demand samples; charging periods excluded; not MARK-specific energy consumption."}

_store_creation_lock=threading.Lock()
def get_store(server):
    with _store_creation_lock:
        if not hasattr(server,"wear_store"): server.wear_store=WearTelemetryStore()
        return server.wear_store

def get_summary(handler):
    device=getattr(handler,"_device_id",None)
    if not device: handler._error(403,"Wear requires an authorized signed device.");return
    try: handler._json(200,get_store(handler.server).summary(device))
    except (OSError,sqlite3.Error): handler._error(503,"Wear telemetry unavailable.")

def post_events(handler):
    device=getattr(handler,"_device_id",None)
    if not device: handler._error(403,"Wear requires an authorized signed device.");return
    body=getattr(handler,"_verified_body",b"")
    if not 2<=len(body)<=MAX_BYTES: handler._error(413,"Wear batch limit.");return
    try:
        payload=json.loads(body,parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
        validate_batch(payload)
        result=get_store(handler.server).record(device,payload)
    except (ValueError,TypeError,RecursionError): handler._error(400,"Invalid Wear telemetry.");return
    except (OSError,sqlite3.Error): handler._error(503,"Wear telemetry unavailable.");return
    handler._json(200,result)
