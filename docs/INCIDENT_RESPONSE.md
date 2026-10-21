# 🚨 Plano de Resposta a Incidentes de Segurança do MARK (Fase P3)

## 1. Classificação de Severidade de Incidentes

| Nível | Classificação | Critérios e Exemplos | Tempo Máximo de Resposta |
| :--- | :--- | :--- | :---: |
| **SEV-0** | **Catastrófico** | Fuga de sandbox com acesso ao host, comprometimento de chave mestra criptográfica ou execução remota de código (RCE). | Imediato (< 15 min) |
| **SEV-1** | **Crítico** | Tentativa persistente de bypass de autenticação, vazamento de segredos em logs, desvio de isolamento de rede ou roubo de dispositivo confirmado. | < 1 hora |
| **SEV-2** | **Alto** | Falha intermitente de certificate pinning, adulteração detectada em backup criptografado ou anomalia repetida em webhook. | < 4 horas |
| **SEV-3** | **Médio / Baixo** | Tentativa isolada de path traversal bloqueada pelo guard, erro de sintaxe em contrato de ferramenta MCP ou alerta preventivo de telemetria. | < 24 horas |

---

## 2. Equipe de Resposta e Papéis Operacionais

- **Incident Commander (IC):** Lidera o plano de resposta, declara severidade e autoriza o acionamento do Kill Switch ou revogação em massa.
- **Security Lead:** Analisa a trilha de auditoria (`audit.jsonl`), realiza perícia forense e coordena contenção técnica.
- **Core Committer:** Desenvolve o hotfix de emergência e valida os testes de regressão adversariais.
- **Communications Lead:** Comunica o dono do sistema e redige o relatório formal de pós-mortem.

---

## 3. Procedimentos Operacionais Padrão (SOP)

### SOP-01: Revogação em Massa de Credenciais (Mass Credential Revocation)
**Objetivo:** Invalidar instantaneamente todos os tokens, sessões ativas e chaves de dispositivos em caso de comprometimento sistêmico.

1. **Avanço Atômico de Geração de Segurança:**
   - Executar avanço de `security_generation` no `DeviceRegistry`:
   ```python
   from mark.device_auth import DeviceRegistry, SessionRegistry
   registry = DeviceRegistry()
   registry.advance_security_generation()  # Invalida todas as sessões anteriores
   ```
2. **Purga Instantânea de Sessões em Memória:**
   - O `SessionRegistry` expurga todas as sessões ativas sem exigir reinício do daemon:
   ```python
   session_mgr = SessionRegistry()
   session_mgr.purge_all_active_sessions()
   ```
3. **Revogação de Capacidades MCP:**
   - O `AuthorizationLayer` revoga todos os `CapabilityToken` em circulação:
   ```python
   from mark.cognition.tools import AuthorizationLayer
   authorizer = AuthorizationLayer()
   authorizer.revoke_all_capabilities()
   ```
4. **Registro de Auditoria:**
   - O evento de revogação em massa é gravado no `audit.jsonl` com `event_type: INCIDENT_ALERT` e status `SUCCESS`.

---

### SOP-02: Preservação Forense de Evidências (Forensic Evidence Preservation)
**Objetivo:** Assegurar a integridade e cadeia de custódia das evidências forenses antes de qualquer limpeza ou recuperação.

1. **Congelamento da Trilha de Auditoria:**
   - Criar uma cópia somente leitura imediata do arquivo de auditoria com timestamp:
   ```bash
   cp ~/.local/state/mark/audit.jsonl ~/.local/state/mark/evidence_audit_$(date +%s).jsonl
   chmod 0400 ~/.local/state/mark/evidence_audit_*.jsonl
   ```
2. **Cálculo de Digest da Cadeia de Hash:**
   - Calcular e documentar o hash SHA-256 do arquivo congelado:
   ```bash
   sha256sum ~/.local/state/mark/evidence_audit_*.jsonl > ~/.local/state/mark/evidence_audit.sha256
   ```
