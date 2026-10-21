# Riscos e recuperação

STOCK SAFE é o estado atual dos componentes Google/OEM: nenhuma nova desativação aplicada nesta campanha. MARK OPTIMIZED só aceitará modificações com benefício medido e regressão negativa.

Essa afirmação descreve as alterações da expansão Companion; não significa que o aparelho estava integralmente em estado de fábrica. Uma remoção anterior de Tic Health deixou o provider de passos ausente e provocou crashes do serviço persistente MCU. Em 10/10/2026, o pacote original foi restaurado para recuperar essa dependência. Tic Health/StepProvider devem ser preservados junto ao MCU. Ver [incidente de recuperação](testing/RECOVERY_INCIDENT_20261010.md).

Antes de alterar pacote: registrar estado real, justificativa, comando individual e reversão; executar um por vez e testar Maps/Bluetooth/watchface/notificações. Não mexer em boot, GMS, Home, FSTN/MCU ou localização enquanto dependência desconhecida. Não remover sistema, resetar, desbloquear bootloader ou root.

Companion tem package separado: sua remoção não apaga dados do Mobile. Wear mantém package/assinatura para atualização. Guardar APK anterior privado antes de upgrade; downgrade Android pode exigir estratégia que preserve dados, não desinstalar automaticamente.

Nenhum segredo copiado para Companion. Sessão inicial limitada5min; nova autorização humana após processo/boot/expiração. A loja bloqueia pacotes sistema, Google/Mobvoi, MARK e downgrade. APK recebido privado, excluído ao encerrar transferência/sessão. Instalação ausente no firmware deve aparecer como limitação real, com ADB auxiliar explicitamente identificado.

Diagnósticos exportados devem excluir endereços, contas, conteúdo de notificações, tokens e conversas. Auditoria de APK proprietário depende da resposta do usuário e permanece fora do Git.
