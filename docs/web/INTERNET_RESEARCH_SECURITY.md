# Pesquisa pública e segurança do MARK

## Diagnóstico

O caminho de pesquisa era `brain.answer → brain_routing.route → web.search → DDGS`. A busca só era iniciada quando uma lista estreita de expressões era reconhecida pelo roteador. Pedidos atuais formulados de outra maneira podiam cair no classificador local; se ele respondesse `web=false`, o MARK não fazia consulta, mesmo que a resposta do modelo dissesse que buscaria fontes. A aplicação não guardava prova de execução da ferramenta.

Mesmo quando a busca funcionava, o MARK usava apenas os resumos do DDGS. Não abria as páginas encontradas, não verificava o texto no site de origem e não anexava uma lista de links gerada a partir dos resultados executados. A tela de falha mencionada no prompt não veio como arquivo de imagem neste pacote; a causa foi reproduzida a partir do código e dos testes de roteamento, sem alegar que a captura exata foi reproduzida.

## Fluxo implementado

1. O roteador identifica localmente pedidos atuais comuns e pedidos explícitos de pesquisa; consultas ambíguas ainda podem usar o classificador. Pedidos por documentação oficial do Python restringem a busca a `docs.python.org` e priorizam resultados que mencionem o assunto pedido.
2. O buscador DDGS roda com até duas tentativas somente para falhas transitórias. Resposta vazia, payload inválido e falha de rede ficam separados.
3. O MARK valida esquema, endereço DNS público e porta de cada URL antes de usá-la. Abre no máximo duas das cinco fontes, limita tempo e tamanho e valida todo redirecionamento novamente.
4. O leitor extrai HTML/texto/JSON estaticamente. Script, estilo e elementos equivalentes são ignorados. O modelo recebe os resultados como dados externos não confiáveis; eles não são registrados como instruções nem autorizam ferramentas.
5. A resposta final recebe links somente das fontes retornadas pela busca real. Falha de busca não é apresentada como busca vazia nem permite inventar resposta atual.
6. O estado operacional pode ser lido em `/health` autenticado: disponibilidade do pacote, execução mais recente, falha sem mensagem ou URL privada, total de resultados e páginas abertas. Nenhuma consulta é guardada.

## Modelo e chamadas de ferramenta

O MARK não depende de tool calling nativo do modelo para pesquisar. A aplicação executa o protocolo de pesquisa com limite fixo de resultados e páginas, então passa os dados ao modelo Geral configurado para síntese. Isso funciona com o modelo local configurado sem presumir que o identificador do modelo prove suporte a ferramentas. A especialização Engineer continua usando o perfil Engineer; chamadas usam o perfil rápido; imagens ficam no perfil multimodal Geral; documentos extensos usam a Biblioteca/RAG local junto ao modelo Geral. A seleção é por perfis locais já configurados, sem baixar ou deixar modelos adicionais residentes.

## Proteções

- Consultas com sinais claros de credenciais, caminhos locais, e-mail ou dados pessoais não são enviadas ao buscador. O texto da consulta nunca é registrado.
- URLs com credenciais embutidas ou tokens comuns na query são rejeitadas.
- DNS é resolvido antes da conexão e o socket é fixado no IP público validado, reduzindo risco de DNS rebinding e acesso a rede privada.
- TLS mantém verificação padrão de certificado.
- Proxies HTTP configurados no sistema são suportados mantendo o destino preso ao IP validado; credenciais do proxy não são encaminhadas ao site HTTPS. Proxy HTTPS e SOCKS não são suportados e não são contornados.
- O MARK não executa código da página, não contorna login, paywall, CAPTCHA ou controles de acesso, e não inicia ações cibernéticas ofensivas.
- Ferramentas de cálculo e conhecimento continuam read-only; tarefas de edição do workspace exigem concessões com escopo e operações registradas.

## Dependências e limites atuais

- Dependência já declarada: `ddgs>=9.0`; instalada no ambiente operacional do MARK. Sem ela, `/health` informa provedor indisponível e a consulta falha claramente.
- Busca atual usa DDGS como agregador sem chave. Se o serviço bloquear/limitar consultas, o MARK reporta HTTP 429, falha TLS, DNS, timeout, rede ou provedor indisponível; não há uma segunda chave de busca configurada.
- Leitura é HTML/texto/JSON estática; páginas que dependem de JavaScript, PDFs, sessões autenticadas e páginas protegidas não são extraídas. Não foi instalada uma engine de navegador nem contornado controle algum.
- O leitor padrão aceita somente HTTP/HTTPS nas portas 80/443, limita respostas a 1,5 MB, texto a 16 mil caracteres e segue até cinco redirecionamentos.
- A heurística de privacidade é conservadora, mas não é anonimização perfeita; ela verifica somente padrões conhecidos. Dados pessoais que não correspondam aos padrões não podem ser identificados com certeza.

## Verificação

Testes mockados verificam roteamento de fatos atuais, execução do buscador, abertura das páginas, links derivados de resultados reais, preferência por documentação primária quando pedida, distinção entre vazio/falha/formato inválido, bloqueio de dados privados, redação de logs, prompt injection sem despacho de ferramenta e fallback de rede. O teste de internet real consulta a documentação oficial do Python, abre `docs.python.org` por HTTPS com verificação TLS, extrai o aviso “Novo na versão 3.4” e sintetiza a resposta com o modelo local ativo. Essa prova usa a rede pública real e o runtime local; fixtures não são contadas como prova de internet.

## Ainda não concluído

Não há suporte a página JavaScript, PDF ou sessão autenticada. O painel do app PC ainda não apresenta um cartão visual para todos os campos de `/health`; os diagnósticos estão disponíveis no endpoint local autenticado. O estado continua parcial até validar a instalação sobre todos os perfis da API e confirmar a navegação real após o reinício.
