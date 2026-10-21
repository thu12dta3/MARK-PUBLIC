# Energia e limites medidos

## Configuração vigente — 0.3.3

Por determinação do proprietário: mostrador original como interface principal; botão superior abre a lista original de apps. MARK funciona como launcher auxiliar, acessível pelo app/atalho/watchface opcional. HomeEntry está desabilitado e a inicialização do MARK desfaz overrides habilitados deixados por versões antigas, usando a própria identidade do aplicativo. A preferência antiga é limpa e o caminho Home retorna à HomeActivity2 OEM. Não oferecer ativação de Home MARK como solução nessa configuração.

Sem polling de reconnect, GPS contínuo, microfone de espera ou controle da tela secundária. Sensors sob demanda e timeout máximo de 15 s; param ao ocultar UI. Display fica acordado apenas durante a comparação de código por até 60 s. Watchface tem relógio de minutos, sem animação contínua; timer removido em ambient/invisível/destroy.

Bluetooth: leitura bloqueante, fila de escrita quatro, fila de pedidos Mobile dois, lease biométrico de até cinco minutos e foreground notification no telefone. Sem WAKE_LOCK para evitar Doze, sem ignorar economia de bateria. M23 bloqueado continua sendo teste aberto HIGH, não declarar corrigido só pelo serviço existir.

Snapshot técnico opt-in local e limitado: tempo ativo, CPU do processo (não energia), interações, ativações de voz, bytes do protocolo (não todo tráfego Bluetooth), erros, conexões e tempo registrado de sensor. Uma única string substituída por sessão; sem áudio/saúde/identificadores. Fila enviada ao PC preserva schema anterior battery/charging/foreground_ms.

Campanha bateria PENDING: original, MARK idle, normal, active por duração comparável, brilho/rede/carga e temperatura registrados privadamente. Não há economia ou autonomia atribuível ao MARK medida. Watchface pode modificar comportamento OEM: FSTN e Essential Mode exigem conferência física; não foram alterados via hacks.
