
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time

LANGUAGES={'python':('main.py',['/usr/bin/python3','-I','-m','py_compile','/work/main.py']),
           'c':('main.c',['/usr/bin/gcc','-std=c17','-Wall','-Wextra','-fsyntax-only','/work/main.c']),
           'cpp':('main.cpp',['/usr/bin/g++','-std=c++20','-Wall','-Wextra','-fsyntax-only','/work/main.cpp']),
           'kotlin':('Main.kt',['/usr/bin/kotlinc','/work/Main.kt','-d','/work/output.jar']),
           'assembly':('main.s',['/usr/bin/as','--64','-o','/work/output.o','/work/main.s']),
           'aarch64':('main.s',['/usr/bin/clang','--target=aarch64-linux-gnu','-c','/work/main.s','-o','/work/output.o'])}
ALIASES={'py':'python','c++':'cpp','cc':'cpp','kt':'kotlin','asm':'assembly','s':'assembly','arm64':'aarch64'}

def kotlin_dependencies():
    cache=Path.home()/'.gradle/caches/modules-2/files-2.1'
    coordinates=[('org.jetbrains.kotlin','kotlin-compiler-embeddable'),('org.jetbrains.kotlin','kotlin-stdlib'),('org.jetbrains.kotlin','kotlin-script-runtime'),('org.jetbrains.kotlin','kotlin-reflect'),('org.jetbrains.kotlinx','kotlinx-coroutines-core-jvm'),('org.jetbrains','annotations')]
    jars=[]
    for group,artifact in coordinates:
        root=cache/group/artifact
        paths=[p for p in root.glob('*/*/*.jar') if not p.is_symlink() and not p.name.endswith(('-sources.jar','-javadoc.jar'))]
        paths.sort(key=lambda p:tuple(int(n) for n in re.findall(r'\d+',p.parts[-3])))
        if not paths:return []
        jar=paths[-1]
        if hashlib.sha1(jar.read_bytes(), usedforsecurity=False).hexdigest()!=jar.parent.name:return []
        jars.append(jar)
    return jars

def available():return {name:bool(shutil.which(command[0])) for name,(_,command) in LANGUAGES.items()}

def check(code,language,timeout=30):
    language=ALIASES.get(language.lower(),language.lower())
    if language not in LANGUAGES:raise ValueError('Linguagem não suportada pelo verificador')
    if not isinstance(code,str) or len(code.encode())>64000:raise ValueError('Código excede 64 KB')
    filename,command=LANGUAGES[language]
    result={'language':language,'code_sha256':hashlib.sha256(code.encode()).hexdigest(),'executed':False,'scope':'compiler/syntax check only','status':'UNAVAILABLE'}
    if not shutil.which('bwrap') or not shutil.which('prlimit') or not shutil.which(command[0]):return result
    with tempfile.TemporaryDirectory(prefix='mark-code-check-') as tmp:
        directory=Path(tmp);source=directory/filename;source.write_text(code);source.chmod(0o600)
        args=['/usr/bin/prlimit','--as=2147483648','--cpu=25','--fsize=2097152','--core=0','--nofile=128','--',
              '/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL','--clearenv',
              '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib','/lib64',
              '--proc','/proc','--dev','/dev',
              '--tmpfs','/tmp',  # nosec: B108
              '--dir','/home','--dir','/home/mark',
              '--setenv','HOME','/home/mark','--setenv','PATH','/usr/bin:/bin','--setenv','LANG','C.UTF-8',
              '--setenv','JAVA_OPTS','-Xms32m -Xmx256m -XX:CompressedClassSpaceSize=64m -XX:ReservedCodeCacheSize=64m -XX:MaxMetaspaceSize=256m -XX:ActiveProcessorCount=2',
              '--bind',str(directory),'/work','--chdir','/work',*command]
        if language=='kotlin':
            jars=kotlin_dependencies()
            if not jars:return result
            security=Path('/usr/lib/jvm/java-17-openjdk/conf/security/java.security').resolve()
            mounts=['--dir','/etc','--dir',str(security.parent.parent),'--dir',str(security.parent),'--ro-bind',str(security),str(security)]
            for jar in jars:mounts+=['--ro-bind',str(jar),'/deps/'+jar.name]
            java='/usr/lib/jvm/java-17-openjdk/bin/java'
            compiler=['/deps/'+p.name for p in jars]
            classpath=':'.join(p for p in compiler if any(name in p for name in ('kotlin-stdlib-','annotations-','kotlinx-coroutines-')))
            args=args[:-len(command)]+['--dir','/deps',*mounts,java,'-Xms32m','-Xmx384m','-XX:CompressedClassSpaceSize=64m','-XX:ReservedCodeCacheSize=64m','-XX:MaxMetaspaceSize=256m','-XX:ActiveProcessorCount=2',
                 '-cp',':'.join(compiler),'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler','-no-stdlib','-no-reflect','-classpath',classpath,'/work/Main.kt','-d','/work/output.jar']
        start=time.monotonic()
        with tempfile.TemporaryFile() as output:
            process=subprocess.Popen(args,stdout=output,stderr=output,start_new_session=True)
            try:process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait();result['status']='TIMEOUT';return result
            output.seek(0);diagnostic=output.read(6000).decode('utf-8',errors='replace')
        result.update(status='PASS' if process.returncode==0 else 'FAIL',returncode=process.returncode,
                      seconds=time.monotonic()-start,diagnostic=diagnostic.replace(str(directory),'/work'))
        if process.returncode and ('unresolved reference' in diagnostic or 'No such file or directory' in diagnostic):result['status']='DEPENDENCY_OR_SOURCE_ERROR'
        return result

def code_blocks(reply):
    return [(ALIASES.get(language.lower(),language.lower()),body) for language,body in re.findall(r'```([A-Za-z0-9_+]+)\s*\n(.*?)```',reply,re.S)]
