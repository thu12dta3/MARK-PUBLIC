
import math
import subprocess
import threading
import time


def gpu_sample():
    r=subprocess.run(['/usr/bin/nvidia-smi','--id=0','--query-gpu=power.draw,memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2)
    if r.returncode:raise RuntimeError('GPU sensor unavailable')
    watts,mib=map(float,r.stdout.strip().split(','))
    if not all(math.isfinite(v) and v>=0 for v in (watts,mib)):raise ValueError('Invalid GPU sensor')
    return {'time':time.monotonic(),'watts':watts,'vram_gib':mib/1024}


class EnergyMeter:
    def __init__(self,sampler=gpu_sample,interval=.2):
        self.sampler=sampler;self.interval=interval;self.samples=[];self.failed=False;self.stop=threading.Event()
    def sample(self):
        try:self.samples.append(self.sampler())
        except Exception:self.failed=True
    def __enter__(self):
        self.started=time.monotonic();self.sample()
        def worker():
            while not self.stop.wait(self.interval):self.sample()
        self.thread=threading.Thread(target=worker,daemon=True);self.thread.start();return self
    def __exit__(self,*args):
        self.stop.set();self.thread.join(timeout=3);self.sample();self.ended=time.monotonic()
    def result(self):
        duration=self.ended-self.started
        valid=not self.failed and len(self.samples)>=2
        energy=sum((b['time']-a['time'])*(a['watts']+b['watts'])/2 for a,b in zip(self.samples,self.samples[1:])) if valid else None
        return {'energy_joules':energy,'mean_watts':energy/duration if energy is not None and duration>0 else None,
                'gpu_board_peak_gib':max((s['vram_gib'] for s in self.samples),default=None),
                'duration_seconds':duration,'samples':len(self.samples),'energy_scope':'GPU board, includes other GPU processes',
                'sensor_status':'MEASURED' if valid else 'UNMEASURED'}


def compare_efficiency(before,after,regression=False):

    keys=('passed','total','energy_joules','vram_peak_gib','latency_seconds')
    for row in (before,after):
        for key in keys:
            v=row.get(key)
            if type(v) not in (int,float) or not math.isfinite(v) or v<0:
                return {'decision':'REJECTED','reason':'UNMEASURED','promotion_authorized':False}
        if row['total']<=0 or row['passed']>row['total'] or any(row[k]<=0 for k in ('energy_joules','vram_peak_gib','latency_seconds')):
            return {'decision':'REJECTED','reason':'INVALID_METRIC','promotion_authorized':False}
    if before['total']!=after['total'] or regression or after['passed']<before['passed']:
        reason='QUALITY_REGRESSION'
    elif any(after[k]>before[k]*1.05 for k in ('energy_joules','vram_peak_gib','latency_seconds')):
        reason='RESOURCE_REGRESSION'
    elif after['passed']<=before['passed'] and not any(after[k]<before[k]*.9 for k in ('energy_joules','vram_peak_gib','latency_seconds')):
        reason='NO_MEASURABLE_GAIN'
    else:reason='PARETO_GAIN'
    ratios={mode:{'correct_per_joule':r['passed']/r['energy_joules'],'correct_per_peak_gib':r['passed']/r['vram_peak_gib']} for mode,r in [('before',before),('after',after)]}
    return {'decision':'REVIEW_REQUIRED' if reason=='PARETO_GAIN' else 'REJECTED','reason':reason,'efficiency':ratios,'promotion_authorized':False}
