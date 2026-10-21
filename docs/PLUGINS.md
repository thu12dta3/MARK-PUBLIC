# Plugins do MARK

O painel **Ferramentas → Plugins** administra os conectores que o MARK já consegue usar. Cada integração tem uma chave independente em `plugins` no arquivo privado `~/.config/mark/config.json`. O painel altera apenas essa chave, salva o arquivo atomicamente com permissão `0600` e não mostra nem copia segredos.

## Conectores disponíveis

| Plugin | O que permite | Limites |
| --- | --- | --- |
| Internet e páginas | Pesquisa web e leitura de páginas públicas enviadas por link | O leitor não executa scripts nem downloads; conteúdo externo é tratado como dado, não como instrução |
| GitHub | Leitura de repositórios e uso da interface de edição no PC | Credenciais ficam no GitHub CLI; gravação é uma ação separada com confirmação e revisão |
| God’s Eye View | Consultas geográficas e visualização local iniciada sob demanda | Ferramentas passam pela allowlist e validação existentes; desligado, não aceita comandos do chat nem abre a tela do PC |

Desativar um plugin bloqueia seu uso pelo chat e pelos painéis GitHub e God’s Eye View no PC. Os novos valores são lidos da configuração privada a cada decisão de acesso, então não exigem reiniciar os serviços nem desconectam outros recursos.

## Código de terceiros

Esta versão **não instala nem executa plugins arbitrários**, scripts, wheels ou comandos MCP fornecidos por terceiros. Não existe uma loja pública conectada. Os itens do painel são adaptadores nativos revisados, cada um com um conjunto de permissões visível. Isso evita tratar manifesto, nome ou descrição de pacote como prova de segurança. Uma futura importação de conectores externos precisa acrescentar isolamento de processo, validação das ferramentas e autorização individual para operações com efeitos antes de ser habilitada.

## Implementação

- Catálogo e gravação segura: `src/mark/plugins.py`.
- Painel desktop: `src/mark/desktop_plugins.py`, aberto por **Ferramentas → Plugins**.
- Bloqueios efetivos: `src/mark/brain.py`, `src/mark/github_assistant.py`, `src/mark/web_access.py`, `src/mark/mobile_api.py` e `src/mark/desktop.py`.
- Preferências são locais; o catálogo, descrições e código dos adaptadores ficam versionados neste repositório.
