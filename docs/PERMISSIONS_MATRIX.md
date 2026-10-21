# 🛡️ Matriz Formal de Permissões e Capacidades do MARK (Fase P3)

## 1. Visão Geral de Arquitetura e Princípios Reitores

O assistente **MARK** adota um modelo estrito de **Controle de Acesso Baseado em Capacidades (Capability-Based Access Control - CBAC)** associado ao **Princípio do Menor Privilégio (Principle of Least Privilege - PoLP)** e à **Segregação de Funções (Separation of Duties - SoD)**.

Nenhum componente do sistema (seja interface gráfica, agente autônomo ou conector externo) possui permissões implícitas ou irrestritas. Qualquer operação sensível exige a emissão e validação de um `CapabilityToken` assinado criptograficamente via HMAC-SHA256 ou validação explícita pelo `BridgeCapabilityGuard`.

### Taxonomia de Operações
- **R (Leitura / Read):** Capacidade de inspecionar dados na memória volátil, bancos de dados autorizados ou sistema de arquivos isolado.
- **W (Alteração / Write):** Capacidade de criar, modificar ou excluir registros locais, arquivos no espaço de trabalho confinado ou bancos de dados.
- **X (Execução / Execute):** Capacidade de despachar instruções para o interpretador Python, compilar código Rust ou executar processos isolados via Bubblewrap.
- **N (Transmissão de Rede / Network):** Capacidade de estabelecer tráfego de rede TCP/IP através de interfaces autorizadas (Tailscale Serve, TLS 1.3, MQTT seguro).

---

## 2. Matriz Consolidada de Modos, Agentes, Pontes MCP e Conectores

| Componente | Tipo | R | W | X | N | Escopo de Confinamento | Capability / Token Requerido |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |
| **Modo Chat Geral** | Modo UI | ✅ | ❌ | ❌ | ❌ | Memória volátil e histórico SQLite do usuário | Sessão local autenticada |
| **Modo Professor** | Modo UI | ✅ | ❌ | ❌ | ❌ | Biblioteca didática, síntese e explicações | `mode:professor` |
| **Modo Engenheiro** | Modo UI | ✅ | ✅ | ✅ | ❌ | Estritamente restrito a `/workspace` | `engineer.modify`, `sandbox.execute` |
| **Modo Visão** | Modo UI | ✅ | ❌ | ❌ | ❌ | Captura de frames de tela autorizados | `godseye.observe` |
| **Modo Wearable / Mobile** | Modo UI | ✅ | ❌ | ❌ | ✅ | Tailscale / TLS 1.3 mTLS loopback | Token de sessão efêmero (120s) |
| **EngineerAgent** | Agente | ✅ | ✅ | ✅ | ❌ | Confinado em `/workspace`, bwrap sem rede | `CapabilityToken` com `allow_privileged=True` |
| **ProfessorAgent** | Agente | ✅ | ❌ | ❌ | ❌ | Apenas geração pedagógica e leitura de corpus | `mode:professor` |
| **WebResearchAgent** | Agente | ✅ | ❌ | ❌ | ✅ | URLs públicas com validação de SSRF | Proveniência não confiável (`UNTRUSTED`) |
| **SentinelAgent** | Agente | ✅ | ❌ | ❌ | ❌ | Telemetria de CPU/RAM/Disco local | `sentinel.telemetry` |
| **ReverseEngine (REA)** | Agente/Tool | ✅ | ❌ | ❌ | ❌ | Artefatos estáticos e descompilação segura | `reverse.inspect` |
| **Ponte MCP Sandbox** | Ponte MCP | ✅ | ✅ | ✅ | ❌ | Bubblewrap (`--unshare-all`, `--unshare-net`) | `sandbox.execute`, `sandbox.verify` |
| **Ponte MCP God's Eye** | Ponte MCP | ✅ | ❌ | ❌ | ❌ | Leitura de janelas e contexto visual | `godseye.observe`, `godseye.intent` |
| **Ponte Smart Home Gateway** | Ponte MCP | ✅ | ❌ | ❌ | ✅ | Sensores MQTT over TLS (somente leitura) | `smarthome.read` |
| **Conector Home Assistant** | Conector | ✅ | ❌ | ❌ | ✅ | `/api/states` e `/api/discovery` (GET apenas) | `homeassistant.read` |
| **Ponte Backup Manager** | Ponte MCP | ✅ | ✅ | ❌ | ❌ | Backups cifrados AEAD (`0600`/`0700`) | `backup.create`, `backup.restore` |
| **Conector GitHub Assistant** | Conector | ✅ | ❌ | ❌ | ✅ | Repositórios na allowlist configurada | `github.read` |
| **Gateway Móvel Tailscale** | Gateway | ✅ | ❌ | ❌ | ✅ | Loopback `127.0.0.1` com Certificate Pinning | Pin SHA-256 e ECDSA P-256 |
| **Roteador Híbrido Rust** | Core | ✅ | ❌ | ✅ | ❌ | Classificação em memória nativa (`mark_core`) | Permissão de core interno |

