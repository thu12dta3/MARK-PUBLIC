# Integração com sistema

## Configuração vigente — 0.3.7

A conexão Bluetooth aprovada pertence ao serviço WearLinkService, independente da Activity. [Detalhes e limites](BACKGROUND_LINK_V037.md).

[Refinamento da interface e validação local](REFINEMENT_V036.md). A Home OEM continua vigente.

Home OEM continua principal; watchface MARK foi selecionada pelo proprietário. Botão superior permanece com a lista original de apps. Gestos horizontais no mostrador e os atalhos inferiores abrem MARK/voz/clima; gestos verticais continuam com painéis OEM. HomeEntry está desabilitado e a inicialização do MARK desfaz overrides habilitados deixados por versões antigas, usando a própria identidade do aplicativo. A preferência antiga é limpa e o caminho Home retorna à HomeActivity2 OEM. Não oferecer ativação de Home MARK como solução nessa configuração. [Detalhes e limites dos testes](WATCHFACE_GESTURES_VOICE.md).

Inspeção ADB em 08/10/2026: Android 9/API 28, ARM32, tela 400×400. HOME resolve para HomeActivity2 do Wear OS; existem também RetailHomeActivity e FallbackHome. HOME_SETTINGS resolve para FakeSettingsActivity. Essa tela não comprova seleção de Home. Na revisão 0.3.1 o mecanismo oficial de preferência de Home aceitou o MARK; MAIN/HOME e KEYCODE_HOME abriram HomeEntry, com Activity em primeiro plano verificada por dumpsys.

A experiência de Home real da revisão 0.3.1 foi desfeita após regressão informada pelo proprietário. A configuração vigente usa HomeActivity2 OEM e HomeEntry MARK desabilitada. Pressão física do botão superior ainda é UNVERIFIED; não confundir KEYCODE_HOME injetado com STEM_PRIMARY físico. Retorno ao relógio escolhe uma Activity HOME original exportada, excluindo o pacote MARK. Settings usa ACTION_SETTINGS. Apps usa MAIN/LAUNCHER explícito, sem listar serviços internos.

Nenhum gesto global, notificações, recentes, Bluetooth ou tela FSTN é substituído. MARK não expõe uma intent privilegiada para abrir notificações: usa navegação original do Wear OS. Os aplicativos de saúde solicitados pelo proprietário foram removidos somente do usuário, com possibilidade de restauração; [inventário específico](APPS_AND_UTILITIES.md).

Preferências sobrevivem a boot. Nenhum receiver inicia Activity no boot; lease Bluetooth não é restaurado. Morte do processo exige nova autorização. Watchface, quando selecionada pelo proprietário, tem ciclo de vida gerenciado pelo sistema.
