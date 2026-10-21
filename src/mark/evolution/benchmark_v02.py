
import hashlib
from pathlib import Path
from ..cognition.benchmark import load_cases,run_benchmark


def run(path,provider=None,expected_sha256=None):
    path=Path(path)
    checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_sha256 and checksum!=expected_sha256:raise PermissionError('Rubrica/holdout mudou depois do baseline')
    cases=load_cases(path);report=run_benchmark(cases,provider)
    report['suite']='MARKBENCH-v0.2';report['suite_sha256']=checksum
    report['groups']={kind:{status:sum(row['operation']==kind and row['status']==status for row in report['cases']) for status in ('PASS','FAIL','SKIPPED')} for kind in {row['operation'] for row in report['cases']}}
    report['scope']='Additional authored model tasks and explicit component gates; never a general intelligence certification'
    return report
