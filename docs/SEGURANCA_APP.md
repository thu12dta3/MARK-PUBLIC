# Verificações de segurança do aplicativo de área de trabalho

## Controles conferidos

- A chave da API é lida de `~/.config/mark/mobile.token` sem seguir links simbólicos. O app exige arquivo regular, pertencente ao usuário e sem permissões para grupo/outros (`0600`). A chave não é colocada em campos visíveis, URLs, mensagens ou argumentos de processos.
- As chamadas HTTP do app apontam para `127.0.0.1:8765` e incluem a chave no cabeçalho `Authorization`. A publicação remota continua responsabilidade do Tailscale Serve.
- O histórico é aberto em modo somente leitura no arranque e só se o arquivo pertencer ao usuário e tiver permissões privadas. A limpeza de memória usa a API existente e pede confirmação antes de apagar o histórico compartilhado.
- Texto da conversa é escapado antes de ser apresentado como rich text Qt, para que conteúdo enviado pelo usuário não vire marcação ou imagem HTML.
- Email abre somente um rascunho `mailto:` no cliente padrão. O usuário revisa e envia no Tuta; o MARK não armazena senha nem envia automaticamente.
- Controle de serviços chama `systemctl --user` com uma lista fixa de argumentos, sem shell. O teste da câmera chama um script do próprio projeto, também sem shell.
- O atalho do menu define `Terminal=false`, aponta para o inicializador do MARK e usa o ícone SVG do projeto. A interface não abre uma janela separada de X11/Wayland; na sessão atual, o Qt escolhe Wayland diretamente e usa o compositor já ativo no KDE.

## Verificações executadas

- `tests/test_desktop_security.py`: oito casos aprovados com `/usr/bin/python3` e PyQt6, incluindo envio de uma pergunta pela janela com resposta simulada.
- Suíte comum do MARK: 108 casos aprovados; o módulo gráfico foi ignorado dentro do ambiente virtual do núcleo, que não inclui PyQt6. Os cinco casos gráficos foram executados à parte com o Python do sistema.
- `desktop-file-validate`, compilação sintática Python e inicialização gráfica sem terminal: aprovados.
- A tela foi renderizada em modo de diagnóstico e inspecionada: paleta escura, saudação inicial e ícone carregado.

Estas verificações cobrem o código e a configuração do app local. Não são uma auditoria de segurança independente nem uma análise das dependências do sistema.
