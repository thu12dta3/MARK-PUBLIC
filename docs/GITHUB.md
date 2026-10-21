# GitHub no MARK

O MARK acessa o GitHub por meio do programa oficial GitHub CLI (`gh`). A autenticação continua armazenada e administrada pelo `gh`; o MARK não lê, copia nem grava o token.

## Conectar

Instale o GitHub CLI e autorize a conta com `gh auth login --hostname github.com --web --git-protocol https`. Conclua a aprovação no site oficial do GitHub. Depois, abra **GitHub · repositórios e arquivos** no MARK e use **Atualizar lista**.

## Ler e alterar arquivos

1. Escolha um repositório ou informe `dono/nome`.
2. Informe o branch e o caminho de um arquivo de texto existente.
3. Toque em **Abrir arquivo**, revise ou edite o texto e escreva a mensagem do commit.
4. Toque em **Revisar e salvar no GitHub**. A janela mostra um diff e pede confirmação antes de gravar.

O salvamento altera diretamente o branch escolhido usando a revisão exata do arquivo carregado. Se outra pessoa alterar o arquivo antes do salvamento, o GitHub recusa a atualização para evitar sobrescrever a mudança. O editor aceita arquivos UTF-8 de até 1,5 MB; não cria arquivos novos nem abre pull requests.

O MARK só envia mudanças depois da ação e confirmação explícitas na interface. O conteúdo lido fica no editor local e não é enviado ao Qwen automaticamente.

## Skills e MCP

Os MCPs e as skills conectados ao Codex pertencem ao ambiente do Codex e não são automaticamente transferidos para outro aplicativo. Integrações no MARK precisam de suporte próprio, configuração explícita e permissões próprias. O acesso ao GitHub documentado aqui é uma integração nativa inicial, não uma ponte para credenciais ou ferramentas internas do Codex.
