# Domínios e modos

O registro inicial inclui COMPUTING, ELECTRONICS, AUTOMOTIVE, CHEMISTRY, LAW, HISTORY, MILITARY_HISTORY, WEAPONS_KNOWLEDGE, AFV_ARMOR e HEALTH. HEALTH é rota preparada, sem biblioteca clínica importada. GENERAL_CHAT é o fallback. Os aliases `afv` e `weapons` são aceitos na ingestão. A expansão requer definição versionada no registro e compatibilidade dos schemas; não há importação arbitrária de plugins.

`route_domains` retorna domains, confidence, reason, intent e thinking_mode. A confiança é uma heurística de palavras/expressões, não probabilidade calibrada. Sem indícios, retorna confiança zero e deixa o núcleo responder. Não usa inferência extra em perguntas comuns.

“Corrosão galvânica num carro” seleciona CHEMISTRY + AUTOMOTIVE; “eletrônica de fire control de dois blindados” seleciona ELECTRONICS + AFV_ARMOR. Roteamento por domínio não substitui o roteador atual de câmera/web. A integração fornece orientações específicas, como pedir ano/motor/mercado quando necessário, conferir datas legais e distinguir protótipo de produção.

FAST existe concretamente no caminho de calculadora determinística. NORMAL usa o modelo atual para a resposta; DEEP é conceito/interface preparada e não inicia múltiplas inferências automaticamente. DEVICE_STATUS e TOOL_ONLY continuam atendidos pelos caminhos locais existentes; não há um novo classificador treinado.
