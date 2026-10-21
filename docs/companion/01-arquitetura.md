# Arquitetura da migração

- Mobile mantém assistente e canal PC; permissões/chaves não são copiadas automaticamente para outro APK.
- Companion é APK separado, sem dependência de Mobile aberto ou PC para inventário/loja/estado.
- Wear reaproveita interface, protocolo e criptografia atuais; gerenciamento local separado da autorização do assistente.
- Gradle agrega :companion e :store-test sem recriar wrapper/SDK/protocolo.
- Canal de gerenciamento possui UUID independente; não disputa socket da ponte do assistente. Primeira revisão exige confirmação dos códigos e sessão limitada5min. Associação durável e reconexão autenticada ainda precisam implementação e testes, não ampliar grant do assistente como atalho.
- BLE é prioridade de investigação; RFCOMM serve como caminho transitório já comprovado no hardware. Não alegar superioridade energética sem ensaio comparativo.
- Loja envia arquivo limitado, com ACK por fragmento e SHA256; verifica identidade, assinatura, minSDK, ABI, downgrade e pacotes protegidos. Instalação exige revisão local no relógio e confirmação Android quando disponível. Resultado real validado contra PackageManager.
- Notificações/mídia só com acesso explicitamente concedido na tela do Android. Nenhum shell genérico, nenhum ADB exposto por APK.

## Etapas restantes

Autorização durável/reconexão, transporte BLE real, catálogo externo assinado, chamadas, integração compartilhada com Mobile, migração e remoção das responsabilidades antigas só após paridade. FSTN/Essential/Maps/boot e bateria prolongada exigem campanha física própria.

## Segundo plano e substituição do Wear OS

Requisito reafirmado pelo proprietário em10/10: Companion deve funcionar em segundo plano. Sessão real observada com serviços foreground ativos sob bloqueio; gerenciamento expira em5min. Associação durável/reconexão devem ser implementadas sem transformar autorização administrativa transitória em grant indefinido. O proprietário deve ser avisado quando a paridade necessária estiver comprovada; Wear OS by Google não foi autorizado para remoção imediata e permanece necessário nesta etapa. Ver checkpoint04.
