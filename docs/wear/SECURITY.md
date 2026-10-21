# Segurança e privacidade

Wear não contém endpoint/chave/token do PC. Package e assinatura iguais são pré-requisitos do Data Layer, não aprovação privilegiada. Usuário escolhe celular; M23 escolhe relógio e exige nova biometria forte. A aprovação vale cinco minutos, apenas com Activity visível. Sair, revogar ou processo reiniciar encerra. Geração de sessão impede entregar resposta de uma autorização anterior após revogação/reaprovação.

Ponte não é exportada. Único peer escolhido, paths fixos, payload limitado, validação de tipo/tamanho/TTL, ids e replay, executor/fila limitados. Não há ações admin, root, wakeword permanente, serviço em segundo plano, gravação bruta ou sincronização contínua. Resposta curta; dados sensíveis necessários à consulta podem transitar por infraestrutura Google. Reconhecedor externo de voz também pode usar nuvem: usar texto quando isso não for desejado.

Reutiliza TLS 1.3, pin, Android Keystore, assinatura do corpo e sessão do Mobile. O PC verifica autorização/revogação/lockdown por pedido. Rotas Wear exigem aparelho assinado, recusando Bearer legado. Origem wear é parte do JSON assinado e não confere privilégio. Apagar memória/desligar continuam bloqueados para dispositivo móvel; ações críticas exigem fluxo do proprietário no PC/M23. Presença/voz do relógio não equivalem a autenticação humana.

Saúde local opcional sem histórico; telemetria opt-in sem dados clínicos. Apps sem backup e tela protegida; dados técnicos privados no PC. Não publicar diagnósticos brutos, serial, GPS, biometria, voz, conversas ou credenciais. Assinatura do APK foi reutilizada, não houve cópia de chave para Wear.

Limites: Android comprometido/owner key roubada não é resolvido pelo Data Layer; dívidas existentes de Mobile em ../SECURITY_DEBT.md permanecem. Sem revisão criptográfica externa ou campanha física, estado PARTIAL. Não declarar SECURITY COMPLETE.
