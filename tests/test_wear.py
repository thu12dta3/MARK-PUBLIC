
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from mark.wear_telemetry import WearTelemetryStore,validate_batch
import test_redteam as redteam

class WearStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=WearTelemetryStore(Path(self.tmp.name)/"private/events.sqlite3")
        self.now=2000000000
        self.row={"id":"a"*32,"at":self.now,"battery":80,"charging":False,"foreground_ms":100}
    def test_opt_in_payload_dedupe_and_private_file(self):
        self.assertEqual(self.store.record("device",{"events":[self.row]},self.now)["inserted"],1)
        self.assertEqual(self.store.record("device",{"events":[self.row]},self.now)["inserted"],0)
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.store.summary("other",self.now)["samples_7d"],0)
    def test_invalid_data_and_health_rejected(self):
        for extra in ({"gps_lat":1},{"heart_rate":80},{"token":"secret"},{"battery":True},{"battery":float("nan")},{"battery":10**400},{"charging":"yes"},{"at":True},{"id":"../path"},{"at":self.now+1000}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):validate_batch({"events":[self.row|extra]},self.now)
    def test_batch_cap_and_atomic_validation(self):
        for events in ([],[self.row]*33):
            with self.assertRaises(ValueError):self.store.record("device",{"events":events},self.now)
        with self.assertRaises(ValueError):self.store.record("device",{"events":[self.row,self.row|{"battery":500}]},self.now)
        self.assertEqual(self.store.summary("device",self.now)["samples_7d"],0)
    def test_retention_and_quota(self):
        for i in range(6):
            row=self.row|{"id":f"{i:032x}","at":self.now-i}
            with patch("mark.wear_telemetry.MAX_EVENTS",3):self.store.record("device",{"events":[row]},self.now)
        self.assertEqual(self.store.summary("device",self.now)["samples_7d"],3)
        self.store.record("device",{"events":[self.row|{"id":"b"*32,"at":self.now+31*86400}]},self.now+31*86400)
        self.assertEqual(self.store.summary("device",self.now+31*86400)["samples_7d"],1)
    def test_sparse_battery_not_attributed_to_mark(self):
        self.store.record("device",{"events":[self.row,self.row|{"id":"b"*32,"at":self.now+60,"battery":75}]},self.now+60)
        report=self.store.summary("device",self.now+60)
        self.assertEqual(report["observed_drop_pct"],5)
        self.assertEqual(report["mark_battery_attribution"],"UNAVAILABLE")
        self.assertEqual(report["autonomy_estimate"],"UNAVAILABLE")
    def test_symlink_denied(self):
        link=Path(self.tmp.name)/"link.sqlite3";link.symlink_to(self.store.path)
        with self.assertRaises(PermissionError):WearTelemetryStore(link)
    def test_read_only_sensor_and_privacy_manifest(self):
        root=Path(__file__).resolve().parents[1]
        manifest=(root/"mark-wear/app/src/main/AndroidManifest.xml").read_text()
        for forbidden in ("WAKE_LOCK","ACCESS_FINE_LOCATION","BODY_SENSORS_BACKGROUND","FOREGROUND_SERVICE_MICROPHONE"):
            self.assertNotIn(forbidden,manifest)

        import xml.etree.ElementTree as ET
        tree=ET.fromstring(manifest);android='{http://schemas.android.com/apk/res/android}'
        permissions={node.get(android+'name') for node in tree.findall('uses-permission')}
        self.assertIn('android.permission.RECORD_AUDIO',permissions)
        self.assertIn('android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE',permissions)
        for service in tree.findall('./application/service'):
            if service.get(android+'name') in ('.WearLinkService','.WearCompanionService'):
                self.assertEqual(service.get(android+'exported'),'false')
                self.assertEqual(service.get(android+'foregroundServiceType'),'connectedDevice')
        source=(root/"mark-wear/app/src/main/java/br/com/bannaman/markwear/MainActivity.kt").read_text()
        self.assertIn("sensors.stop()",source);self.assertIn('getBoolean("telemetry",false)',source)
        self.assertIn("events.length()-32",source)

class WearApiTests(unittest.TestCase):
    setUp=redteam.Attacks.setUp;tearDown=redteam.Attacks.tearDown;req=redteam.Attacks.req;pair=redteam.Attacks.pair;headers=redteam.Attacks.headers
    def signed(self,path,body=b""):
        key,device=self.pair();token,_=self.server.sessions.issue(device)
        return self.headers(key,device,path,body,"POST" if body else "GET",token=token)
    def test_requires_signed_device_not_legacy_token(self):
        for path in ("/v1/wear/summary",):
            self.assertEqual(self.req(path)[0],401)
            self.assertEqual(self.req(path,headers={"Authorization":"Bearer lab-token"})[0],403)
        body=b'{"events":[]}'
        self.assertEqual(self.req("/v1/wear/telemetry",body,{"Authorization":"Bearer lab-token"})[0],403)
    def test_signed_persistence_and_invalid_payload(self):
        self.server.wear_store=WearTelemetryStore(Path(self.tmp.name)/"wear.sqlite3")
        import time
        body=json.dumps({"events":[{"id":"a"*32,"at":int(time.time()),"battery":70}]}).encode()
        path="/v1/wear/telemetry";headers=self.signed(path,body)
        status,data=self.req(path,body,headers);self.assertEqual(status,200)
        self.assertEqual(json.loads(data)["inserted"],1)
        self.assertEqual(self.req(path,body,headers)[0],401)
        bad=b'{"events":[{"token":"private"}]}'
        self.assertEqual(self.req(path,bad,self.signed(path,bad))[0],400)
    def test_signed_summary_scoped_to_phone(self):
        import time
        self.server.wear_store=WearTelemetryStore(Path(self.tmp.name)/"wear.sqlite3")
        key,device=self.pair();token,_=self.server.sessions.issue(device)
        path="/v1/wear/telemetry"
        body=json.dumps({"events":[{"id":"b"*32,"at":int(time.time()),"battery":70}]}).encode()
        self.assertEqual(self.req(path,body,self.headers(key,device,path,body,"POST",token=token))[0],200)
        path="/v1/wear/summary"
        code,data=self.req(path,headers=self.headers(key,device,path,method="GET",token=token))
        self.assertEqual(code,200);self.assertEqual(json.loads(data)["samples_24h"],1)
        code,data=self.req(path,headers=self.signed(path))
        self.assertEqual(code,200);self.assertEqual(json.loads(data)["samples_24h"],0)

    def test_wear_origin_and_critical_denial(self):
        path="/v1/chat/completions"
        body=json.dumps({"model":"mark-call","mark_origin":"wear","messages":[{"role":"user","content":"pergunta de teste"}]}).encode()
        with patch("mark.mobile_api.answer",return_value="resposta") as answer:
            status,_=self.req(path,body,self.signed(path,body));self.assertEqual(status,200)
            self.assertEqual(answer.call_args.kwargs["origin"],"relógio")
        self.assertEqual(self.memory.history(1)[0]["source"],"wear-chat")
        from mark.memory import source_label
        self.assertEqual(source_label("wear-chat"),"relógio")
        critical=json.dumps({"mark_origin":"wear","model":"mark-call","messages":[{"role":"user","content":"limpa a memória"}]}).encode()
        self.assertEqual(self.req(path,critical,self.signed(path,critical))[0],403)

if __name__=="__main__":unittest.main()
