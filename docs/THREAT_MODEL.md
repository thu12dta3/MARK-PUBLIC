# Modelo de ameaças do MARK Mobile

Atualizado em 30/09/2026 para o candidato de segurança 0.2.1. O MARK PC é a autoridade de acesso. O app Android é um cliente potencialmente analisável e modificável pelo adversário.

## Ativos e limites de confiança

| Ativo | Localização | Proteção esperada |
| --- | --- | --- |
| Chave de identidade ECDSA do aparelho | Android Keystore por instalação | Não exportável; aparelho individualmente aprovado no PC |
| Sessão móvel temporária | Memória do app e do serviço PC | Expiração curta, vínculo à chave, revogação e assinatura por pedido |
| Banco de aparelhos e chaves públicas | `~/.config/mark/mobile-devices.db` no PC | Permissão privada; aprovação local; revogação |
| Histórico, áudio e anexos | App e MARK PC | TLS, autorização, limites de tamanho, backup desativado no Android |
| Token legado LocalMind | Arquivo 0600 no PC e app LocalMind | Compatibilidade temporária; risco residual até migração para autenticação por aparelho |
| Chave de assinatura do APK | Fora do repositório | Arquivo privado; nunca embarcada no APK |

Limites: rede externa → Tailscale Serve → serviço local PC; Android → Keystore; conteúdo do usuário/modelo → camada de autorização; código/repositório/CI → artefato assinado. Um cliente que afirma ter desbloqueado a biometria não pode ser tratado pelo PC como prova de presença do proprietário.

## Adversários e cenários

| Ameaça | Controle atual | Limite ou teste faltante |
| --- | --- | --- |
| Atacante da Internet ou da LAN | Serviço Python escuta 127.0.0.1; Tailscale Serve limita a tailnet | Validar configuração e firewall após reboot; HTTP 80 da tailnet ainda ativo |
| Peer malicioso na tailnet | ECDSA por aparelho, aprovação local e assinatura por pedido; token legado separado | Testar peer sem chave; migrar token legado |
| Servidor falso, DNS manipulado, MITM | HTTPS com confiança do sistema, hostname, TLS 1.3 e pin do certificado obtido via QR no PC | Testar certificado falso/expirado e downgrade no M23 |
| APK copiado ou modificado | Chave privada criada no Keystore após instalação; servidor aceita apenas chave aprovada | Instalar APK em outro aparelho e testar APK reassinado |
| Celular roubado bloqueado | Trava biométrica do app e chave por instalação | Testar revogação imediata e estado pós-reboot no M23 |
| Celular roubado desbloqueado ou app malicioso | Sandbox Android e trava do app | Testar tela recente, capturas, intents, bypasses biométricos e rotação do pin |
| Celular com root/hooking | Nenhum segredo permanente embutido no APK | OS/TEE comprometido pode permitir uso da chave em tempo real; não prometer proteção absoluta |
| Repetição ou alteração de requisição | Nonce e timestamp no PC; assinatura de método, rota, corpo e sessão | Fuzz, concorrência, relógio e testes de corpo/ação alterados |
| Intercepção ou reutilização do QR | Código de cinco minutos, uso único, prova de posse e aprovação manual no PC | Testar QR expirado/copiado/modificado; manter código fora de logs/processos |
| Conteúdo malicioso ou prompt injection | Autorização do PC independente do texto do modelo | Revisão de ações privilegiadas, anexos e testes adversariais |
| Dependência ou cadeia de build maliciosa | Versões declaradas, build release assinado | SBOM, scanner de vulnerabilidade, CI, verificação de dependências |
| GitHub ou repositório comprometido | Chave de assinatura e credenciais locais ficam fora do repositório | Auditoria de histórico, branches, CI, proteção de release e build limpo |

## Limites explícitos

Se o PC MARK inteiro for comprometido, a autoridade final também estará comprometida. Se o kernel, o TEE ou o sistema Android estiverem comprometidos, o Keystore e a biometria podem não oferecer a proteção esperada. O token legado do LocalMind e a rota HTTP 80 da tailnet impedem declarar a arquitetura totalmente conforme ao novo documento.

## Recuperação de incidente

No PC, colocar o MARK em lockdown, revogar o aparelho afetado, revisar eventos disponíveis, corrigir a exposição, e abrir um novo pareamento com código temporário. A remoção de HTTP 80 exige privilégio administrativo do sistema e deve ser confirmada no Tailscale Serve. Não usar email, voz ou texto do modelo como meio de recuperar autorização.
