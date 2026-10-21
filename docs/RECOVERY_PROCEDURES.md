# MARK — Procedimento Operacional Padrão (SOP): Recuperação Pós-Perda ou Roubo de Dispositivo

**Documento:** SOP-SEC-002  
**Classificação:** Confidencial / Segurança Operacional  
**Fase do Ciclo de Vida:** P2 (Segurança Avançada e Continuidade de Negócios)  
**Módulos Envolvidos:** `src/mark/device_auth.py`, `src/mark/backup_manager.py`, `src/mark/cognition/storage.py`

---

## 1. Visão Geral e Modelo de Ameaças

Em caso de furto, perda, extravio ou comprometimento físico de um dispositivo autorizado (smartphone, relógio, tablet ou estação cliente), o sistema MARK implementa um fluxo de contenção de incidentes de confiança zero (*Zero Trust*). O objetivo primordial é:

1. **Cortar Imediatamente o Acesso:** Revogar a chave pública ECDSA P-256 associada ao dispositivo e purgar instantaneamente todas as sessões ativas e nonces na memória e no banco SQLite.
2. **Prevenir Reativação:** Garantir que o dispositivo ou chave comprometida jamais seja reativado ou reinscrito no cluster MARK, mesmo se o invasor interceptar códigos de pareamento subsequentes.
3. **Reemparelhamento Seguro do Dispositivo Substituto:** Permitir a geração de um novo par de chaves criptográficas em hardware seguro (Secure Enclave / StrongBox / TPM) e reemparelhar via desafio canônico de uso único.
4. **Restauração Segura da Memória Persistente:** Reconstituir o estado cognitivo, configurações e históricos através de backups cifrados em repouso com Criptografia Autenticada AEAD (AES-256-GCM ou ChaCha20-Poly1305), com garantias estritas contra Zip-Slip, Zip Bomb e adulteração de dados.

---

## 2. Fase 1: Revogação Imediata do Dispositivo Furtado

Assim que o incidente de roubo for detectado:

### 2.1. Revogação Via API / CLI Local
No nó central MARK (servidor/host local):

```python
from mark.device_auth import DeviceRegistry, SessionRegistry

# 1. Carregar registro de dispositivos
devices = DeviceRegistry()
sessions = SessionRegistry(devices)

# 2. Revogar permanentemente o dispositivo furtado
lost_device_id = "<DEVICE_ID_DO_APARELHO_ROUBADO>"
devices.revoke_device(lost_device_id)
```

### 2.2. Efeitos Criptográficos e de Sessão Imediatos
- **Status do Dispositivo:** Atualizado para `revoked` no banco de dados com lock transacional atômico.
- **Incremento de `security_generation`:** O contador de geração de segurança é incrementado no banco, invalidando em tempo real todas as sessões ativas emitidas sob a geração anterior.
- **Purga de Sessões Ativas:** O callback registrado `SessionRegistry.revoke_device_sessions` expurga imediatamente da memória volátil todos os tokens de sessão associados ao `device_id`.
- **Eliminação de Nonces:** Todos os nonces anti-replay do dispositivo revogado são apagados da tabela `nonces`.
- **Bloqueio Irreversível de Reativação:** O método `enroll()` e `rotate_key()` checam se o hash da chave pública DER pertence a um dispositivo com status `revoked`. Se houver tentativa de reutilização da chave roubada, o sistema rejeita imediatamente com `ValueError("chave e dispositivo revogados permanentemente; reativação proibida")`.

---

## 3. Fase 2: Emissão de Novo Par de Chaves e Reemparelhamento

No novo dispositivo substituto:

### 3.1. Geração de Chave Soberana
O novo dispositivo gera um par de chaves assimétricas **ECDSA na curva elíptica NIST P-256 (secp256r1)** no hardware criptográfico seguro local (Secure Enclave no iOS, Android Keystore com StrongBox no Android):

```python
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from mark.device_auth import b64

# Gerar par de chaves soberano local
new_private_key = ec.generate_private_key(ec.SECP256R1())
new_public_der = new_private_key.public_key().public_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)
new_public_der_b64 = b64(new_public_der)
```

### 3.2. Abertura da Janela de Pareamento
No console local do MARK:

```python
# Abre janela de pareamento de uso único (ex: 180 segundos)
code = devices.open_enrollment(validity_seconds=180)
# Ou gera o payload canônico para QR Code seguro com pinning de fingerprint TLS:
qr_payload = devices.generate_pairing_qr_payload(
    server_url="https://mark.local:8443",
    cert_fingerprint="<SHA256_HEX_FINGERPRINT>",
    validity_seconds=180,
)
```

### 3.3. Prova de Posse (Proof of Possession - PoP)
O novo dispositivo assina o código efêmero fornecido no QR Code:

```python
challenge = b"MARK-PAIR-V1\n" + code.encode("ascii")
proof_sig = new_private_key.sign(challenge, ec.ECDSA(hashes.SHA256()))
proof_b64 = b64(proof_sig)
```

### 3.4. Inscrição e Aprovação Soberana
O novo dispositivo envia a requisição de pareamento:

