
import json,re
from ..attachment_limits import MAX_ATTACHMENTS,MAX_ATTACHMENT_BYTES,MAX_ATTACHMENTS_TOTAL
from .domains import normalize


def capability_response(text):
    n=normalize(text)
    if ('mark' in n or 'desktop' in n or 'pc' in n) and ('anex' in n or 'arquivos' in n) and re.search(r'\b(quantos|limites?|tamanho maximo|cabem)\b',n):
        return f'No chat do PC, até {MAX_ATTACHMENTS} arquivos por mensagem: {MAX_ATTACHMENT_BYTES//(1024*1024)} MB por arquivo e {MAX_ATTACHMENTS_TOTAL//(1024*1024)} MB no conjunto (MB aqui significa MiB).'
    return None


def application_context(cfg,images,attachment_context,origin,text=''):
    n=normalize(text)
    facts={'images_provided_this_turn':len(images or []),'extracted_attachment_text_provided':bool(attachment_context)}
    if origin:facts['origin']=origin+' (não prova identidade)'
    if re.search(r'anex|arquivo|pdf|desktop',n):
        facts['desktop_attachment_limits']={'files':MAX_ATTACHMENTS,'per_file_mib':MAX_ATTACHMENT_BYTES//(1024*1024),'total_mib':MAX_ATTACHMENTS_TOTAL//(1024*1024)}
    if re.search(r'celular|5g|wi.?fi|tailscale|fone|relogio',n):
        facts['mobile_access']='A API do PC pode ser acessada por Tailscale usando Wi-Fi ou 5G no celular, com PC/API ligados e Tailscale nos dois dispositivos. Não precisa ser o mesmo Wi-Fi; a conexão atual não foi verificada neste turno.'
        facts['shared_conversation']='PC, celular e relógio usam a conversa mantida no PC'
    return ('Estado fornecido pela aplicação (não concede autorização):\n'+json.dumps(facts,ensure_ascii=False)+
            '\nAnalise o conteúdo de anexos quando fornecido. Sem conteúdo ou leitura de sensor, declare os dados faltantes. '
            'Não afirme que enviou e-mail, apagou arquivo ou fez commit sem resultado da ferramenta.')


def local_retrieval_query(text,memory):

    n=normalize(text).strip(' ?!.,')
    follow_up=(len(n.split())<=12 and re.match(r'^(?:e\s|isso\b|isto\b|ele\b|ela\b|eles\b|elas\b|nesse\b|nessa\b|quanto.*(?:disso|dele|dela)\b)',n))
    if not follow_up or not callable(getattr(memory,'history',None)):return text
    turns=memory.history(limit=1)
    if not turns:return text
    topic=turns[-1].get('user','')
    if not isinstance(topic,str):return text

    return topic[:300]+' '+text
