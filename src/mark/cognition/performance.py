
import resource
import statistics
import tempfile
from pathlib import Path
import time
from .embeddings import HashEmbeddingProvider
from .knowledge import KnowledgeStore

def process_ram():
    try:
        values={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/self/status').read_text().splitlines() if line.startswith(('VmHWM:', 'VmRSS:'))}
        return {'runner_RAM_peak_KiB':values['VmHWM'],'runner_RAM_current_KiB':values['VmRSS'],'RAM_measurement':'Linux process VmHWM/VmRSS, not Ollama or whole system'}
    except (OSError,KeyError,ValueError):
        return {'runner_RAM_peak_KiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'runner_RAM_current_KiB':None,'RAM_measurement':'getrusage fallback'}

def percentile(values,p): return sorted(values)[min(len(values)-1,int((len(values)-1)*p))]

def measure_local_index(documents=50,queries=30):
    if type(documents) is not int or not 1<=documents<=200 or type(queries) is not int or not 1<=queries<=200: raise ValueError('Carga de medição fora do limite')
    embedding=HashEmbeddingProvider(); embedding_times=[]; retrieval_times=[]
    text=('Linux kernel networking measurements and diagnostics. '*20)
    for _ in range(100):
        begin=time.perf_counter(); embedding.embed(text); embedding_times.append((time.perf_counter()-begin)*1000)
    start=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='mark-perf-') as tmp:
        root=Path(tmp)
        with KnowledgeStore(root/'index.sqlite3') as store:
            for i in range(documents):
                path=root/f'doc-{i}.md'; path.write_text(f'# Computing module{i:03d}\n'+(text+f' Module{i:03d} diagnostic fixture.\n')*3)
                store.ingest(path,'computing',title=f'Performance fixture {i}',license='USER_OWNED',source_type='USER_OWNED')
            ingestion_ms=(time.perf_counter()-start)*1000
            for i in range(queries):
                begin=time.perf_counter(); store.search(f'Linux module{i%documents:03d}',limit=3); retrieval_times.append((time.perf_counter()-begin)*1000)
            stats=store.stats()
    return {'status':'VERIFIED','scope':'synthetic corpus, process-local cache, uncontrolled machine load','documents':documents,'queries':queries,
            'ingestion_ms':ingestion_ms,'embedding_ms_median':statistics.median(embedding_times),'embedding_ms_p95':percentile(embedding_times,.95),
            'retrieval_ms_median':statistics.median(retrieval_times),'retrieval_ms_p95':percentile(retrieval_times,.95),
            **process_ram(),'index':stats,
            'semantic_embeddings':False,'VRAM_used_by_hash_embedding':0,'watts':'UNMEASURED'}
