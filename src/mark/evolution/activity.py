
from datetime import datetime
from zoneinfo import ZoneInfo

PROGRESS_STAGES={'OPTIMIZING':(2,'Preparando otimização do modelo'),'TRAINING':(3,'Treinando Qwen · QLoRA'),'OBSERVING':(1,'Observando métricas'),'PLANNING':(2,'Preparando hipótese'),
    'GENERATING':(3,'Gerando candidato'),'BASELINE':(4,'Testando versão atual'),
    'CANDIDATE':(5,'Testando candidato'),'EVALUATING':(6,'Comparando resultados'),
    'COMPLETE':(7,'Ciclo concluído'),'WAITING':(0,'Aguardando próximo ciclo')}
OUTCOMES={'DONE':'Concluída','FAILED':'Falhou','CANCELLED':'Cancelada','DEFERRED':'Adiada',
          'REVIEW_REQUIRED':'Candidato pronto para revisão','REJECTED':'Candidato descartado'}


def phase_report_text(record):
    label=PROGRESS_STAGES.get(record.get('phase'),(0,'Etapa'))[1]
    result=OUTCOMES.get(record.get('status'),'Em andamento')
    rows=[label,result]
    if 'duration_seconds' in record:rows.append(f"Duração: {record['duration_seconds']:.1f} s")
    summary=record.get('summary',{})
    for key,label in [('observed','Ocorrências novas'),('opportunities','Oportunidades'),('edits','Edições propostas'),('experiments','Experimentos')]:
        if key in summary:rows.append(f'{label}: {summary[key]}')
    if 'passed' in summary and 'total' in summary:rows.append(f"Testes aprovados: {summary['passed']}/{summary['total']}")
    if 'gain' in summary:rows.append(f"Variação nos testes: {summary['gain']:+d}")
    if 'security_gate' in summary:rows.append('Verificação de segurança: '+('aprovada' if summary['security_gate'] else 'reprovada'))
    if 'decision' in summary:rows.append(OUTCOMES.get(summary['decision'],'Resultado registrado'))
    for key,label,unit in [('quality_before','Acertos antes',''),('quality_after','Acertos depois',''),('energy_joules','Energia de inferência',' J'),('vram_peak_gib','Pico de VRAM',' GiB'),('latency_seconds','Tempo da avaliação',' s')]:
        if key in summary:rows.append(f'{label}: {summary[key]:.2f}{unit}')
    if 'error_type' in summary:
        reason='Você interrompeu a etapa.' if record.get('status')=='CANCELLED' else {
            'JSONDecodeError':'O modelo devolveu uma proposta inválida.',
            'TimeoutError':'A etapa excedeu o tempo máximo.',
            'SandboxUnavailable':'O ambiente isolado não ficou disponível.',
            'RepositoryBusy':'O repositório está em edição. Aguardando uma versão estável.'}.get(summary['error_type'],'Houve uma falha técnica na etapa.')
        rows.append('Motivo: '+reason)
    return '\n'.join(rows)


def log_line(record):
    clock=datetime.fromtimestamp(record.get('time',0),ZoneInfo('America/Sao_Paulo')).strftime('%H:%M:%S')
    if record.get('kind')=='control':
        text='Início solicitado.' if record.get('action')=='start' else 'Parada solicitada.'
        if record.get('status')=='FAILED':text='Não foi possível '+('iniciar.' if record.get('action')=='start' else 'parar.')
    elif record.get('kind')=='training_step':text='Treino · passo '+str(record.get('summary',{}).get('completed_steps',0))+' · '+str(record.get('summary',{}).get('samples',0))+' exemplos processados.'
    elif record.get('kind')=='recovery':text=PROGRESS_STAGES.get(record.get('phase'),(0,'Etapa'))[1]+' · erro temporário, nova tentativa '+str(record.get('summary',{}).get('attempt',1))+' de 2.'
    elif record.get('kind')=='waiting' and record.get('reason')=='training_interval':text='Treino concluído; aguardando intervalo da próxima rodada.'
    elif record.get('kind')=='waiting' and record.get('reason')=='repository':text='Aguardando: repositório em edição.'
    elif record.get('kind')=='waiting':text='Aguardando intervalo entre ciclos.' if record.get('reason')=='interval' else 'Adiado: o modelo está em uso.'
    else:
        label=PROGRESS_STAGES.get(record.get('phase'),(0,'Etapa'))[1]
        text=label+'…' if record.get('kind')=='phase_started' else label+' · '+OUTCOMES.get(record.get('status'),'concluída').lower()+'.'
    return clock+'  '+text
