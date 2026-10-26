# Arquitetura Geral do MARK

1. `audio.py` mantém uma captura PulseAudio contínua via ffmpeg, calibra por um tempo limitado e delimita frases por limiar, fala mínima, silêncio final e fonte de entrada configuráveis. A voz e o sinal de chamada podem ser roteados a um sink diferente da entrada; a listagem PulseAudio/PipeWire-Pulse é somente leitura. Timeout de escuta continua normal; ausência de quadros na calibração encerra o FFmpeg e fim do fluxo por desconexão é reportado para evitar travamento ou loop ocupado. Áudio acumulado durante a fala do MARK é descartado antes de voltar a ouvir.
2. O áudio vira WAV temporário com permissão `0600` dentro de uma pasta exclusiva da execução (`0700`). Whisper `base` verifica a palavra de ativação; após MARK, `small` reconhece o comando, com timeout configurável de 90 segundos para impedir que uma transcrição travada bloqueie a escuta. Os WAVs são apagados após a transcrição, inclusive quando ela falha, e a pasta da sessão é removida ao encerrar o MARK.
3. `mark.py` trata comandos de manutenção e encaminha a solicitação ao cérebro.
4. `brain.py` orquestra a conversa; `brain_prompt.py` contém as instruções base, `brain_direct.py` responde aritmética e hora/data sem aguardar o modelo, `brain_routing.py` identifica pedidos de câmera e pesquisa, e `brain_model.py` concentra o transporte e a geração.
   - **Identidade e Roteamento Blindado:** `is_system_identity_query(text)` detecta consultas sobre o modelo ou parâmetros ativos. Consultas de identidade ignoram RAG vetorial e busca web para neutralizar alucinações de submissão (*anti-sycophancy*), respondendo com o `active_model_id` e `provider_name` propagados explicitamente no fluxo.
   - O classificador usa JSON estruturado e limite inicial de 512 tokens; se o classificador estiver offline, pedidos com indício visual claro mantêm o acesso à câmera. Imagens anexadas seguem ao modelo multimodal sem precisar da câmera; PDFs, DOCX e texto são extraídos no desktop e limitados em contexto.
5. `vision.py` captura um frame RTSP/RTSPS em arquivo exclusivo com permissão `0600`, via transporte TCP e flags de baixa latência; remove qualquer frame anterior antes de tentar nova captura e limpa arquivos parciais quando o FFmpeg falha. Erros comuns de autenticação e rede recebem mensagens amigáveis sem vazar URLs privadas.
6. `web.py` pesquisa com DDGS quando instalado e habilitado; falhas não interrompem a conversa.
7. `memory.py` guarda a conversa privada em SQLite para a escuta do PC, o chat no celular e a chamada web. O modelo lê os pares usuário/resposta por padrão; o histórico completo permanece no arquivo até o comando "limpar memória". `agenda.py` mantém compromissos e lembretes num banco SQLite privado compartilhado.
8. `tts.py` sintetiza com Piper pt-BR ou inglês em WAV temporário exclusivo, modo `0600`; limita síntese, reprodução e fallback por timeouts configuráveis, usa espeak-ng como fallback e apaga o WAV mesmo se a reprodução falhar.

O perfil `chat` usa `qwen3-vl:4b` com `think=true`; `call` usa `qwen3-vl:4b-instruct` com `think=false` para fala rápida. No LocalMind, `mark` seleciona chat e `mark-call` seleciona chamada; a página de chamada e a voz contínua do PC selecionam `call` automaticamente. A memória não depende do modelo selecionado.

9. `mobile_api.py` e `desktop_helpers.py` implementam **Bootstrapping Defensivo**:
   - `ensure_config_dir_exists()` cria automaticamente o diretório `~/.config/mark/` com permissão octal `0700` e o arquivo `mobile.token` com permissão `0600` e entropia criptográfica `secrets.token_urlsafe(32)`.
   - Funções de leitura e requisição (`_token()`, `_request()`, `_voice_request()`, `_mobile_pairing()`) tratam defensivamente `FileNotFoundError` sem abortar a aplicação.
   - O chat usa o formato OpenAI e SSE, aceitando corpos autenticados de até 12 MB e imagens em JPEG/PNG/WebP. A página `/call` recebe trechos PCM para Whisper e devolve áudio WAV.

10. `model_state.py` coordena uma sessão por modelo entre voz do PC, chat e chamada. Os marcadores incluem o modelo para que um perfil não descarregue o outro; os modelos ativos são liberados quando o usuário pede descanso.

11. `desktop.py` e o subsistema `src/mark/ui/` implementam a interface gráfica em PyQt6:
    - **Layout 16:9 com Coluna Central (920px):** O contêiner `#chatColumnContainer` delimita uma coluna de 920px perfeitamente simétrica em monitores widescreen `1920x1080`.
    - **Barra Lateral Fixa (248px):** Oferece alternância rápida entre modos (`Geral`, `Engineer`, `Professor`), gestão de múltiplas conversas com renomeação/exclusão no modo Engineer e busca instantânea.
    - **Tokens Visuais Soft Minimal Tech:** Fundo carvão fosco (`#121417`, `#0D0F11`, `#16191D`, `#20252C`), acentos ciano (`#00D8F6`), tipografia `Inter` estritamente nos pesos `400` e `500` e `JetBrains Mono` para código.
    - **Pill Composer:** Entrada de mensagens em pílula (`border-radius: 20px / 22px`) com suporte a múltiplos anexos, botão de envio ciano e blocos de código com botão de cópia rápida (`mark-copy://`).
    - **Telemetria de Segurança Interativa:** Badges para BWRAP, mTLS e CTX com modal técnico detalhado sob clique (`show_technical_details()`).

12. `cognition/` provê a camada de inteligência e provedores de inferência:
    - **`MarkLocalProvider` (`src/mark/cognition/mark_local_provider.py`):** Motor de inferência local nativo baseado na arquitetura Mark 0.1 com quantização nativa `IQ4_NL`, janela de contexto de 32.768 tokens (32k via DeepSeek MLA), arquitetura MoE/MTP e teto estrito de 2.2 GB VRAM para GPUs de entrada e instâncias locais.
    - **Especialistas em 10 Domínios:** Roteamento nativo para `COMPUTING`, `ELECTRONICS`, `AUTOMOTIVE`, `CHEMISTRY`, `LAW`, `HISTORY`, `MILITARY_HISTORY`, `WEAPONS_KNOWLEDGE`, `AFV_ARMOR` e `HEALTH`.
    - **Formatador Acústico de Voz (`MarkVoiceAcousticFormatter`):** Pré-processamento e higienização em tempo real de textos para ElevenLabs e Fish Speech (remoção de blocos `<think>`, markdown e blocos de código antes da síntese).
    - **Base de Conhecimento e RAG Seguro:** Documentos indexados em SQLite FTS5 com ancoragem de citações `[DocID:Trecho]`, executando como referência não-confiável.