3. **Snapshot de Banco de Dados e Conexões:**
   - Gerar cópia limpa do banco SQLite via `sqlite3 database.sqlite3 ".backup 'evidence_db.sqlite3'"` para evitar corrupção por escrita concorrente.
   - Capturar estado dos descritores e conexões de rede locais:
   ```bash
   ss -tlpn > ~/.local/state/mark/evidence_network_sockets.txt
   ```

---

### SOP-03: Interrupção Imediata de Serviços (Emergency Kill Switch)
**Objetivo:** Interromper imediatamente todo o processamento autônomo e tráfego de rede para estancar exfiltração ou exploração ativa.

1. **Isolamento de Rede Host:**
   - Derrubar imediatamente a interface Tailscale Serve e forçar ligação estrita exclusivamente a `127.0.0.1`:
   ```bash
   systemctl stop mark-mobile.service || killall uvicorn
   ```
2. **Desativação do ExecutionSandbox e Agentes Autônomos:**
   - Congelar qualquer processo ativo do `EngineerAgent` e do `ExecutionSandbox`:
   ```bash
   pkill -9 -f "bwrap" || true
   pkill -9 -f "mark.engineer_agent" || true
   ```
3. **Bloqueio de Pontes MCP:**
   - Setar a flag de emergência `MARK_EMERGENCY_LOCKDOWN=true` no ambiente, instruindo o `BridgeCapabilityGuard` a rejeitar 100% das chamadas com `ToolCapabilityError("Sistema em confinamento de emergência (Kill Switch ativo).")`.

---

### SOP-04: Recuperação Limpa do Sistema (Clean System Recovery)
**Objetivo:** Restaurar o assistente MARK a um estado íntegro, limpo e verificado.

1. **Restauração de Backup Confiável:**
   - Restaurar a base de dados a partir do último backup AEAD íntegro conhecido:
   ```python
   from mark.backup_manager import BackupManager
   backup_mgr = BackupManager()
   backup_mgr.restore_encrypted_backup(latest_clean_backup_file, master_key_bytes)
   ```
2. **Verificação de Sanidade do Banco de Dados:**
   - Executar validação de esquema e integridade:
   ```bash
   sqlite3 ~/.local/state/mark/conversation.sqlite3 "PRAGMA integrity_check;"
   ```
3. **Execução da Bateria Completa de Testes:**
   - Executar `pytest tests/` e `./scripts/security_audit.py --all` garantindo 100% de aprovação.
4. **Re-emparelhamento e Reemissão de Chaves:**
   - Dispositivos móveis devem ser reinscritos do zero através de novo QR Code de uso único efêmero gerado com novas chaves ECDSA P-256.

---

## 4. Modelo de Relatório Pós-Incidente (Post-Mortem Template)

```markdown
# Relatório de Pós-Mortem de Incidente de Segurança - MARK

- **ID do Incidente:** INC-YYYYMMDD-XX
- **Severidade:** [SEV-0 / SEV-1 / SEV-2 / SEV-3]
- **Data e Hora de Início:** AAAA-MM-DD HH:MM:SS UTC
- **Data e Hora de Contenção:** AAAA-MM-DD HH:MM:SS UTC
- **Incident Commander:** [Nome do Engenheiro]

## 1. Resumo Executivo
[Breve resumo técnico do incidente, impacto e medidas tomadas]

## 2. Linha do Tempo dos Fatos
- **HH:MM:** Alerta detectado no audit.jsonl / monitoramento.
- **HH:MM:** Acionamento do SOP de Contenção.
- **HH:MM:** Interrupção de serviços e isolamento de rede.
- **HH:MM:** Aplicação de hotfix e restauração de integridade.

## 3. Causa Raiz (Análise dos 5 Porquês)
1. Por que ocorreu o incidente?
2. Por que o controle anterior não o preveniu?
3. Por que a falha não foi detectada no SAST/SCA?
4. Por que a permissão foi concedida?
5. Causa fundamental identificada.

## 4. Medidas Corretivas e Ações Preventivas
- [ ] Criação de teste de regressão adversarial em tests/test_p3_adversarial_regression.py.
- [ ] Ajuste no BridgeCapabilityGuard ou ExecutionSandbox.
- [ ] Atualização de dependências ou política de permissões.
```
