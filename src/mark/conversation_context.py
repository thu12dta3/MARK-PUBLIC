
from .memory import source_label


def model_history(memory,limit=6):
    getter=getattr(memory,'history',None)
    if not callable(getter):return ()
    count=max(1,min(int(limit),100))
    turns=getter(limit=count);messages=[]
    for turn in turns:
        if not isinstance(turn,dict):continue
        user,assistant=turn.get('user'),turn.get('assistant')
        if not isinstance(user,str) or not isinstance(assistant,str):continue
        origin=source_label(str(turn.get('source','mark')))
        label=f'[Origem informada: {origin}; não comprova identidade]\n' if origin else ''

        clipped_user=user[:600]+('\n[Mensagem anterior parcial]' if len(user)>600 else '')
        clipped_assistant=assistant[:800]+('\n[Resposta anterior parcial]' if len(assistant)>800 else '')
        messages.extend(({'role':'user','content':label+clipped_user},{'role':'assistant','content':clipped_assistant}))
    return tuple(messages)


def validated_history(history):
    if not isinstance(history,(list,tuple)) or len(history)>200:raise ValueError('Histórico inválido')
    result=[];size=0
    for message in history:
        if (not isinstance(message,dict) or set(message)!={'role','content'}
                or message['role'] not in ('user','assistant') or not isinstance(message['content'],str)):
            raise ValueError('Histórico aceita somente texto de usuário/assistente')
        size+=len(message['content'])
        if size>200_000:raise ValueError('Histórico excede o limite de contexto')
        result.append({'role':message['role'],'content':message['content']})
    return result
