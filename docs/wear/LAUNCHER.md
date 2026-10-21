# MARK Launcher v0.3

## Configuração vigente — 0.3.3

Por determinação do proprietário: mostrador original como interface principal; botão superior abre a lista original de apps. MARK funciona como launcher auxiliar, acessível pelo app/atalho/watchface opcional. HomeEntry está desabilitado e a inicialização do MARK desfaz overrides habilitados deixados por versões antigas, usando a própria identidade do aplicativo. A preferência antiga é limpa e o caminho Home retorna à HomeActivity2 OEM. Não oferecer ativação de Home MARK como solução nessa configuração.

Implementação nível B: tela principal com hora, estado, ASK, HEALTH, STATUS, APPS, Ações e Configurações. Native Views, preto/cinza/ciano, ícones vetoriais; área circular com scroll. Apps: somente Activities MAIN/LAUNCHER exportadas e habilitadas, ordenadas pelo nome, até 100, ícones reduzidos para 40×40 px em worker limitado a uma tarefa pendente. Resultado tardio de navegação é ignorado.

Nível A comprovado em 08/10/2026 na revisão 0.3.1: HomeEntry é registrado habilitado, e o MARK foi selecionado como Home padrão pelo mecanismo oficial `cmd package set-home-activity`. A resolução MAIN/HOME aponta para HomeEntry; abrir MAIN/HOME e injetar KEYCODE_HOME mantém o MARK em primeiro plano. Isso valida a Home real, não apenas um app com atalhos. O botão físico superior é outro evento: comportamento OEM ainda exige confirmação humana. Não desabilitamos Home/watchface OEM.

A revisão 0.3.0 anterior não havia ativado uma Home de verdade e não cumpria essa parte da proposta. A seleção visual HOME_SETTINGS aponta para FakeSettingsActivity; não foi tratada como seleção comprovada. Nesta versão há uma tela Integrar launcher com Home/watchface/atalho do botão e recuperação.

Watchface MARK legada via CanvasWatchFaceService 2.9.0 para o API 28 observado, selecionável pelo usuário. Hora/data e toque para abrir MARK. Atualiza por minuto quando visível; ambient usa tick do sistema, branco/preto e deslocamento de pixels. Não compatível como nova distribuição Play/WFF para Wear OS moderno; biblioteca deliberadamente legada para este hardware. Não controla FSTN/Essential Mode.

[Recuperação](RECOVERY.md), [sistema](SYSTEM_INTEGRATION.md), [testes](TEST_RESULTS.md).