```python
new_device_id = devices.enroll(
    code=code,
    name="Smartphone Substituto (Novo)",
    public_der_b64=new_public_der_b64,
    proof_b64=proof_b64,
)

# Aprovação manual obrigatória pelo proprietário (bloqueio irrestrito contra autoaprovação por LLM)
devices.update_device(new_device_id, "authorized")
```

Alternativamente, a API utilitária atômica pode ser acionada diretamente pelo console de segurança:
```python
new_device_id = devices.recover_after_device_theft(
    lost_device_id=lost_device_id,
    new_name="Smartphone Substituto (Novo)",
    new_public_der_b64=new_public_der_b64,
    new_proof_b64=proof_b64,
    enrollment_code=code,
    auto_authorize=True,
)
```

---

## 4. Fase 3: Restauração da Memória Persistente e Dados

Caso o dispositivo roubado também tenha causado perda ou necessidade de restauração dos dados locais:

### 4.1. Criptografia Autenticada de Backups (AEAD)
Os backups do MARK são protegidos utilizando **AES-256-GCM** ou **ChaCha20-Poly1305** com:
- Derivação de chave via PBKDF2-HMAC-SHA256 (310.000 iterações) e salt aleatório de 32 bytes criptograficamente seguro.
- Nonce único de 12 bytes gerado por arquivo/backup (`secrets.token_bytes(12)`).
- Chave mestra mantida fora do arquivo comprimido no caminho seguro de estado com permissões POSIX restritas `0600`.
- Tag de autenticação MAC de 16 bytes que impede qualquer adulteração imperceptível.

### 4.2. Blindagem do Processo de Descompressão e Importação
O `BackupManager` aplica defesas estritas durante a restauração:
- **Anti Zip-Slip:** Todos os caminhos internos do arquivo comprimido são normalizados e validados contra o diretório de destino (`_validate_archive_relpath`). Qualquer tentativa de path traversal (`../`) ou caminho absoluto dispara `ValueError`.
- **Anti Zip-Bomb:** Limite estrito de tamanho máximo por membro (128 MB), tamanho total descompactado (512 MB), número máximo de membros (4.096) e razão máxima de descompressão (100.0x).
- **Rejeição de Links Simbólicos:** Arquivos no arquivo comprimido que apresentem flag de symlink POSIX (`0o120000`) são imediatamente rejeitados com erro de segurança.
- **Rollback Atômico em Caso de Falha:** Antes de sobrescrever qualquer arquivo no estado de destino, o `restore_backup()` cria uma fotografia atômica temporária. Se ocorrer falha no meio da descompressão ou validação do banco SQLite, a restauração é abortada e o estado original é recomposto perfeitamente com suas permissões `0600`.

### 4.3. Execução da Restauração

```python
from mark.backup_manager import BackupManager

# Instanciar o gerenciador de backup
backup_mgr = BackupManager(
    app_state_dir=Path("~/.local/state/mark").expanduser(),
    backups_dir=Path("~/backups/mark").expanduser(),
)

# Restaurar backup seguro informando a senha mestra ou derivando da chave mestra
backup_mgr.restore_backup(
    archive_path=Path("~/backups/mark/backup_mark_latest.zip"),
    passphrase="sua-senha-mestra-segura",
)
```

---

## 5. Matriz de Auditoria e Testes

Os testes automatizados que comprovam e blindam todo o fluxo de recuperação contra regressões estão consolidados em:
- `tests/test_p2_security_hardening.py`
  - `test_device_revocation_and_anti_reactivation()`: Valida revocação imediata, invalidação de sessões e rejeição permanente de reativação da chave roubada.
  - `test_post_theft_recovery_flow()`: Valida a transição atômica completa para o novo dispositivo com novo par EC P-256.
  - `test_backup_aead_encryption_and_tamper_detection()`: Valida cifras AES-256-GCM / ChaCha20-Poly1305, isolamento de chaves e detecção de adulteração.
  - `test_backup_zip_slip_and_bomb_protection()`: Valida rejeição de Zip-Slip, Zip Bomb e symlinks.
  - `test_backup_atomic_rollback_on_failure()`: Valida reversão atômica de arquivos e permissões.

---

## 6. Checklist de Resposta a Incidentes (Para Operador/Usuário)

1. [ ] Notificar o incidente de extravio/roubo do dispositivo.
2. [ ] Acessar o nó central MARK via terminal seguro local (SSH com chave ou console físico).
3. [ ] Executar o comando de revogação para o `device_id` comprometido.
4. [ ] Confirmar que o status do dispositivo no banco é `revoked` e que sessões foram expurgadas.
5. [ ] Configurar o novo dispositivo e gerar novo par de chaves ECDSA P-256.
6. [ ] Gerar novo QR Code com expiração estrita (180s) e emparelhar o novo dispositivo.
7. [ ] Confirmar a aprovação soberana pelo proprietário.
8. [ ] Caso necessário, restaurar a memória persistente a partir do último backup autenticado AEAD.
9. [ ] Verificar integridade e permissões POSIX (`0600` em arquivos, `0700` em diretórios).
