import copy, hashlib, http.client, json, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from mark.config import DEFAULTS
from mark.device_auth import DeviceRegistry, b64, signed_message, session_message
from mark.mobile_api import MobileServer, _conversation
from mark.memory import ShortMemory
from mark.agenda import Agenda

class IdleModel:
    awake=False
    def __init__(self,*a): pass
    def wake(self): self.awake=True
    def sleep(self): self.awake=False

class Attacks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.registry=DeviceRegistry(Path(self.tmp.name)/'devices.db')
        self.cfg=copy.deepcopy(DEFAULTS)
        self.memory=ShortMemory(); self.memory.add('SENTINEL_PRIVATE','never disclose without authorization')
        self.errors=[]
        with patch('mark.mobile_api.Agenda',side_effect=lambda:Agenda(Path(self.tmp.name)/'agenda.db')), patch('mark.mobile_api.ModelSession',IdleModel):
            self.server=MobileServer(self.cfg,'lab-token',0,self.memory,self.registry)
        self.server.handle_error=lambda *args:self.errors.append(type(sys.exc_info()[1]).__name__)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.brain=patch('mark.mobile_api.answer',return_value='safe laboratory answer'); self.brain.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.registry.close(); self.brain.stop(); self.tmp.cleanup()
    def req(self,path,body=None,headers=None):
        con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
        try:
            con.request('POST' if body is not None else 'GET',path,body=body,headers=headers or {})
            r=con.getresponse(); return r.status,r.read()
        except http.client.RemoteDisconnected: return 0,b''
        finally: con.close()
    def pair(self):
        key=ec.generate_private_key(ec.SECP256R1()); code=self.registry.open_enrollment()
        der=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        device=self.registry.enroll(code,'lab-device',b64(der),b64(key.sign(b'MARK-PAIR-V1\n'+code.encode(),ec.ECDSA(hashes.SHA256()))))
        self.registry.update_device(device,'authorized'); return key,device
    def headers(self,key,device,path='/health',body=b'',method='GET',token=None,nonce=None):
        stamp=str(int(time.time())); nonce=nonce or __import__('secrets').token_hex(16); rid='request-'+nonce
        data=session_message(method,path,body,stamp,nonce,rid,token) if token else signed_message(method,path,body,stamp,nonce)
        h={'X-Mark-Device':device,'X-Mark-Time':stamp,'X-Mark-Nonce':nonce,'X-Mark-Signature':b64(key.sign(data,ec.ECDSA(hashes.SHA256())))}
        if token:h.update({'X-Mark-Session':token,'X-Mark-Request-Id':rid})
        return h
    def test_unauthenticated_routes(self):
        for path in ['/health','/v1/history','/v1/agenda','/v1/models']:
            with self.subTest(path=path):self.assertEqual(self.req(path)[0],401)
    def test_lockdown_denies_legacy_history(self):
        self.registry.set_lockdown(True)
        status,body=self.req('/v1/history',headers={'Authorization':'Bearer lab-token'})
        self.assertNotIn(b'SENTINEL_PRIVATE',body); self.assertIn(status,(401,403))
    def test_malformed_enrollment_types(self):
        for code in [[],{},None,23]:
            with self.subTest(code=code):
                body=json.dumps({'code':code,'name':'x','public_key':'AA','proof':'AA'}).encode()
                self.assertEqual(self.req('/v1/pair/enroll',body)[0],400)
        self.assertEqual(self.errors,[])
    def test_malformed_model_types(self):
        for model in [[],{},True,4,None]:
            with self.subTest(model=model):
                body=json.dumps({'model':model,'messages':[{'role':'user','content':'hello'}]}).encode()
                self.assertEqual(self.req('/v1/chat/completions',body,{'Authorization':'Bearer lab-token'})[0],400)
        self.assertEqual(self.errors,[])
    def test_deep_json_denied(self):
        body=b'['*2000+b'0'+b']'*2000
        self.assertEqual(self.req('/v1/pair/enroll',body)[0],400)
        status,_=self.req('/v1/chat/completions',body,{'Authorization':'Bearer lab-token'})
        self.assertEqual(status,400);self.assertEqual(self.errors,[])
    def test_unknown_device_rejected_before_body_upload(self):
        con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=1)
        try:
            con.putrequest('POST','/v1/chat/completions')
            con.putheader('X-Mark-Device','unknown-device')
            con.putheader('Content-Length','12000000')
            con.endheaders()
            self.assertEqual(con.getresponse().status,401)
        finally:con.close()
    def test_malformed_authorization_unicode(self):
        status,_=self.req('/health',headers={'Authorization':'Bearer \u00e9'})
        self.assertEqual(status,401);self.assertEqual(self.errors,[])
    def test_signature_mutation_fuzz_500(self):
        key,device=self.pair(); token,_=self.server.sessions.issue(device)
        for i in range(500):
            h=self.headers(key,device,path='/v1/history',token=token)
            field=['X-Mark-Signature','X-Mark-Session','X-Mark-Device','X-Mark-Time','X-Mark-Request-Id'][i%5]
            h[field]='forged-'+str(i)
            self.assertEqual(self.req('/v1/history',headers=h)[0],401)
        self.assertEqual(self.errors,[])
    def test_signed_request_and_replay_race(self):
        key,device=self.pair(); token,_=self.server.sessions.issue(device)
        h=self.headers(key,device,token=token)
        with ThreadPoolExecutor(max_workers=12) as pool:
            statuses=list(pool.map(lambda _:self.req('/health',headers=h)[0],range(100)))
        self.assertEqual(statuses.count(200),1); self.assertEqual(statuses.count(401),99)
    def test_revoked_and_stolen_session(self):
        key,device=self.pair(); token,_=self.server.sessions.issue(device)
        attacker=ec.generate_private_key(ec.SECP256R1())
        self.assertEqual(self.req('/health',headers=self.headers(attacker,device,token=token))[0],401)
        self.registry.update_device(device,'revoked')
        self.assertEqual(self.req('/health',headers=self.headers(key,device,token=token))[0],401)
    def test_external_image_ssrf_denied(self):
        for url in ['http://127.0.0.1:11434/api/tags','file:///etc/passwd','http://169.254.169.254/']:
            body=json.dumps({'model':'mark','messages':[{'role':'user','content':[{'type':'text','text':'describe'},{'type':'image_url','image_url':{'url':url}}]}]}).encode()
            self.assertEqual(self.req('/v1/chat/completions',body,{'Authorization':'Bearer lab-token'})[0],400)
    def test_ambiguous_headers_denied(self):
        for kind in ('duplicate-auth','duplicate-length','transfer-encoding'):
            con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
            try:
                con.putrequest('GET','/health')
                con.putheader('Authorization','Bearer lab-token')
                if kind=='duplicate-auth':con.putheader('Authorization','Bearer wrong')
                elif kind=='duplicate-length':con.putheader('Content-Length','0');con.putheader('Content-Length','3')
                else:con.putheader('Transfer-Encoding','chunked')
                con.endheaders()
                with self.subTest(kind=kind):self.assertEqual(con.getresponse().status,400)
            finally:con.close()
    def test_session_rotation_does_not_exhaust_other_devices(self):
        key,device=self.pair()
        for i in range(300):
            h=self.headers(key,device,path='/v1/session',method='POST')
            self.assertEqual(self.req('/v1/session',b'',h)[0],200)
        self.assertLessEqual(len(self.server.sessions.sessions),8)
        key2,device2=self.pair()
        self.assertEqual(self.req('/v1/session',b'',self.headers(key2,device2,path='/v1/session',method='POST'))[0],200)
    def test_pairing_race_single_winner(self):
        key=ec.generate_private_key(ec.SECP256R1());code=self.registry.open_enrollment()
        der=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        proof=b64(key.sign(b'MARK-PAIR-V1\n'+code.encode(),ec.ECDSA(hashes.SHA256())))
        body=json.dumps({'code':code,'name':'lab','public_key':b64(der),'proof':proof}).encode()
        with ThreadPoolExecutor(max_workers=12) as pool:statuses=list(pool.map(lambda _:self.req('/v1/pair/enroll',body)[0],range(50)))
        self.assertEqual(statuses.count(202),1);self.assertEqual(statuses.count(400),49)
    def test_signed_critical_commands_denied(self):
        key,device=self.pair();token,_=self.server.sessions.issue(device)
        for command in ['apague a memória','desligue mark']:
            body=json.dumps({'model':'mark','messages':[{'role':'user','content':command}]}).encode()
            h=self.headers(key,device,path='/v1/chat/completions',body=body,method='POST',token=token)
            self.assertEqual(self.req('/v1/chat/completions',body,h)[0],403)
        self.assertIn('SENTINEL_PRIVATE',self.memory.render())
if __name__=='__main__':unittest.main(verbosity=2)
