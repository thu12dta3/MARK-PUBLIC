# System Bridge futuro — somente conceito

Estado PLANNED. Nenhuma mudança de sistema foi executada. A v0.1 usa aplicativo normal, sem registrar HOME, trocar watchface, remapear tecla ou controlar o display secundário.

Primeiro diagnosticar versão/OEM: intents HOME e escolha pelo usuário, comportamento dos botões, navegação/retorno ao watchface, APIs de Tiles/watchface e gerenciamento de energia. Se não houver suporte oficial, manter experiência launcher dentro do app convencional/atalho.

Um eventual privilégio adicional só se justificaria por benefício demonstrado impossível nas APIs comuns, como integração OEM específica; ainda não há benefício comprovado neste TicWatch. Riscos incluem perda de atualizações, enfraquecimento de Keystore/atestado, bootloop, consumo, exposição de saúde e perda do modo econômico. Root/Magisk/bootloader/ROM/partições/desativação de serviços estão fora desta entrega.

Antes de qualquer etapa futura: autorização explícita, prova do benefício, procedimento de recuperação com firmware compatível e backup, avaliação energética/segurança e plano de atualização. Não supor que presença física do relógio autoriza ação crítica. Preferir SDK oficial/aplicativo normal sempre que possível.
