# Configuração

O arquivo ativo fica fora do projeto em `~/.config/mark/config.json` e deve ter permissão `0600`. Não coloque senhas no exemplo, nos scripts, nos logs ou no Git. `config.example.json` contém apenas valores ilustrativos; não o copie por cima de uma configuração já existente.

## Câmera

Configure uma destas formas:

- Campos separados: `scheme`, `host`, `port`, `path`, `username` e `password`.
- URL integral em `url`; quando preenchida, ela prevalece sobre os campos separados.

`scheme` aceita `rtsp` ou `rtsps` e, se omitido, mantém o padrão `rtsps` para compatibilidade. Sem uma porta explícita, o MARK usa `554` para RTSP e `8554` para RTSPS. `tls_verify` controla a verificação TLS; `timeout` limita a captura. `MARK_CAMERA_URL` substitui a URL integral durante a execução. O caminho RTSP varia por modelo: confirme-o no manual do dispositivo.

## Demais opções

- `models.chat` escolhe o Qwen para chat normal (`qwen3-vl:4b`, com raciocínio); `models.call` escolhe a variante rápida de voz (`qwen3-vl:4b-instruct`). `thinking` configura esse comportamento por perfil. Ambos usam a mesma memória. `ollama.url`, `ollama.timeout` e `ollama.num_predict` controlam o serviço; `ollama.model` continua como compatibilidade e perfil padrão. As sessões são controladas separadamente por modelo e uma despedida natural do usuário libera os modelos ativos.
- A agenda fica em `~/.local/state/mark/agenda.sqlite3`, modo privado, e é comum a todos os dispositivos ligados ao MARK. Os lembretes vencidos viram notificações do Linux enquanto `mark-mobile.service` estiver ativo; o worker de notificações não carrega o Qwen.
- `audio` controla frequência, limiar, calibração, fala mínima, silêncio final e duração máxima. `audio.source` escolhe a entrada PulseAudio/PipeWire-Pulse (padrão `default`); `audio.sink` escolhe a saída de voz (vazio usa a saída padrão). Também podem ser definidos por execução com `MARK_AUDIO_SOURCE` e `MARK_AUDIO_SINK`, ou pela linha de comando `MARK --audio-source NOME --audio-sink NOME`.
- `whisper` aponta para os modelos de wake word e comandos; `timeout` limita cada transcrição (padrão: 90 segundos).
- `tts` aponta para o ambiente Piper e vozes pt-BR/inglês. `synth_timeout`, `playback_timeout` e `fallback_timeout` limitam, respectivamente, a síntese, a reprodução e a voz alternativa (padrões: 60, 180 e 60 segundos).
- `web.enabled` habilita ou desabilita pesquisa DDGS; `web.timeout` define o limite de segundos por requisição (padrão: 5); `MARK_WEB=0` desabilita sem alterar o arquivo.
- `memory.max_messages` limita a memória recente em processo.

`MARK_CONFIG` seleciona outro arquivo de configuração; `MARK_OLLAMA_URL` substitui o endereço do Ollama. `MARK_DEBUG=1` habilita detalhes de diagnóstico, inclusive transcrições locais. Use esse modo apenas quando necessário. `MARK --audio-devices` lista entradas, saídas e rotas padrão sem ativar o microfone. Para usar o microfone do Bluetooth, selecione o perfil de chamadas (HFP/HSP) nas configurações de som do sistema; o MARK não altera o perfil Bluetooth global automaticamente.
