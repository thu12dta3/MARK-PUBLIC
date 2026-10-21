# Especificação Arquitetural e de Protocolo — MARK Link

**Data:** 19/10/2026  
**Status:** Implementado e Auditado  
**Subsistema:** `mark_link` (Rust) & `src/mark/link` (Python)  
**Repositório:** `MARK-PUBLIC` (Especificação Técnica de Arquitetura)

---

## 1. Visão Geral e Papel no Ecossistema

O **MARK Link** é o subsistema de transporte seguro e conectividade privada do ecossistema MARK. Seu propósito principal é permitir a comunicação bidirecional confidencial e autenticada entre o **MARK PC** (Core em Rust / Serviços em Python) e os clientes móveis autorizados (**MARK Mobile** no Android e Wear OS no relógio), eliminando a necessidade de VPNs de terceiros (Tailscale) para o tráfego de controle e dados do MARK, sem requerer privilégios de administrador (`root`) e sem criar interfaces virtuais no kernel do sistema operacional.

```
+-----------------------------------------------------------------------+
|                              MARK PC                                  |
|                                                                       |
|  +-------------------+        +------------------------------------+  |
|  |  mark_link daemon | <----> | Python DirectListener / Service    |  |
|  |    (Rust Core)    |  TCP   | (DeviceRegistry, Session, Adapter) |  |
|  |  Porta 8766 / LAN |        | Porta 8765 (API interna do MARK)   |  |
|  +---------^---------+        +------------------------------------+  |
+------------|----------------------------------------------------------+
             |
             | [Handshake Mútuo EC P-256 ECDH + ECDSA]
             | [Canal Criptografado MLNK: AES-256-GCM com Chaves Direcionais]
             |
     +-------v--------+                +---------------------+
     | Conexão Direta | (ou Fallback)  | MARK Blind Relay    |
     |   (LAN / WAN)  | -------------> | Porta 8767 (MRLY)   |
     +-------^--------+                +----------^----------+
             |                                    |
             +-----------------+------------------+
                               |
                   +-----------v-----------+
                   |  MARK Mobile / Wear   |
                   |   Cliente Autorizado  |
                   | (Android Keystore EC) |
                   +-----------------------+
```

---

## 2. Componentes e Papéis

### 2.1 Daemon de Transporte (`mark_link::daemon` / `DirectListener`)
- **Papel:** Ponto de entrada TCP principal no PC (padrão: `127.0.0.1:8766` ou IP local da interface autorizada).
- **Linguagem Prioritária:** Rust (`src-rust/mark_link/src/daemon.rs`), com espelho operacional em Python (`src/mark/link/transport/direct.py`).
- **Autenticação:** Rejeita qualquer conexão que não inicie com `HANDSHAKE_INIT` assinado digitalmente por chave EC P-256 de dispositivo previamente aprovado no `DeviceRegistry`.
- **Isolamento por Aplicação:** Encapsula requisições autenticadas e submete cada caminho HTTP à política de isolamento `AccessPolicy`. Rotas fora da allowlist (como `/admin`, `/etc/shadow`, `/system/exec`) são terminantemente bloqueadas com HTTP 403.

### 2.2 Blind Relay (`mark_link::relay` / `RelayServer` / `RelayClient`)
- **Papel:** Roteador de contingência para travessia de NATs restritivos e redes simétricas onde a conectividade direta ponto-a-ponto não é viável.
- **Linguagem:** Rust (`src-rust/mark_link/src/relay.rs`) e Python (`src/mark/link/relay/`).
- **Propriedade Cega:** O servidor opera no protocolo `MRLY`, lendo apenas os identificadores de origem e destino (`DeviceId`). O payload permanece opaco e criptografado ponta-a-ponta entre cliente e PC. O relay não possui as chaves e não inspeciona o tráfego.
- **Proteção contra Abuso:** Filas delimitadas (`max_queue_per_peer = 64`), descarte automático do pacote mais antigo em sobrecarga, payload máximo de 64 KB por envelope, encerramento de conexões inativas.

### 2.3 Gestor de Pares e Telemetria (`LinkPeerManager` / `LinkTelemetryCollector`)
- **Papel:** Monitoramento contínuo de latência RTT, seleção dinâmica de candidatos (DirectLocal > DirectRemote > Relay), controle de reconexão exponencial com jitter de ±20% e ajuste de taxa de ping (ativo: 2s a 5s; repouso/idle: 20s a 30s) para economia de bateria no celular.
- **Sanitização:** Todos os relatórios mascaram identificadores de pares e expurgam credenciais, tokens Bearer e dados privados de mensagens de erro.

