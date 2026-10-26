# Registro de Atualizações e Evolução Arquitetural — MARK
**Versão Pública:** v1.1.0  
**Data da Edição:** Outubro de 2026  
**Classificação:** Documentação Técnica Pública  
**Repositório:** [https://github.com/thu12dta3/MARK-PUBLIC](https://github.com/thu12dta3/MARK-PUBLIC)

---

## 1. Visão Geral das Atualizações Recentes

Esta versão reflete um marco significativo na arquitetura, resiliência e maturidade de engenharia do ecossistema **MARK (Modular Augmented Reasoning & Knowledge Assistant)**. As melhorias concentram-se na criação de uma infraestrutura de transporte privada e autônoma (**MARK Link**), na introdução de uma ponte nativa de alto desempenho para o **Model Context Protocol (MCP)** em Rust, no suporte de baixo nível via **JNI** para dispositivos Android/Wear OS e na garantia de estrita tolerância a falhas através de garantias formais de idempotência e reconexão transparente.

Todas as atualizações documentadas foram implementadas sob a metodologia de Desenvolvimento Orientado a Testes (TDD), verificadas com suítes de regressão adversarial e auditadas com ferramentas de análise estática de segurança.

---

## 2. Pilares de Evolução Arquitetural

### 2.1 Blindagem de Rede e Conectividade Resiliente (`MARK Link`)
Anteriormente, a conectividade entre o computador de trabalho e os nós móveis dependia de configurações de VPNs de terceiros ou túneis manuais. O novo subsistema `mark_link` (desenvolvido como daemon nativo em Rust em `src-rust/mark_link` e com orquestração em Python em `src/mark/link`) estabelece uma malha ponto-a-ponto autônoma e segura:

- **Enquadramento Binário `MLNK` e Anti-Fuzzing:** Protocolo de framing próprio sobre TCP com validação estrita de magic bytes (`MLNK`), controle de versões e tamanho máximo delimitado de 4 MB por pacote. Mensagens truncadas, corrompidas ou malformadas são rejeitadas de forma limpa na camada de transporte sem gerar pânico de memória.
- **Autenticação Mútua com Assinatura de Transcrição:** O processo de handshake de 4 vias utiliza pares de chaves efêmeras NIST P-256 (ECDH) e nonces criptográficos de 32 bytes. Cada nó assina a transcrição canônica com sua chave de identidade de dispositivo persistida no hardware seguro (Android Keystore no cliente móvel), neutralizando matematicamente qualquer tentativa de substituição de chaves ou ataque Man-in-the-Middle (MitM).
- **Canais Simétricos Direcionais:** A partir do segredo compartilhado derivado via HKDF-SHA256, o canal estabelece duas chaves independentes AES-256-GCM (Cliente $\to$ Servidor e Servidor $\to$ Cliente). Contadores monotônicos de sequência e janelas deslizantes anti-replay descartam de imediato pacotes repetidos ou atrasados.
- **Hierarquia Multicamadas Dinâmica:** O subsistema descobre e prioriza conexões dinamicamente:
  $$\text{Loopback IPC} \succ \text{Direct LAN Local} \succ \text{Public Reflexive (STUN)} \succ \text{Blind Relay (MRLY)}$$
- **Roteamento por Blind Relay (`MRLY`):** Em ambientes com NAT simétrico ou firewalls corporativos restritivos, o tráfego é retransmitido pelo blind relay. O relay inspeciona apenas o cabeçalho público de endereçamento (`device_id`), sendo incapaz de decifrar o conteúdo protegido ponta-a-ponta. Possui limite de taxa de pacotes e filas limitadas (64 envelopes/par) para proteção contra exaustão de buffer e abuso de proxy aberto.

---

### 2.2 Tratamento de Idempotência e Tolerância a Falhas
A conectividade de dispositivos móveis em redes sem fio (4G/5G/Wi-Fi) é inerentemente instável. Para garantir integridade sem duplicar execuções de ações no host:

- **Tokens de Idempotência para Mutações:** Toda requisição capaz de alterar estado (criação de arquivos, agendamento de tarefas, configuração de periféricos) carrega um identificador único UUIDv4 (`request_id`). O daemon registra o resultado e o status da operação; requisições repetidas causadas por timeout de rede retornam a resposta cacheada sem reexecutar o efeito colateral.
- **Failover Transparente e Retomada de Sessão:** Caso o soquete TCP seja abruptamente encerrado durante a movimentação física do usuário (troca de Wi-Fi para rede celular), a camada de peer gerencia a reconexão automática com backoff exponencial e jitter aleatório de $\pm 20\%$. Ao reconectar, a vinculação de sessão é validada sem reiniciar o handshake completo quando dentro da janela de tolerância.
- **Keep-Alive Adaptativo:** Para poupar a bateria de relógios e celulares, a taxa de envio de heartbeats ajusta-se dinamicamente:
  - *Estado Ativo:* Pings a cada 2 a 5 segundos para medição de RTT e detecção instantânea de queda.
  - *Estado Ocioso (Idle):* Intervalos expandidos para 20 a 30 segundos com jitter, reduzindo em até 75% o número de wakeups da CPU móvel. O canal retorna imediatamente ao modo ativo assim que um novo payload é gerado.
- **Revogação Imediata de Sessões:** Se um dispositivo pareado for desautorizado ou um desafio QR for invalidado, todas as conexões de soquete ativas daquele `device_id` são terminadas imediatamente em nível de kernel, impedindo qualquer transmissão residual.
- **Atualizador Seguro Anti-Downgrade:** O mecanismo de atualização de software (`updater`) valida a integridade do manifesto com assinaturas criptográficas e hashes SHA-256, impedindo reversões maliciosas de versão (ataques de downgrade).

---

### 2.3 Ponte Nativa MCP (Model Context Protocol) em Rust
Para expandir o repertório de ferramentas do MARK sem comprometer a estabilidade do processo principal em Python, foi introduzido o subsistema `mark_core::mcp`:

- **Isolamento Estrito de Capacidades (Zero-Trust):** Cada ferramenta registrada no MCP recebe permissões explícitas:
  - `ReadOnly`: Operações de leitura estrita e diagnóstico, sem permissão de gravação.
  - `WriteMutate`: Operações autorizadas de criação e alteração em diretórios delimitados.
  - `NetworkAccess`: Acesso a chamadas de rede externas controladas.
- **Normalização de Caminhos e Bloqueio de Path Traversal:** O módulo nativo normaliza todos os caminhos de arquivos antes da execução, bloqueando terminantemente sequências como `../`, links simbólicos maliciosos ou tentativas de fuga para fora dos diretórios de trabalho permitidos.
- **Defesa Contra OOM (Out-of-Memory):** Envelopes JSON-RPC e respostas de ferramentas possuem teto de tamanho delimitado (4 MB). Payloads que excedem o limite são descartados antes da alocação de memória do interpretador, prevenindo ataques de negação de serviço.
- **Integração com ToolRegistry da IA:** A ponte `McpBridge` é acoplada diretamente ao registro cognitivo da IA (`mark.cognition.tool_registry`), permitindo que modelos locais e em nuvem descubram e invoquem ferramentas nativas com verificação de tipos e conformidade JSON-RPC 2.0.

---

### 2.4 Ponte JNI e Suporte Mobile Android / Wear OS
- **Integração Nativa Android (JNI):** O código em Rust do `mark_link` expõe símbolos C/JNI padronizados (`br.com.bannaman.markmobile.link.MarkLinkNative`), permitindo compilação cruzada para arquiteturas `arm64-v8a`, `armeabi-v7a` e `x86_64`.
- **Hardware Keystore Binding:** As chaves de assinatura do cliente móvel nunca deixam o chip seguro do dispositivo (Android Keystore / Secure Enclave), garantindo que mesmo um dispositivo comprometido no nível do sistema de arquivos não exponha as chaves de identidade primárias.
- **Interface Reativa em Jetpack Compose:** Aplicativo móvel modernizado com exibição de telemetria em tempo real, status da malha de rede (Direct Local / Relay), leitura de QR Code para pareamento e suporte a microfone contínuo com VAD local.

---

## 3. Benefícios Práticos para Usuários e Desenvolvedores

| Dimensão | Versão Anterior | Versão Atual (v1.1) | Benefício Mensurável |
| :--- | :--- | :--- | :--- |
| **Conectividade Móvel** | Dependência de VPNs externas | Malha nativa ponto-a-ponto MARK Link | Conexão direta com latência $< 1\text{ ms}$ em LAN; sem dependências de terceiros. |
| **Consumo de Bateria** | Pings fixos contínuos | Keep-alive adaptativo com jitter ($\pm 20\%$) | Redução de até $75\%$ nos wakeups de CPU em repouso no celular e Wear OS. |
| **Resiliência a Quedas** | Perda de contexto e erro HTTP | Failover automático e retomada de sessão | Transição invisível entre Wi-Fi e rede móvel 4G/5G sem perda de comandos. |
| **Idempotência** | Risco de duplicação de comandos | Deduplicação por `request_id` (UUIDv4) | Execuções mutativas garantem semântica *exactly-once* mesmo sob retransmissões. |
| **Segurança de Ferramentas** | Execução livre no processo Python | Ponte MCP em Rust com isolamento de capacidades | Proteção contra Path Traversal, exaustão de memória (anti-OOM) e privilégio indevido. |
| **Garantia de Qualidade** | Testes focados em Python | $850+$ testes Python $+ 72$ testes nativos Rust | $100\%$ de taxa de sucesso (`Exit Code 0`) em todas as suítes de validação. |

---

## 4. Auditoria de Segurança e Proteção de Dados

A publicação desta versão cumpre rigorosamente os protocolos de **Zero-Leak**:
1. **Anonimização Total:** Todos os relatórios, especificações de rede e testes unitários utilizam blocos de endereçamento de documentação (RFC 5737 e RFC 3849). Nenhum endereço IP de hosts reais ou dados de ambiente privado foram incluídos.
2. **Isolamento de Segredos:** Nenhum arquivo restrito de desenvolvimento (`MEMORY.md`, anotações de planejamento interno, chaves criptográficas de produção ou tokens) reside neste repositório.
3. **Varredura Estática:** O repositório foi validado contra vazamentos com ferramentas automatizadas de análise de segredos e linters de segurança, mantendo conformidade integral com a licença GNU AGPLv3.

---

## 5. Como Atualizar e Validar Localmente

Para atualizar o seu ambiente para a versão mais recente e executar a validação local completa:

```bash
# 1. Obtenha as atualizações do branch principal
git pull origin main

# 2. Compile e valide as suítes nativas em Rust (72 testes unitários)
cargo test --workspace

# 3. Compile a biblioteca nativa mark_core para Python (opcional para testes nativos)
cd src-rust/mark_core && cargo build --release
cp target/release/libmark_core.so ../../src/mark_core/mark_core.cpython-*.so
cd ../..

# 4. Execute a suíte abrangente de testes Python (1028 testes)
PYTHONPATH=.:src pytest tests/ -v
```

---

## 6. Atualização v1.2.0 — Resiliência de Bootstrapping, Identidade Blindada, MarkLocalProvider e Redesign Executivo 16:9

**Versão Pública:** v1.2.0  
**Data da Edição:** Final de Outubro de 2026  
**Classificação:** Documentação Técnica Pública  

Esta versão consolida a maturidade da experiência de desenvolvimento e execução local do MARK, introduzindo salvaguardas rigorosas de inicialização, eliminação completa de alucinações de identidade, o provedor nativo de inferência local `MarkLocalProvider` e um redesenho de interface no padrão widescreen 16:9.

### 6.1 Bootstrapping Defensivo e Resiliência de Instalação Limpa
- **Criação Automática com Permissões POSIX Estritas:** Adicionada a rotina `ensure_config_dir_exists()`, que cria automaticamente o diretório de configurações (`~/.config/mark/`) com máscara octal restrita `0700` e gera o token de autenticação local (`mobile.token`) com permissão `0600` e entropia criptográfica de 32 bytes (`secrets.token_urlsafe(32)`).
- **Proteção Contra `FileNotFoundError`:** Todas as funções auxiliares de requisições locais (`_token()`, `_request()`, `_voice_request()`, `_mobile_pairing()`) agora tratam graciosamente a ausência transitória de credenciais, retornando estados inativos e mensagens defensivas sem abortar processos em background.
- **Cobertura de Testes:** Validado pela suíte `tests/test_fresh_install_bootstrap.py`.

### 6.2 Identidade e Roteamento Blindado (Bypass Anti-Sycophancy)
- **Detecção Rigorosa de Metadados:** Implementação de `is_system_identity_query()` com regex pré-compilada cobrindo todas as variantes semânticas de perguntas sobre qual modelo ou arquitetura está ativa na conversa.
- **Bypass Atômico de RAG e Busca Web:** Consultas sobre a identidade do sistema ignoram a busca vetorial na biblioteca e resultados externos da web, evitando contaminações de contexto e alucinações induzidas por submissão ao usuário (*anti-sycophancy*).
- **Propagação Transparente de Parâmetros:** Os identificadores `active_model_id` e `provider_name` são propagados integralmente através de `desktop_chat.py`, `mobile_api.py` e `brain.py`, garantindo resposta determinística e auditável sobre o modelo em uso.
- **Higienização de Histórico:** O módulo `conversation_context.py` higieniza declarações de identidade obsoletas em conversas anteriores.
- **Cobertura de Testes:** Validado por `tests/test_chat_identity.py` e `tests/test_hallucination_root_cause.py`.

### 6.3 Arquitetura de Provedor Local (`MarkLocalProvider`)
- **Inferência Nativa Mark 0.1:** Adicionado o adaptador `MarkLocalProvider` (`src/mark/cognition/mark_local_provider.py`) com suporte a quantização `IQ4_NL`, teto estrito de memória de 2.2 GB VRAM para operação fluida em GPUs de entrada e ambientes locais.
- **Janela de Contexto Expandida:** Suporte a 32.768 tokens (32k) via compressão semântica DeepSeek Multi-head Latent Attention (MLA), arquitetura Mixture-of-Experts (MoE) e Multi-Token Prediction (MTP).
- **Roteamento de 10 Domínios Oficiais:** Ativação automática e steering para os domínios: `COMPUTING`, `ELECTRONICS`, `AUTOMOTIVE`, `CHEMISTRY`, `LAW`, `HISTORY`, `MILITARY_HISTORY`, `WEAPONS_KNOWLEDGE`, `AFV_ARMOR` e `HEALTH`.
- **Formatação Acústica de Voz (`MarkVoiceAcousticFormatter`):** Pré-processamento de texto em tempo real para motores TTS como ElevenLabs e Fish Speech, removendo blocos `<think>`, trechos de código e referências de formatação complexas antes da síntese vocal.
- **Diretório Parametrizável de Checkpoints:** Descoberta dinâmica via variável de ambiente `MARK_CHECKPOINTS_DIR` ou fallback seguro para `~/.config/mark/checkpoints/` e `./checkpoints`.
- **Cobertura de Testes:** Validado por `tests/test_mark_local_provider.py`.

### 6.4 Redesign Executivo da Interface Desktop (Widescreen 16:9)
- **Coluna Central Simétrica (920px):** Introdução do `#chatColumnContainer` com largura máxima de 920px perfeitamente centralizado em monitores widescreen 16:9 (`1920x1080`), eliminando assimetrias e garantindo legibilidade executiva.
- **Tokens de Cor Carvão Fosco e Acentos Ciano:**
  - `BG_CANVAS`: `#121417` (fundo principal anti-fadiga)
  - `BG_SIDEBAR`: `#0D0F11` (barra lateral fixa de 248px)
  - `BG_PANEL` / `BG_SURFACE`: `#16191D` e `#1A1D22` (superfícies elevadas)
  - `BG_USER_BUBBLE`: `#20252C` (bolha do usuário elegante e sem bordas duras)
  - `ACCENT_CYAN`: `#00D8F6` (acento luminoso executivo)
- **Tipografia Estrita:** Inter como fonte primária com pesos rigorosamente restritos a `400` (normal) e `500` (médio), e JetBrains Mono para elementos monospaçados.
- **Pill Composer:** Caixa de entrada em formato de cápsula (`border-radius: 20px / 22px`) com botão de envio ciano integrado e visualização em linha única de dicas.
- **Blocos de Código Interativos:** Blocos markdown com cabeçalho de linguagem identificada e botão direto de cópia para a área de transferência (`mark-copy://`).
- **Telemetria de Segurança Interativa:** Badges clicáveis para BWRAP (sandbox Linux namespaces), mTLS e janela CTX acionando diálogo modal com detalhes de segurança (`show_technical_details()`).
- **Gestão de Sessões:** Criação e renomeação de conversas dedicadas no modo Engenheiro diretamente pela barra lateral.
- **Cobertura de Testes:** Validado por `tests/test_ui_components.py` (27 testes de componentes e simetria visual).

---

## 7. Sumário Geral de Validação e Métricas de Qualidade

| Componente | Testes Executados | Taxa de Sucesso | Status |
| :--- | :--- | :--- | :--- |
| **Rust Core (`mark_core`)** | 14 testes | 100% | `PASSED` |
| **Rust Mesh (`mark_link`)** | 58 testes | 100% | `PASSED` |
| **Suíte Python Completa (`pytest`)** | 1028 testes (+ 176 subtestes) | 100% | `PASSED` |
| **Auditoria Zero-Leak** | 0 caminhos privados / 0 credenciais / 0 hardware restrito | 100% | `VERIFIED` |

