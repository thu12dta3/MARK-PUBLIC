
import contextlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

sys.path.insert(0,'/candidate/src')


def main():
    payload=json.load(sys.stdin)
    operation=payload['operation']
    if operation=='probe':
        host_user = os.environ.get('USER', 'user')
        result={'private_home_visible': Path(f'/home/{host_user}').exists(),
                'host_config_visible':Path('/home/mark/.config/mark/config.json').exists(),
                'interfaces':[name for _,name in socket.if_nameindex()],
                'credential_environment':any(key in os.environ for key in ('GH_TOKEN','GITHUB_TOKEN','SSH_AUTH_SOCK'))}
    elif operation=='reply':
        from mark.cognition.providers import OllamaProvider,ModelRequest
        replies=iter(payload['replies']);calls=[]
        cfg={'ollama':{'model':'fixture','url':'http://127.0.0.1:11434','timeout':1,'think':True,'num_predict':256}}
        def response(request,**kwargs):
            calls.append(json.loads(request.data))
            return BytesIO(json.dumps(next(replies)).encode())
        provider=OllamaProvider(cfg)
        try:
            with contextlib.redirect_stdout(StringIO()),patch('mark.cognition.providers.urllib.request.urlopen',side_effect=response):
                result={'content':provider.generate(ModelRequest('Laboratory request')).text,'error':None}
        except Exception as exc:result={'content':None,'error':type(exc).__name__}
        result['calls']=len(calls)
        result['thinking_preserved']=all(call.get('think') is True for call in calls)
        result['generation_budget_preserved']=all(call['options']['num_predict']==256 for call in calls)
    elif operation=='contracts':
        from mark.cognition.tools import default_registry,ActionRequest
        registry=default_registry()
        try:registry.invoke(ActionRequest('chemistry.molar_mass',{'formula':'H2O'}),set());denied=False
        except PermissionError:denied=True
        result={'unauthorized_tool_denied':denied}
    else:raise ValueError('RPC não registrada')
    print(json.dumps(result,allow_nan=False))

if __name__=='__main__':main()
