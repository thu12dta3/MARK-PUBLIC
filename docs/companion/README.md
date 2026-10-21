# MARK Companion

Migração em desenvolvimento; não é substituição completa do Wear OS by Google.

[Checkpoint atual e limitações](testing/CHECKPOINT_04.md). Wear0.4.3 RC instalado; Companion0.1.2 instalado no M23, assinatura e hash conferidos, oito cenários nativos aprovados após desbloqueio. Companion0.1.1 teve quatro cenários nativos aprovados no telefone. Sessão real retornou estado/apps/sensores; serviços sob bloqueio observados durante grant5min. Associação durável e validação prolongada seguem pendentes. Wear OS by Google deve ser preservado. [Checkpoint anterior](testing/CHECKPOINT_02.md).

- [Auditoria](00-auditoria.md)
- [Arquitetura](01-arquitetura.md)
- [Matriz funcional](02-matriz-funcional.md)
- [Riscos e recuperação](03-riscos-e-recuperacao.md)
- [Pesquisa e fontes](research/REFERENCES.md)
- [Dependências Google/OEM](google-dependencies/INITIAL_CLASSIFICATION.md)

## Build reproduzível

Usar Gradle existente em mobile-android, JDK17, SDK instalado, keystore/password file privados do proprietário. Variáveis de ambiente: JAVA_HOME, ANDROID_HOME, ANDROID_SDK_ROOT, MARK_ANDROID_KEYSTORE, MARK_ANDROID_KEY_PASSWORD_FILE. Não incluir valores/chaves em arquivos versionados.

```sh
./gradlew --offline --no-daemon :companion:assembleRelease :wear:assembleRelease :companion:assembleReleaseAndroidTest :wear:assembleReleaseAndroidTest :companion:lintRelease :wear:lintRelease :companion:testReleaseUnitTest :wear-protocol:testDebugUnitTest :wear:testReleaseUnitTest
```

Companion package `br.com.bannaman.markcompanion`; Wear permanece `br.com.bannaman.markmobile`. APK de teste da loja usa `br.com.bannaman.markstoretest`, sem permissões Android, produzido/assinado localmente e incorporado ao catálogo gerado. Não publicar APK proprietário extraído ou log privado.

## Conexão de laboratório

No M23: Companion → Ouvir relógio → TicWatch. No relógio: MARK → Configurações → MARK Companion → M23. Conferir os mesmos códigos nos dois; confirmar no relógio e autorizar com biometria forte no M23. Sessão de5min independente da ponte de assistente, UUID diferente. Ainda não há associação durável/reconexão automática; não confundir com pareamento completo do sistema.

## Loja inicial

Selecione fixture própria ou APK local; confira origem, pacote, versão, checksum, signer e permissões; envie. No relógio, gerenciador Companion → Revisar APK. Em firmware suportado, solicitar instalação ao Android; no TicWatch testado a UI informa o bloqueio real e necessidade de auxiliar. Estágio recebido é validado contra API/ABI/features/signer e versão instalada; sistema/Google/Mobvoi/MARK ficam protegidos. Não há suporte a split APK, catálogo F-Droid assinado, pesquisa/categorias externas ou desinstalação nesta primeira revisão.

Se o firmware não permitir confirmação de fonte desconhecida/instalação, informar limitação em vez de afirmar sucesso. ADB serve como teste auxiliar durante desenvolvimento, não função silenciosa do APK.

## Notificações e mídia (0.1.1)

Companion → Notificações e mídia → conceder acesso na tela especial do Android. Selecionar explicitamente os aplicativos que podem enviar título e habilitar controles de mídia separadamente. Padrão: tudo desativado. Notificações secretas/contínuas não são transmitidas; limite20/minuto por sessão, fila limitada e sem armazenamento de conteúdo. Nesta revisão não há respostas ou espelhamento de remoção. O app Google original ainda pode produzir notificações duplicadas; não desativá-lo automaticamente para esconder essa limitação.

No relógio: Companion → Mídia do celular → consultar/reproduzir/pausar/anterior/próxima. Controle de sessão Android, não transmissão de áudio. Requer sessão Companion válida, acesso de notificações concedido e autorização de mídia no telefone. Teste completo depende do M23.

Diagnóstico BLE no relógio abre GATT/publicidade temporários e publica somente uma identificação pública de teste; não contém administração sem autenticação. Serve para comprovar capacidade de peripheral, não conexão/pareamento completo nem superioridade de autonomia.
