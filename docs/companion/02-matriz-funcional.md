# Matriz verificável

| Função | Mecanismo original/conhecimento | Alternativa MARK | Dependência/viabilidade | Comprovação atual |
|---|---|---|---|---|
| Ponte MARK | RFCOMM + protocolo próprio | Reuso canal independente Companion | Bluetooth do Android, sem Data Layer | Canal Companion real retornou estado/apps/sensores; serviços ativos sob bloqueio durante sessão5min; reconexão durável pendente |
| Notificações | Listener telefone + Wear OS | NotificationListener com consentimento, envio cifrado | Acesso especial necessário | Pendente |
| Mídia | Sessões de mídia telefone | MediaSessionManager com acesso de notificações | Sessões públicas; DRM não alterado | Pendente |
| Chamadas | Serviços OEM/telefonia | Aviso/controles permitidos Android | Não presumir áudio HFP reproduzível | Pendente |
| Loja | PackageInstaller/Play Store | Transferência validada + instalação no Wear | Instalador deste firmware exibe incompatibilidade Android Wear | Recepção/validação testadas; instalação Companion bloqueada; USB auxiliar demonstrado |
| Maps | Maps10.43.2 + GMS16.0.89 | Abrir aplicativo existente | Preservar localização/GMS | Funcionalidade completa não certificada |
| Watchface/AMOLED | Home/Wear framework | MARK watchface existente | Home OEM conservada | Seleção/gestos testados anteriormente |
| FSTN/Essential | MCU e serviços Mobvoi | Preservar implementação original | MCU depende comprovadamente do StepProvider/Tic Health; outras dependências privilegiadas não isoladas | Tic Health restaurado após crash persistente; FSTN/Essential não certificados |
| Sensores | SensorManager | Leitura existente sob demanda | BODY_SENSORS para pulso | Não alegar calibração clínica |
| Pareamento após reset | Wizard Google/OEM | Pesquisa futura | Privilégios/protocolos não conhecidos | Não suportado/comprovado; nenhum reset |
| Independência do Mobile | Ponte hoje no Mobile | Companion separado | Novo APK/identidade | Estado/inventário real pelo Companion separado comprovado; paridade completa pendente |

Níveis1–4 permanecem incompletos. Existência de tela/build não equivale a preservação funcional.
