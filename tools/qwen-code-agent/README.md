# Qwen Code Agent

Agente local de programação para o LM Studio. Usa o modelo já instalado `qwen2.5-coder-7b-instruct` pela API local `http://127.0.0.1:1234/v1`; não instala nem baixa modelos.

## Arquivos

- `agent.py`: interface, histórico da conversa, ferramentas e acesso ao workspace.
- `web_access.py`: busca pública via DuckDuckGo e leitura de páginas HTTP/HTTPS.
- `askpass.py`: autenticação temporária do Git para leitura de repositórios privados usando uma sessão `gh` já autenticada. O token permanece em variável de ambiente do processo e não é incluído na URL nem exibido.
- `icon.png`: ícone do aplicativo.
- `vendor/tkinterdnd2/`: dependência de arrastar e soltar (binário Linux x64). Avisos e licença em `vendor/`.
- `tests/`: testes locais do plano, caminhos, aprovações, backups, links e navegação web.

## Interface

A interface usa tema grafite com acentos lavanda, navegação lateral compacta, coluna de conversa centralizada e balões arredondados para mensagens do usuário. As respostas do assistente ficam sem cartões pesados; o campo de mensagem é destacado e a rolagem automática acompanha a conversa.

O agente mantém configuração, histórico e memória em pastas locais do usuário, fora deste repositório:

- `~/.local/share/lmstudio-qwen-agent/config.json`
- `~/.local/state/lmstudio-qwen-agent/projects/<id>/`: histórico, memória, planos e backups dos arquivos editados.
- `~/.local/state/lmstudio-qwen-agent/backups/`: backups da instalação do app.

## Instalação local

Copie `agent.py`, `web_access.py`, `askpass.py`, `icon.png` e a pasta `vendor/tkinterdnd2/` para `~/.local/share/lmstudio-qwen-agent/`. Preserve qualquer `config.json` já existente. Em seguida, execute `python3 ~/.local/share/lmstudio-qwen-agent/agent.py` ou use um atalho `.desktop` com esse comando.

O requisito é Python 3 com Tkinter instalado no sistema. Para o uso de GitHub privado, instale o `gh` e faça login localmente com `gh auth login`. O agente só lê arquivos de repositório fornecido pelo usuário; não cria commits nem envia alterações.

## Fluxo de trabalho

1. Inspeciona o pedido, as instruções `AGENTS.md`, arquivos e estado Git antes de propor mudanças.
2. Em tarefas com várias etapas, exibe um plano curto na barra lateral e atualiza o andamento.
3. Mostra um diff e pede aprovação antes de gravar arquivo. Uma cópia da versão anterior é guardada no estado privado do workspace.
4. Pede aprovação para cada comando. O comando inicia no workspace e sem shell, mas usa as permissões da conta do usuário; portanto, pode alcançar outros arquivos do computador.
5. Mantém conversas, memória e planos arquivados separados por workspace. O histórico e a memória ficam na máquina.

## Limites e privacidade

- Ferramentas de listar, ler, buscar e editar ficam dentro do workspace escolhido e recusam links simbólicos.
- O executor de comandos não é um sandbox do sistema operacional. Aprove apenas comandos que você entenda.
- A leitura GitHub clona temporariamente em diretório temporário, sem checkout e sem executar hooks; arquivos são lidos com Git e limitados a 256 KiB. A autenticação depende do `gh` já autenticado.
- A busca envia os termos ao DuckDuckGo. A navegação aceita páginas públicas HTTP/HTTPS e bloqueia destinos locais/privados, esquemas não web e portas diferentes de 80/443. Cookies e credenciais do navegador não são enviados.
- As edições são apresentadas como diff, mas o modelo propõe o conteúdo final do arquivo, em vez de gerar patches incrementais.

## Testes

Na raiz do repositório:

```sh
python3 -m unittest discover -s tools/qwen-code-agent/tests -v
```
