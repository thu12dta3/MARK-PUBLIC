#!/usr/bin/env python3

import argparse,json,os,subprocess
from pathlib import Path
COMMANDS={
    "api":["getprop","ro.build.version.sdk"],
    "android":["getprop","ro.build.version.release"],
    "abi":["getprop","ro.product.cpu.abilist"],
    "display_size":["wm","size"],"display_density":["wm","density"],
    "storage":["df","-k","/data"],"memory":["cat","/proc/meminfo"],
    "features":["pm","list","features"],
    "sensors":["dumpsys","sensorservice"],"battery":["dumpsys","battery"],
    "gms":["dumpsys","package","com.google.android.gms"],
    "home":["cmd","package","resolve-activity","--brief","-a","android.intent.action.MAIN","-c","android.intent.category.HOME"],
}
def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument("--adb",default="adb")
    a.add_argument("--serial");a.add_argument("--output",type=Path);args=a.parse_args()
    if not args.serial or not args.output:
        print("PHYSICAL TEST REQUIRED: pass --serial and a private --output.");return
    result={"state":"UNVERIFIED","commands":{}}
    for name,cmd in COMMANDS.items():
        r=subprocess.run([args.adb,"-s",args.serial,"shell",*cmd],capture_output=True,text=True,timeout=20)

        result["commands"][name]={"exit_code":r.returncode,"output":r.stdout[:250000],"error":r.stderr[:1000]}
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if args.output.parent.is_symlink():raise PermissionError("Private output required")
    args.output.parent.chmod(0o700)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,"O_NOFOLLOW",0),0o600)
    os.fchmod(fd,0o600)
    with os.fdopen(fd,"w") as f:json.dump(result,f,indent=2)
    print("Private diagnostics saved. Review before sharing; no hardware verification declared.")
if __name__=="__main__":main()
