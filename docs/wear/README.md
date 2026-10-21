# MARK Wear v0.1

## Estado atual — 0.4.4-sensors-rc, 10/10/2026

[Checkpoint04](../companion/testing/CHECKPOINT_04.md): Wear atualizado,12 unitários e17 cenários selecionados no relógio PASS. Ajuste local de repouso limitado, diagnóstico dos demais sensores, número BPM e aquisição explícita45s. Calibração salva só após confirmação; BPM no pulso pendente, adiado pelo proprietário. Companion0.1.2 retornou estado/apps/sensores em sessão real; serviços sob bloqueio observados durante grant5min. Reconexão durável pendente; Wear OS by Google preservado.

## Histórico — 0.4.3-companion-rc, 10/10/2026

[Companion0.1.2 e campanha atual](../companion/testing/CHECKPOINT_03.md):51 unitários/12 cenários selecionados no Wear PASS; callbacks/navegação, vínculo pendente, estado visual e revogação refinados. Wear atualizado; Companion foi instalado/testado e o vínculo real comprovado posteriormente no checkpoint04. Vínculo real, instalador comum e estabilidade prolongada continuam sem certificação. [Campanha anterior](../companion/testing/CHECKPOINT_02.md).

## Histórico — 0.3.7, 10/10/2026

[Conexão independente da tela](BACKGROUND_LINK_V037.md): serviço no relógio e testes locais; consulta de estado com M23 apagado confirmada pelo proprietário; pergunta/voz ainda pendentes.

Refinamento de controles, navegação e visual: [relatório 0.3.6](REFINEMENT_V036.md). O checkpoint abaixo descreve também as utilidades preservadas de 0.3.5.

O texto v0.1 abaixo é histórico. Watchface MARK selecionada no TicWatch, gestos horizontais com limite ao mostrador, painéis verticais originais e Google Weather testados. Canal nativo de voz implementado, conversa completa ainda pendente com M23. Calculadora local adicionada; Spotify e Maps Wear instalados com verificação de assinaturas. Home OEM preservada. Fase permanece PARTIAL; não declarar integração permanente nem correção do HIGH do M23 bloqueado.

[Gestos e voz](WATCHFACE_GESTURES_VOICE.md), [apps e calculadora](APPS_AND_UTILITIES.md), [testes atuais](TEST_RESULTS.md).

Estado: PARTIAL. Código e integração compiláveis; nenhum teste físico/emulador executado. Alvo informado: TicWatch Pro 2020, M23 e PC. Sem root, sem servidor/modelo/Tailscale no relógio. Wear OS e display econômico permanecem sob controle do sistema.

Fluxo: relógio → MARK Mobile aberto e autorizado → API assinada do PC. Telas HOME, ASK, HEALTH, STATUS, ACTIONS, SETTINGS e Sensor Lab. Interface nativa de preto/ciano, adaptada à tela redonda. Voz manual usa o reconhecedor do sistema quando disponível; fallback é texto. Não há chamada contínua ou wake word.

Esta versão requer o mesmo package e certificado nos APKs e autorização biométrica forte no M23. A ponte dura cinco minutos e encerra ao sair da tela. Essa restrição inicial evita uma ponte privilegiada em segundo plano; não declarar integração permanente validada.

- [Arquitetura](ARCHITECTURE.md)
- [Compatibilidade](COMPATIBILITY.md)
- [Instalação e recuperação](INSTALLATION.md)
- [Design](DESIGN_SYSTEM.md)
- [Bateria](BATTERY.md)
- [Sensores](SENSORS.md)
- [Telemetria](TELEMETRY.md)
- [Segurança](SECURITY.md)
- [Testes](TESTING.md)

Relatório final: [MARK_WEAR_IMPLEMENTATION_REPORT.md](../../MARK_WEAR_IMPLEMENTATION_REPORT.md). Artefatos e logs detalhados são locais, fora do Git. v0.2/v0.3 não iniciadas.

## Continuação v0.3

[Launcher](LAUNCHER.md), [botões](BUTTON_MAPPING.md), [sistema](SYSTEM_INTEGRATION.md), [energia](POWER_OPTIMIZATION.md), [bugs](BUG_AUDIT.md), [testes](TEST_RESULTS.md), [recuperação](RECOVERY.md). O texto anterior documenta o primeiro baseline; Bluetooth direto e a watchface/launcher são a expansão atual. HIGH offline M23 bloqueado continua pendente.
