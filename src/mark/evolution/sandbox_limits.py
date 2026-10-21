
import os
import resource
import sys


def main():
    memory,cpu,output=map(int,sys.argv[1:4])
    resource.setrlimit(resource.RLIMIT_AS,(memory,memory))
    resource.setrlimit(resource.RLIMIT_CPU,(cpu,cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE,(output,output))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    os.execv('/usr/bin/bwrap',['/usr/bin/bwrap',*sys.argv[4:]])

if __name__=='__main__':main()