---

## 3. Especificação Detalhada por Camada

### 3.1. Modos do MARK
1. **Modo Chat Geral:** Atua exclusivamente como intermediário conversacional. Não possui capacidade de criar ou alterar arquivos no disco do sistema, nem executar comandos de terminal.
2. **Modo Professor:** Projetado para tutoria e explicação passo a passo. Bloqueado de qualquer mutação estrutural de código ou execução de processos.
3. **Modo Engenheiro:** Único modo de interface autorizado a solicitar mutações ao `EngineerAgent`. Todas as alterações são confinadas obrigatoriamente dentro do subdiretório `/workspace` com backups automáticos verificáveis (`.mark_backups`).
4. **Modo Visão / God's Eye:** Acesso somente leitura ao buffer de renderização gráfica. Proibida a transmissão de capturas de tela para serviços de terceiros sem autorização prévia.
5. **Modo Móvel e Wearable:** Comunicação criptografada fim a fim com dispositivos Android emparelhados via QR Code de uso único.

### 3.2. Agentes Autônomos
1. **`EngineerAgent`:**
   - **Escopo Concedido:** Leitura e alteração de arquivos de código dentro de `/workspace`.
   - **Isolamento de Kernel:** Processos filhos despachados via `ExecutionSandbox` com `bwrap` (`--unshare-all`, `--unshare-net`, `--cap-drop ALL`, `--die-with-parent`).
   - **Quotas de Recursos:** Limites estritos via `resource.setrlimit` para tempo de CPU (`RLIMIT_CPU = 30s`), memória virtual (`RLIMIT_AS = 1 GB`) e descritores de arquivo (`RLIMIT_NOFILE = 128`).
   - **Bloqueio Incondicional:** Proibido de acessar `src/mark/` (código-fonte do core), diretórios de sistema (`/etc`, `/root`, `~/.ssh`), ou invocar ferramentas com bypass de autenticação.
2. **`ProfessorAgent`:**
   - **Escopo Concedido:** Consulta a notas e síntese didática.
   - **Bloqueio Incondicional:** Sem permissão de mutação ou execução de comandos de SO.
