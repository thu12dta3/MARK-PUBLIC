
import argparse
from pathlib import Path
import sys
import signal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mark.evolution.cli import main
from mark.evolution.sandbox import ExperimentCancelled

def stop_cycle(_signum,_frame):
    raise ExperimentCancelled('Parada solicitada pelo proprietário')

if __name__=='__main__':
    signal.signal(signal.SIGTERM,stop_cycle)
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--state',required=True)
    a=p.parse_args();raise SystemExit(main(['--repo',a.repo,'--state',a.state,'cycle']))