---

## 3. Especificação do Protocolo Criptográfico

### 3.1 Handshake Mútuo Autenticado

1. **Iniciação pelo Cliente (`HANDSHAKE_INIT`, Tipo 0x01):**
   O cliente gera um par efêmero EC P-256 ($E_{cli}, e_{cli}$) e um nonce de 32 bytes ($N_{cli}$).
   Constrói a mensagem canônica:
   ```
   MARK-LINK-AUTH-INIT-V1\n<device_id>\n<ephemeral_pk_der_b64>\n<nonce_b64>\n<timestamp>
   ```
   O cliente assina essa mensagem com sua chave de identidade de dispositivo privada persistida no Android Keystore via ECDSA P-256 SHA-256:
   $$\sigma_{cli} = \text{Sign}_{K_{dev}}(M_{init})$$
   Envia o frame `MLNK` com cabeçalho contendo `packet_type = 0x01` e o JSON:
   ```json
   {
     "device_id": "...",
     "ephemeral_pk_der_b64": "...",
     "nonce_b64": "...",
     "timestamp": 1729382400,
     "signature_b64": "..."
   }
   ```

2. **Validação pelo Servidor (PC):**
   - Valida deriva de relógio (anti-replay): $|t_{now} - timestamp| \le 60\text{ s}$.
   - Consulta `DeviceRegistry`: se `device_id` não estiver em estado `authorized`, aborta a conexão imediatamente.
   - Recupera a chave pública DER registrada do aparelho e verifica a assinatura $\sigma_{cli}$ sobre a mensagem canônica. Se inválida, desconecta imediatamente.

3. **Resposta do Servidor (`HANDSHAKE_RESPONSE`, Tipo 0x02):**
   - O servidor gera seu par efêmero EC P-256 ($E_{srv}, e_{srv}$) e um nonce de 32 bytes ($N_{srv}$).
   - Computa o segredo compartilhado Diffie-Hellman:
     $$SS = \text{ECDH}(e_{srv}, E_{cli})$$
   - Envia resposta com `packet_type = 0x02`:
     ```json
     {
       "server_id": "mark-pc-core",
       "ephemeral_pk_der_b64": "...",
       "nonce_b64": "...",
       "timestamp": 1729382400,
       "status": "authorized"
     }
     ```

4. **Derivação de Chaves Direcionais de Sessão (HKDF-SHA256):**
   Ambas as partes expandem 72 bytes de material criptográfico usando:
   $$\text{Salt} = N_{cli} \parallel N_{srv}$$
   $$\text{Info} = \text{"mark-link-v1-directional-session"}$$
   $$\text{OKM} = \text{HKDF-Expand}(\text{HKDF-Extract}(\text{Salt}, SS), \text{Info}, 72)$$

   Divisão do material criptográfico derivado:
   - $K_{c2s}$ (Bytes 0..31): Chave AES-256 de Cliente para Servidor.
   - $S_{c2s}$ (Bytes 32..35): Salt de nonce de 4 bytes de Cliente para Servidor.
   - $K_{s2c}$ (Bytes 36..67): Chave AES-256 de Servidor para Cliente.
   - $S_{s2c}$ (Bytes 68..71): Salt de nonce de 4 bytes de Servidor para Cliente.

### 3.2 Separação Direcional e Não-Reutilização de Nonces

Para eliminar permanentemente o risco de colisão de nonces em AES-GCM:
- O canal Cliente $\to$ Servidor utiliza exclusivamente a chave $K_{c2s}$ e o salt $S_{c2s}$.
- O canal Servidor $\to$ Cliente utiliza exclusivamente a chave $K_{s2c}$ e o salt $S_{s2c}$.
- Cada direção possui um contador de sequência independente `tx_seq` inicializado em 1, que cresce estritamente a cada pacote transmitido.
- O nonce de 96 bits (12 bytes) do AES-GCM é construído como:
  $$\text{Nonce}_{12} = \text{Salt}_{4} \parallel \text{to\_be\_bytes}_{8}(seq)$$
