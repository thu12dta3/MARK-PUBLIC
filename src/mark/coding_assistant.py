
import re
from .coding_verify import check,code_blocks,LANGUAGES

TOPICS={
 'python':('python','pyqt','asyncio','pytest'),
 'cpp':('c++','cpp','std::','unique_ptr','shared_ptr','raii'),
 'c':('c17','c23','malloc','ponteiros em c','linguagem c','codigo em c','código em c'),
 'assembly':('assembly','assembler','asm','x86','aarch64','arm64'),
 'kotlin':('kotlin','coroutines','jetpack','flow','kotlinc')}

def languages(text):
    lowered=text.lower();selected=[language for language,terms in TOPICS.items() if any(term in lowered for term in terms)]
    if re.search(r'(?i)\b(?:linguagem|código|codigo|programa|programar)\s+(?:em\s+)?c(?!\+)\b',text) and 'c' not in selected:selected.append('c')
    return selected

def instructions(text,cfg):
    selected=languages(text)
    if not selected or cfg.get('coding',{}).get('enabled',True) is False:return ''
    specific={
      'python':'Python: verificação local de sintaxe, sem execução. Escopo/binding, iteradores, tipos e contratos; async não transforma CPU em paralelismo. Considere versão e dependências; não invente APIs.',
      'cpp':'C++: verificador local em C++20 por padrão. RAII, ownership/lifetime, regras de movimento e invalidação, data races/UB, complexidade e padrão selecionado. Preserve funcionalidades e use ferramentas antes de afirmar desempenho.',
      'c':'C: verificador local em C17 por padrão. Tamanho/limites, ponteiros, lifetime, UB, overflow, aliasing, alocação/retornos e padrão. Bibliotecas POSIX não são ISO C e ambientes embarcados diferem.',
      'assembly':'Assembly: verificador usa GNU as x86_64 (AT&T ou .intel_syntax noprefix) e Clang AArch64. Declare arquitetura, sintaxe, ABI, registradores caller/callee-saved, alinhamento da pilha, flags e convenção de retorno. PC costuma x86_64; ARM64 precisa seleção explícita. Não confunda syscalls Linux com funções C.',
      'kotlin':'Kotlin: verificador Kotlin/JVM 2.4 com JDK17, sem SDK Android. Nullability, smart casts, imutabilidade, coroutines/cancelamento, Flow e ciclo de vida. Distinga JVM de Android e informe versões; dependências Compose/Android exigem projeto e SDK.'}
    return (' Atue com rigor de engenharia nas linguagens solicitadas. Entregue código completo quando pedido, explique decisões/limites, preserve requisitos, dê testes e trate erros. '
            'Não diga que compilou, executou ou passou testes sem resultado real de ferramenta; não alegue especialização infalível. '
            +' '.join(specific[key] for key in selected))

def verify_reply(reply,text,cfg):
    if cfg.get('coding',{}).get('enabled',True) is False or cfg.get('coding',{}).get('verify',True) is False:return []
    intent=languages(text) or re.search(r'(?i)código|codigo|script|programa|fun[cç][aã]o|algoritmo|compile|depur|debug|corrija',text)
    if not intent:return []
    results=[]
    for language,body in code_blocks(reply)[:2]:
        if language not in LANGUAGES:continue
        if language=='assembly' and any(t in text.lower() for t in ('aarch64','arm64')):language='aarch64'
        try:results.append(check(body,language))
        except (ValueError,OSError):results.append({'language':language,'status':'UNAVAILABLE','executed':False})
    return results

def footer(results):
    if not results:return ''
    names={'python':'Python','c':'C17','cpp':'C++20','kotlin':'Kotlin/JVM','assembly':'Assembly x86_64','aarch64':'Assembly AArch64'}
    lines=[]
    for row in results:
        if row['status']=='PASS':state='compilação/sintaxe verificada'
        elif row['status']=='FAIL':state='o compilador apontou erros; verificação não aprovada'
        elif row['status']=='TIMEOUT':state='verificação atingiu o limite de tempo'
        else:state='verificação inconclusiva ou ambiente/dependências indisponíveis'
        lines.append(names.get(row['language'],row['language'])+': '+state)
    return '\n\nVerificação local isolada — '+ '; '.join(lines)+'. O programa não foi executado; isso não prova correção funcional.'

def review_reply(reply,text,cfg,model_call):

    results=verify_reply(reply,text,cfg)
    generation=any(word in text.lower() for word in ('escreva','crie','implemente','faça','faca','programa','código completo','codigo completo'))
    failures=[r for r in results if r['status']=='FAIL']
    if not generation or not failures or cfg.get('coding',{}).get('repair',True) is False:return reply,results
    diagnostic='\n'.join(r.get('diagnostic','')[:4000] for r in failures)
    prompt=('Pedido original: '+text+'\nResposta candidata:\n'+reply[:24000]+'\nDiagnóstico real do compilador:\n'+diagnostic+
            '\nCorrija os erros mantendo todos os requisitos, funções, plataformas e dependências. Não remova funcionalidades para compilar. '
            'Retorne a resposta completa com o código corrigido. Não alegue testes funcionais: o verificador só compila e não executa.')
    try:corrected=model_call(prompt,cfg,'Revisão técnica de código a partir de diagnóstico real.'+instructions(text,cfg))
    except Exception:return reply,results
    blocks=code_blocks(corrected)
    if not blocks:return reply,results
    names=lambda value:set(re.findall(r'(?m)^\s*(?:async\s+)?(?:def|class|fun)\s+([A-Za-z_][A-Za-z0-9_]*)', '\n'.join(code for _,code in code_blocks(value)))) | set(re.findall(r'(?m)^\s*(?:[A-Za-z_][\w:&<>*,]*\s+)+([A-Za-z_][\w:]*)\s*\([^;{}]*\)\s*(?:const\s*)?\{','\n'.join(code for _,code in code_blocks(value))))
    if not names(reply)<=names(corrected):return reply,results
    second=verify_reply(corrected,text,cfg)
    if not second or any(r['status']!='PASS' for r in second):return reply,results
    return corrected,second
