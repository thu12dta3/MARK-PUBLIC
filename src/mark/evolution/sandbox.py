
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from .state import secure_directory

HERE=Path(__file__).parent


class SandboxUnavailable(RuntimeError):pass
class ExperimentCancelled(RuntimeError):pass


class Sandbox:
    def __init__(self,policy):self.policy=policy
    def _command(self,source):
        if not Path('/usr/bin/bwrap').is_file() or not Path('/usr/bin/systemd-run').is_file():
            raise SandboxUnavailable('Bubblewrap e systemd --user/cgroups são necessários')
        return ['/usr/bin/systemd-run','--user','--quiet','--scope','--collect',
                '-p',f'MemoryMax={self.policy.memory_mib}M','-p','TasksMax=32','-p','MemorySwapMax=0','-p','CPUQuota=50%',
                '/usr/bin/python3','-I',str(HERE/'sandbox_limits.py'),str(self.policy.memory_mib*1024**2),
                str(self.policy.cpu_seconds),str(2*1024**2),
                '--unshare-all','--unshare-user','--disable-userns','--die-with-parent','--new-session','--cap-drop','ALL',
                '--clearenv','--setenv','HOME','/home/mark','--setenv','LANG','C.UTF-8',
                '--setenv','PATH','/usr/bin:/bin','--setenv','QT_QPA_PLATFORM','offscreen',
                '--setenv','MARK_CONFIG','/tmp/no-config.json',  # nosec: B108
                '--setenv','HF_HUB_OFFLINE','1',
                '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
                '--symlink','usr/lib','/lib64','--proc','/proc','--dev','/dev',
                '--tmpfs','/tmp',  # nosec: B108
                '--dir','/home/mark','--dir','/candidate','--ro-bind',str(source/'src'),'/candidate/src',
                '--dir','/runner','--ro-bind',str(HERE/'sandbox_harness.py'),'/runner/harness.py',
                '--chdir','/tmp',  # nosec: B108
                '/usr/bin/python3','-I','/runner/harness.py']
    def call(self,source,payload,cancel=lambda:False):
        source=Path(source).absolute()
        if source.is_symlink() or not (source/'src').is_dir():raise PermissionError('Fonte do sandbox inválida')
        raw=json.dumps(payload,allow_nan=False).encode()
        if len(raw)>2*1024**2:raise ValueError('Entrada do laboratório grande demais')
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}',
             'DBUS_SESSION_BUS_ADDRESS':f'unix:path=/run/user/{os.getuid()}/bus'}
        started=time.monotonic()
        with tempfile.TemporaryFile() as stdin,tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
            stdin.write(raw);stdin.seek(0)
            process=subprocess.Popen(self._command(source),stdin=stdin,stdout=stdout,stderr=stderr,
                                     env=env,start_new_session=True)
            try:
                while process.poll() is None:
                    if cancel():raise ExperimentCancelled('Experimento cancelado ou pausado')
                    if time.monotonic()-started>self.policy.timeout_seconds:raise TimeoutError('Experimento excedeu duração')
                    time.sleep(.05)
                stdout.seek(0);output=stdout.read(1024*1024+1)
                if process.returncode or len(output)>1024*1024:
                    raise SandboxUnavailable('Sandbox/limite/processo falhou; saída não confiável descartada')
                result=json.loads(output,parse_constant=lambda _:(_ for _ in()).throw(ValueError('Número inválido')))
                if not isinstance(result,dict):raise ValueError('Resposta RPC inválida')
                return {'result':result,'wall_seconds':time.monotonic()-started,'output_bytes':len(output),
                        'isolation':'bwrap namespaces + systemd user cgroup + rlimits'}
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGKILL)
                    process.wait(timeout=5)

    def probe(self,source):
        data=self.call(source,{'operation':'probe'})['result']
        if data.get('private_home_visible') or data.get('host_config_visible') or data.get('credential_environment') or data.get('interfaces')!=['lo']:
            raise SandboxUnavailable('Isolamento não passou no probe independente')
        return data
