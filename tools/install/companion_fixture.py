#!/usr/bin/env python3



import argparse,hashlib,pathlib,re,subprocess

def command(argv):
 r=subprocess.run(argv,check=True,capture_output=True,text=True,timeout=90)
 return r.stdout

def require(condition,message):
 if not condition: raise SystemExit(message)

def main():
 p=argparse.ArgumentParser();p.add_argument('--adb',required=True);p.add_argument('--aapt',required=True);p.add_argument('--apksigner',required=True);p.add_argument('--serial',required=True);p.add_argument('--apk',required=True);p.add_argument('--sha256',required=True);p.add_argument('--signer',required=True);a=p.parse_args()
 apk=pathlib.Path(a.apk).resolve();require(apk.is_file() and 0<apk.stat().st_size<=8*1024*1024,'Invalid file or size')
 require(hashlib.sha256(apk.read_bytes()).hexdigest()==a.sha256,'Checksum mismatch')
 cert=command([a.apksigner,'verify','--print-certs',str(apk)])
 signers=re.findall(r'Signer #\d+ certificate SHA-256 digest: ([a-f0-9]{64})',cert)
 require(signers==[a.signer],'Signer mismatch or multiple signers')
 badging=command([a.aapt,'dump','badging',str(apk)])
 pkg=re.search(r"package: name='([^']+)' versionCode='(\d+)'",badging);require(pkg and pkg.group(1)=='br.com.bannaman.markstoretest','Only the own test fixture is authorized by this helper')
 require(" split='" not in badging,'Split APK unsupported')
 api=int(command([a.adb,'-s',a.serial,'shell','getprop','ro.build.version.sdk']).strip())
 minimum=re.search(r"sdkVersion:'(\d+)'",badging);require(minimum and int(minimum.group(1))<=api,'Incompatible Android version')
 before=command([a.adb,'-s',a.serial,'shell','dumpsys','package',pkg.group(1)])
 old=re.search(r'versionCode=(\d+)',before);require(not old or int(old.group(1))<=int(pkg.group(2)),'Downgrade refused')
 require(not re.search(r'flags=\[[^\]]*\bSYSTEM\b',before),'System package refused')
 output=command([a.adb,'-s',a.serial,'install','-r',str(apk)]);require('Success' in output,'Install not confirmed')
 after=command([a.adb,'-s',a.serial,'shell','dumpsys','package',pkg.group(1)])
 version=re.search(r'versionCode=(\d+)',after);require(version and version.group(1)==pkg.group(2),'Installed version not verified')
 print('Own fixture installed via explicit USB ADB and package/version verified:',pkg.group(2))
if __name__=='__main__':main()
