# Classificação conservadora — ainda sem desativações

| Grupo observado | Tratamento inicial | Razão |
|---|---|---|
| com.android.bluetooth; apps.wearable.bluetooth; wearable.app; setupwizard | Preservar | Bluetooth, Home/configuração/pareamento; separar responsabilidades por componente exige teste |
| wearable.ambient; apps.wearable.systemui; settings; overlays; frameworkpackagestubs | Preservar | Interface/ambiente/integração da ROM; não classificar como telemetria pelo nome |
| com.google.android.gms; gsf; Maps | Preservar | Maps antigo/localização/contas; dependências efetivas ainda não isoladas |
| Mobvoi MCU/system/watchface/appsservice/companion | Desconhecido ou essencial até prova | FSTN/Essential/botões e sincronização podem depender desses serviços |
| Tic Health / `com.mobvoi.ticwear.steps` | Preservar: dependência comprovada | Serviço persistente MCU caiu repetidamente sem StepProvider; pacote restaurado em 10/10/2026 |
| Wearable phone; deskclock; inputmethod; handwriting; TTS | Funções importantes | Chamadas, alarmes, texto/voz; nenhuma substituição comprovada |
| retailattractloop; gestos tutorial; reminders; partnersetup | Candidatos a investigar | Não significa dispensável; registrar estado/permissões/processos e beneficio antes de alteração |

STOCK SAFE preservado. MARK OPTIMIZED ainda sem benefícios medidos. Consentimento do proprietário para testes individuais/reversíveis obtido, mas condição funcional prévia ainda pendente. Não desativar aplicativo Google antes de paridade das funções essenciais.

STOCK SAFE acima descreve ausência de novas desativações na expansão Companion, não um inventário integral de fábrica. A remoção anterior de Tic Health foi revertida após comprovar dependência do MCU. Ver [incidente](../testing/RECOVERY_INCIDENT_20261010.md); os componentes OEM não podem ser classificados como dispensáveis apenas pela aparência de aplicativo de saúde.