- **Failsafe de Esgotamento:** O contador rejeita sequências $\ge 2^{32} - 1$ lançando `SequenceOverflow` / `OverflowError`. Sob nenhuma circunstância o contador reinicia ou volta a zero com a mesma chave. A exaustão exige um novo handshake.

### 3.3 Enquadramento Binário MLNK

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Magic: "MLNK"                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|    Version    |  Packet Type  |           Reserved            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Sequence Number (32b)                   |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Payload Length (32b)                     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|        AEAD Ciphertext + 16-byte Polyval/GCM Tag              |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```
- Os 16 bytes do cabeçalho atuam como **Dados Associados Autenticados (AAD)** no AES-GCM. Qualquer alteração nos campos `packet_type`, `version`, `sequence` ou `length` causa falha imediata na verificação do tag de autenticação.

### 3.4 Enquadramento Binário de Relay MRLY

```
+------------+----------------+---------------+----------------+---------------+---------------+--------------------+
| Magic (4B) | TargetLen (1B) | TargetId (NB) | SourceLen (1B) | SourceId (MB) | Length (4B)   | Opaque Payload (KB)|
|   "MRLY"   |    uint8       |   utf-8 str   |    uint8       |   utf-8 str   | uint32 (BE)   | encrypted frame    |
+------------+----------------+---------------+----------------+---------------+---------------+--------------------+
```

---

## 4. Modelo de Ameaças e Isolamento

| Vetor de Ameaça | Defesa Implementada no MARK Link |
| :--- | :--- |
| **Pares Não Autorizados** | Drop imediato de conexões sem verificação mútua de assinatura ECDSA P-256 com chave aprovada em SQLite. |
| **Replay de Pacotes** | Janela deslizante de 64 pacotes com verificação preliminar e comutação atômica pós-validação de tag AEAD. Nonces únicos por chave. |
| **Reutilização de Nonce AES-GCM** | Separação estrita de chaves e salts TX/RX ($K_{c2s} \neq K_{s2c}$), contadores monotônicos de 64 bits com failsafe de estouro. |
| **Acesso a Serviços Indevidos do PC** | `AccessPolicy` valida todas as requisições contra uma allowlist rígida (`/v1/*`, `/health`). Rotas administrativas ou do sistema operacional sofrem abort com HTTP 403. |
| **Vazamento de Dados em Relay** | Envelopes opacos cifrados ponta-a-ponta. O servidor de relay não possui chaves nem acesso ao conteúdo das mensagens. |
| **Comprometimento de Segredos de Sessão** | Chaves efêmeras geradas via ECDH P-256 a cada sessão e destruídas em memória (`zeroize`) após encerramento do canal. |
| **Vazamento de Chaves em Logs** | Telemetria atômica com sanitização regex para tokens, credenciais e ofuscação de IDs de pares (`peer_id_masked`). |

---

## 5. Guia Operacional

### 5.1 Iniciar o Daemon de Transporte (Rust)
```bash
./target/release/mark-link daemon --listen 127.0.0.1:8766
```

### 5.2 Iniciar o Servidor de Relay Cego (Rust)
```bash
./target/release/mark-link relay --listen 0.0.0.0:8767
```

### 5.3 Iniciar e Operar via Python
```python
from src.mark.link import MarkLinkService, LinkDeviceManager

# Inicia serviço gerenciado integrado ao registro de dispositivos do MARK
service = MarkLinkService(host="127.0.0.1", port=8766)
service.start()

# Diagnósticos sanitizados em tempo real
report = service.get_diagnostics()
print(f"Estado: {report.state}, RTT Médio: {report.rtt_ms:.2f} ms")

# Encerramento limpo de recursos e sockets
service.stop()
```

### 5.4 Pareamento e Revogação de Dispositivos
1. O usuário gera um código de pareamento efêmero no PC (via QR Code):
   ```python
   code = registry.open_enrollment(validity_seconds=180)
   ```
2. O aplicativo móvel submete sua chave pública e prova de posse:
   ```python
   device_id = registry.enroll(code, name="Galaxy S23", public_der_b64=..., proof_b64=...)
   ```
3. O PC aprova explicitamente:
   ```python
   registry.update_device(device_id, "authorized")
   ```
4. Revogação imediata (em tempo real):
   ```python
   registry.revoke_device(device_id, permanent=True)
   ```
   Qualquer tentativa subsequente de handshake por esse dispositivo é rejeitada no socket TCP.
