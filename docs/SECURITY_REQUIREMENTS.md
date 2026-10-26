# Rastreabilidade de segurança do MARK Mobile

Fonte: documento “Levar a segurança do MARK Mobile 0.2 ao estado SECURITY COMPLETE”, recebido em 30/09/2026. A numeração SEC referencia os controles mais importantes; o documento original contém 156 seções e o gate 150 exige todos os controles e testes físicos. **Estado geral: SECURITY INCOMPLETE.**

“Implementado” significa presença de controle no código; **PASS** exige teste correspondente. “Pendente” e “não testado” nunca contam como PASS.

| ID | Requisito | Implementação / evidência | Teste / estado |
| --- | --- | --- | --- |
| SEC-NET-001 | Rede privada Tailscale e serviço PC só local | `mobile_api.py` escuta 127.0.0.1; Tailscale Serve 443 na tailnet | Parcial: HTTP 80 da tailnet ainda exposto; configuração pós-reboot não testada |
| SEC-NET-002 | TLS 1.3, confiança do sistema e pin do PC | `Tls13SocketFactory.kt`, `MarkApi.kt`, configuração de rede Android | Build e chat no M23 passaram; MITM/rotação pendentes |
| SEC-NET-003 | Troca de pin/servidor exige autorização explícita | `MainActivity.kt`, Android Keystore | Parcial; validar CryptoObject, troca de certificado e prova remota |
| SEC-ID-001 | Chave do aparelho não exportável | `DeviceIdentity.kt` cria ECDSA P-256 no Android Keystore | Código presente; backing físico e reinstalação não testados |
| SEC-PAIR-001 | Código temporário, uso único, posse da chave e aprovação PC | `DeviceRegistry` e `/v1/pair/*` | Autorização no M23 e chat passaram; replay/expiração física pendentes |
| SEC-SESSION-001 | Sessão curta, em memória e vinculada ao aparelho | `SessionRegistry` e `/v1/session` | Testes Python e chat no M23 passaram; reconexão/restart pendentes |
| SEC-REPLAY-001 | Assinatura cobre método, rota, corpo, sessão, timestamp, nonce e ID | `session_message()` e `DeviceRegistry.verify()` | Testes Python em andamento; fuzz/concorrência pendentes |
| SEC-REVOKE-001 | Revogação e lockdown negam acesso | `DeviceRegistry`, `SessionRegistry` e `MobileHandler` | Testes Python; corrida e sessão ativa física pendentes |
| SEC-BIO-001 | App lock por biometria ou credencial do dispositivo | `MainActivity.kt` | Código presente; M23/lockout/process death pendentes |
| SEC-BIO-002 | CryptoObject por ação crítica com prova ao PC | Android/PC | Pendente ponta a ponta, mesmo que haja bloqueio local |
| SEC-SCREEN-001 | Capturas e recentes protegidos | Preferência de tela segura e `FLAG_SECURE` | Teste no M23 pendente |
| SEC-STORAGE-001 | Backup de credenciais desativado | `AndroidManifest.xml`, `backup_rules.xml`, `data_extraction_rules.xml` | Manifesto inspecionado; restauração física pendente |
| SEC-STORAGE-002 | Arquivos temporários, cache e banco saneados | `VoiceCall.kt`, armazenamento local e servidor | Auditoria/testes de resíduos pendentes |
| SEC-API-001 | Rotas, tamanhos, timeouts, limites de recursos e rate limit | `mobile_api.py` | Parcial; campanha de DoS/fuzz pendente |
| SEC-API-002 | Intents/componentes exportados mínimos | Manifesto final do APK | Inspeção estática; intents maliciosas pendentes |
| SEC-FILE-001 | Upload/parser com formatos e limites | `ChatAttachment.kt` e `mobile_api.py` | Parcial; arquivos maliciosos pendentes |
| SEC-ERROR-001 | Erros saneados PC→GitHub sem token no APK | Não implementado | Pendente |
| SEC-APK-001 | Release R8, assinatura privada, ausência de segredos | `app/build.gradle.kts`, APK release | Build/assinatura e varredura pontual; decompilação completa pendente |
| SEC-SUPPLY-001 | Dependências verificadas, lock, SBOM, CI e scanner | Gradle e `requirements.txt` | Pendente/Parcial |
| SEC-ATTACK-001 | APK roubado/modificado, servidor falso e MITM negados | Arquitetura ECDSA + pin | Nenhum teste físico/adversarial conclusivo |
| SEC-M23-001 | Campanha física SM-M236B/DS | Aparelho `SM-M236B` conectado; APK instalado, autorizado e chat respondido | Parcial: chamada, biometria adversarial e testes de ataque pendentes |
| SEC-GATE-001 | Zero Critical/High conhecido e cobertura de segurança 100% | Gate 150 do documento | **FAIL / NOT TESTED**: não declarar SECURITY COMPLETE |

Detalhes das ameaças e dos limites constam em [THREAT_MODEL.md](THREAT_MODEL.md) e [PERMISSIONS_MATRIX.md](PERMISSIONS_MATRIX.md). Atualizar esta matriz após cada evidência real de teste, sem converter “código presente” em “PASS”.