3. **`WebResearchAgent`:**
   - **Escopo Concedido:** Requisições HTTP GET externas para busca e navegação.
   - **Proteção SSRF:** Bloqueio mandatório de IPs privados (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.1`), link-local (`169.254.0.0/16`) e metadados de nuvem (`metadata.google.internal`).
   - **Higienização:** Todo o conteúdo externo recuperado é encapsulado com metadados de procedência não confiável (`UNTRUSTED_PROVENANCE_META`).

### 3.3. Pontes MCP (Model Context Protocol)
1. **Ponte Sandbox (`mcp_sandbox.py`):**
   - Ferramentas registradas: `sandbox_run_command`, `sandbox_run_python`, `sandbox_run_pytest`, `sandbox_run_rust_build`, `engineer_solve_task`, `rea_inspect_artifact`, `rea_decompile_function`, `rea_compare_artifacts`.
   - Exige `CapabilityToken` ativo com `permissions: ("sandbox.execute",)` ou `("sandbox.verify",)`.
   - Ferramentas privilegiadas exigem explicitamente `allow_privileged=True`.
2. **Ponte Smart Home Gateway (`smart_home_gateway.py`):**
   - Comunicação MQTT restrita a canais com TLS 1.3 obrigatório e validação estrita de certificados X.509.
   - Webhooks assinados com HMAC-SHA256, verificação anti-replay com nonces de uso único e janela máxima de clock skew de 60 segundos.
   - Proibição absoluta de publicação de comandos em atuadores físicos sem confirmação biométrica do dono (`MARK-OWNER-CONFIRM-V1`).
3. **Ponte Home Assistant (`home_assistant.py`):**
   - Modo estritamente **somente leitura**.
   - Proibição de acesso a endpoints mutáveis (`/api/services/*`, `/api/events/*`, `/api/config/*`, `/api/webhook/*`).
   - Purga e higienização automática de tokens de acesso Bearer em logs e mensagens de erro.
4. **Ponte Backup Manager (`backup_manager.py`):**
   - Criptografia autenticada AEAD (`AES-256-GCM` ou `ChaCha20-Poly1305`) com derivação de chave via PBKDF2-HMAC-SHA256 (310.000 iterações).
   - Verificação anti-Zip-Slip, anti-Zip-Bomb e rejeição de links simbólicos.
   - Chave mestra isolada fora do arquivo compactado com permissão estrita `0600`.

---

## 4. Estrutura e Ciclo de Vida do `CapabilityToken`

O `CapabilityToken` é o artefato de segurança primário utilizado para autorização entre agentes e ferramentas:

```python
# Assinatura canônica do CapabilityToken
payload = f"MARK-CAP-V1|{mission_id}|{allowed_tools}|{permissions}|{expires_at:.3f}|{int(allow_privileged)}"
signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
```

### Regras do Token:
1. **Não Reutilizável entre Missões:** O token é estritamente vinculado ao identificador da missão (`mission_id`). Qualquer tentativa de usá-lo em outra missão dispara `ToolCapabilityError`.
2. **Tempo de Vida Curto (TTL):** Por padrão, 900 segundos (15 minutos). Expira automaticamente após esse período.
3. **Assinatura Epifítica:** A chave secreta de assinatura é gerada aleatoriamente na inicialização do daemon (`secrets.token_bytes(32)`) e nunca é exposta na API ou persistida em disco.
4. **Revogação Instantânea:** O `AuthorizationLayer` permite a revogação atômica em memória de todas as capacidades de uma missão através de `revoke_mission_capabilities(mission_id)`.

---

## 5. Lista de Proibições Absolutas (Forbidden Patterns)

A tabela abaixo relaciona padrões cujo acesso ou presença em argumentos de qualquer ferramenta MCP dispara bloqueio imediato e registro de incidente de segurança:

| Padrão / Parâmetro | Motivo do Bloqueio | Exceção Lançada |
| :--- | :--- | :--- |
| `/etc/shadow`, `/etc/gshadow` | Tentativa de acesso a hashes de senhas do sistema | `ToolCapabilityError` |
| `/etc/sudoers`, `/etc/sudoers.d` | Tentativa de escalonamento de privilégios | `ToolCapabilityError` |
| `~/.ssh/id_*`, `id_rsa`, `id_ed25519` | Tentativa de roubo de chaves SSH do desenvolvedor | `ToolCapabilityError` |
| `mobile.token`, `smart_home_vault.key` | Chaves mestras e cofres criptográficos locais | `ToolCapabilityError` |
| `..\` ou `../` (Path Traversal) | Tentativa de fuga de diretório (Directory Traversal) | `ToolCapabilityError` |
| Byte Nulo (`\x00`) | Tentativa de injeção de terminação de string em C | `ToolCapabilityError` |
| `raw_secret`, `master_key`, `sudo_password` | Tentativa de passagem de credencial em texto puro | `ToolCapabilityError` |
| Auto-aprovação por IA (`assert_not_llm_approved`) | Proibição de robôs auto-autorizarem ações críticas | `PermissionError` |
