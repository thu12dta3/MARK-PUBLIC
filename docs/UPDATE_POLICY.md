# 🔄 Política de Atualizações, Assinatura Criptográfica e Rollback do MARK (Fase P3)

## 1. Ciclo de Vida de Versões e Suporte (SemVer 2.0.0)

O projeto **MARK** adota estritamente o Versionamento Semântico (**SemVer 2.0.0** — `MAJOR.MINOR.PATCH`):
- **MAJOR:** Mudanças de arquitetura incompatíveis com versões anteriores, quebras de protocolo ou migrações obrigatórias de esquema criptográfico.
- **MINOR:** Novas funcionalidades, novos agentes, expansão de pontes MCP ou melhorias que mantenham compatibilidade retroativa.
- **PATCH:** Correções de bugs, endurecimento de segurança, atualizações de dependências contra CVEs e melhorias de estabilidade.

### Tabela de Ciclo de Vida de Versões
| Canal / Faixa | Status de Suporte | Tipo de Atualizações Recebidas | Janela de Suporte |
| :--- | :--- | :--- | :--- |
| **Linha Principal (`main` / Current)** | Ativo | Recursos, melhorias, patches de segurança e estabilidade | Contínua (desenvolvimento ativo) |
| **LTS (Long Term Support - Ex: v1.x)** | Estável | Exclusivamente patches críticos de segurança e correções de bugs | 18 meses após lançamento de nova versão Major |
| **Versões Descontinuadas (EOL)** | Sem Suporte | Nenhuma atualização ou patch fornecido | Imediato após encerramento da janela LTS |

---

## 2. Fluxo Operacional de Correções Urgentes (SecOps Hotfix SLA)

Quando uma vulnerabilidade de segurança, regressão crítica ou bypass de sandbox for identificado, o protocolo de Hotfix de Segurança é acionado com os seguintes SLAs:

| Severidade | Descrição / Exemplo | SLA de Mitigação | SLA de Release |
| :--- | :--- | :---: | :---: |
| **P0 (Crítico)** | Fuga de sandbox, bypass de autenticação, vazamento de chaves ou RCE | < 4 horas | < 12 horas |
| **P1 (Alto)** | Falha em certificado pinning, bypass de quota de recursos ou SSRF | < 24 horas | < 48 horas |
| **P2 (Médio)** | Falha de formatação em sanitização de logs, negação de serviço local | < 72 horas | < 7 dias |
| **P3 (Baixo)** | Melhoria defensiva, inconsistência em documentação de segurança | < 7 dias | Próximo ciclo Minor |

### Procedimento Passo a Passo de Hotfix:
1. **Isolamento de Branch:** A partir da tag de versão afetada ou da branch `main`, criar uma branch dedicada no formato `hotfix/sec-<identificador>` (ex: `hotfix/sec-sandbox-bypass`).
2. **Reprodução Adversarial:** Desenvolver obrigatoriamente um teste de regressão adversarial correspondente em `tests/test_p3_adversarial_regression.py` que falhe na versão não corrigida.
3. **Aplicação do Patch:** Corrigir a causa raiz minimizando modificações fora do escopo estrito da vulnerabilidade.
4. **Validação Contínua Obrigatória:** Executar a suíte de DevSecOps (`scripts/security_audit.py --all`) e a bateria completa de testes (`pytest tests/`). Todos os 740+ testes devem estar 100% aprovados.
5. **Assinatura e Tag:** Gerar nova tag PATCH assinada criptograficamente e submeter merge direto para `main` e branches LTS ativas.

---

## 3. Verificação Criptográfica de Assinatura de Artefatos

Todos os executáveis, pacotes de atualização, binários compilados em Rust (`mark_core.so`) e pacotes Android (`.apk`) devem ser assinados criptograficamente antes de qualquer distribuição ou instalação no sistema hospedeiro.

### 3.1. Esquema de Assinaturas Suportado
- **Binários Desktop & Scripts:** Assinatura desanexada via **Ed25519** ou **ECDSA P-256** com SHA-256.
- **Distribuição Mobile:** Validação estrita do **APK Signature Scheme v2** utilizando a chave pública canônica do MARK (`563e12b841fa5838d567f563d23f8ee4a89510a9ed28fcb466f9779992fd92f4`).

### 3.2. Fluxo de Validação Pré-Instalação:
1. **Cálculo de Digest:** O instalador calcula o hash SHA-256 independente do artefato baixado.
2. **Verificação de Assinatura:** O digest é validado contra a assinatura criptográfica anexada utilizando a chave pública embutida no core do MARK.
3. **Rejeição Automática:** Caso o hash divirja por 1 único bit ou a assinatura seja inválida, o arquivo é descartado imediatamente, gerando registro no `audit.jsonl` com `decision: DENIED` e `event_type: INCIDENT_ALERT`.
4. **Proteção contra Chaves Revogadas:** O sistema consulta a lista local de chaves revogadas (`permanently_revoked_keys`). Artefatos assinados por chaves revogadas são rejeitados sumariamente.

---

## 4. Procedimento de Rollback Atômico e Seguro

Para garantir resiliência operacional e disponibilidade contínua, o MARK implementa uma política de **Rollback Atômico e Automático** em qualquer operação de atualização de software ou migração de banco de dados.

### 4.1. Gatilhos de Rollback Automático
- Falha na validação de integridade do banco SQLite (`PRAGMA integrity_check`) pós-migração.
- Exceção não tratada na inicialização do daemon ou falha no healthcheck local em até 30 segundos pós-atualização.
- Detecção de assinatura criptográfica inválida ou adulteração de artefato durante a descompactação.
- Falha em qualquer teste da suíte de regressão adversarial executada em modo pós-instalação.

### 4.2. Procedimento de Rollback Seguro Passo a Passo:
1. **Snapshot Pré-Upgrade:**
   - Antes de iniciar qualquer modificação, o `BackupManager` gera um backup criptografado AEAD (`AES-256-GCM` ou `ChaCha20-Poly1305`) de todo o estado em `~/.local/state/mark/backups/pre_upgrade_<timestamp>.mark`.
   - A chave mestra é verificada e mantida em `backup_master.key` com permissão estrita `0600`.
2. **Interrupção Controlada:**
   - O daemon encerra ordenadamente conexões ativas e desliga as pontes MCP.
3. **Restauração Atômica:**
   - Em caso de falha, os binários e scripts da versão anterior são restaurados a partir do diretório de staging.
   - O banco SQLite é revertido a partir do snapshot verificado.
4. **Verificação de Permissões POSIX:**
   - O sistema valida que todos os arquivos restaurados mantêm permissões estritas `0600` e diretórios `0700`.
5. **Registro de Auditoria:**
   - A operação de rollback é registrada no `audit.jsonl` com status `SUCCESS` e detalhes da reversão.
