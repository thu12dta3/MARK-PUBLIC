# Instalação e execução (Garuda/Arch Linux)

## Componentes externos

- A janela do computador usa PyQt6 no Python do sistema (`/usr/bin/python3`). No Arch/Garuda, o pacote é `python-pyqt6`; o atalho instalado no menu chama o iniciador `scripts/mark-desktop.sh`.
- `ffmpeg` fornece ffmpeg/ffplay e captura PulseAudio, RTSP e reprodução.
- `whisper.cpp` deve disponibilizar `whisper-cli` no PATH.
- Ollama deve estar ativo em `http://127.0.0.1:11434` com os perfis de chat (`qwen3-vl:4b`) e voz (`qwen3-vl:4b-instruct`) disponíveis.
- Nesta conta, a unidade do usuário `~/.config/systemd/user/ollama.service` inicia junto da sessão e usa `~/.local/share/mark/ollama-models`. Confira com `systemctl --user status ollama.service`; para iniciar manualmente, use `systemctl --user start ollama.service`.
- Não inicie ao mesmo tempo outra unidade do Ollama na porta `11434`. Em outras instalações sem unidade do usuário, use apenas o serviço Ollama já configurado no sistema.
- O núcleo usa `~/.local/share/mark/core-env` (DDGS está instalado ali). Piper usa o ambiente existente `~/.local/share/mark/piper-env` e os arquivos de voz em `~/.local/share/mark/voices`. O serviço Ollama da sessão usa `~/.local/share/mark/ollama-models`, onde está a variante instruct.
- `espeak-ng` é fallback de voz. DDGS é opcional; o MARK continua sem busca se não estiver instalado no Python do launcher.
- PulseAudio ou PipeWire com compatibilidade PulseAudio deve expor a fonte `default`.
- `MARK --audio-devices` mostra nomes de entradas e saídas e as rotas padrão sem abrir microfone. `MARK --audio-source NOME --audio-sink NOME` seleciona rotas apenas para aquela execução; para fixá-las, use `audio.source` e `audio.sink` em `~/.config/mark/config.json`. Em fones Bluetooth, habilite HFP/HSP no seletor de perfil de som do desktop para usar o microfone do fone. O perfil pode reduzir a qualidade estéreo enquanto o microfone está em uso, conforme o próprio Bluetooth.

Pacotes Arch usuais incluem `ffmpeg`, `espeak-ng` e `python`; whisper.cpp e Ollama podem vir de pacote ou instalação local. Confira `whisper-cli --help` e `ollama list`.

## Modelos e vozes existentes

Whisper wake word: `~/.local/share/mark/ggml-base.bin` (base, busca wake word). Comandos: `~/.local/share/mark/ggml-small.bin` (small). Piper: voz pt-BR Faber e inglês Lessac no diretório acima. A variante anterior de raciocínio continua armazenada em `/var/lib/ollama` como reserva, mas o serviço ativo usa apenas a variante instruct.

## Configuração privada

Arquivo ativo: `~/.config/mark/config.json`, criado na migração com permissões 0600. Ajuste as chaves `camera` nesse arquivo; nunca coloque credenciais em scripts, logs ou no Git. `MARK_CAMERA_URL` pode substituir a URL integral. `MARK_OLLAMA_URL`, `MARK_WEB=0` e `MARK_DEBUG=1` também são aceitos.

`config/config.example.json` documenta o formato e contém apenas valores ilustrativos. Ajuste caminhos de usuário se instalar em outro local.

## Diagnóstico

Rode `MARK --check` para verificar dependências locais, modelos, vozes, o módulo Piper, a API do Ollama e o modelo padrão selecionado. Esse diagnóstico é rápido e não ativa câmera nem microfone.

Para testes modulares, use `~/MARK/scripts/test_unit.sh`, `test_ollama.sh`, `test_tts.sh`, `test_whisper.sh arquivo.wav`, `test_camera.sh` e `test_microphone.sh`. O teste integrado `test_integration.sh` consulta o roteador/Ollama e fala a resposta pelo Piper. Os testes de câmera e microfone precisam dos dispositivos correspondentes; o teste de câmera usa o stream RTSP/RTSPS configurado e remove a imagem temporária depois.

Para câmeras Intelbras, o protocolo RTSP simples pode ser configurado com `scheme: "rtsp"`; confirme porta e caminho específicos do modelo. Muitos modelos usam a porta 554 e o caminho `/cam/realmonitor?channel=1&subtype=1` para o stream secundário. Mantenha usuário e senha apenas em `~/.config/mark/config.json`, com permissão `0600`.
