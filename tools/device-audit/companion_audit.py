#!/usr/bin/env python3

import argparse,json,pathlib,subprocess,os
p=argparse.ArgumentParser();p.add_argument('--adb',required=True);p.add_argument('--serial',required=True);p.add_argument('--output',required=True);a=p.parse_args()
out=pathlib.Path(a.output).resolve();repo=pathlib.Path(__file__).resolve().parents[2]
if out.is_relative_to(repo): p.error('Use a private output directory outside the repository')
os.umask(0o077);out.mkdir(parents=True,exist_ok=True)
def shell(*args):return subprocess.check_output([a.adb,'-s',a.serial,'shell',*args],text=True,timeout=30).strip()
report={k:shell('getprop',k) for k in ['ro.product.model','ro.product.manufacturer','ro.build.version.release','ro.build.version.sdk','ro.build.version.security_patch','ro.product.cpu.abilist','ro.build.display.id']}
report['memory']=shell('cat','/proc/meminfo').splitlines()[:3];report['storage']=shell('df','-k','/data');report['features']=shell('pm','list','features').splitlines()
(out/'device.json').write_text(json.dumps(report,indent=2));print('Read-only audit saved privately; no device address or credentials printed')
