# Botões físicos

## Configuração vigente — 0.3.3

Por determinação do proprietário: mostrador original como interface principal; botão superior abre a lista original de apps. MARK funciona como launcher auxiliar, acessível pelo app/atalho/watchface opcional. HomeEntry está desabilitado e a inicialização do MARK desfaz overrides habilitados deixados por versões antigas, usando a própria identidade do aplicativo. A preferência antiga é limpa e o caminho Home retorna à HomeActivity2 OEM. Não oferecer ativação de Home MARK como solução nessa configuração.

Inspeção real do dispositivo: gpio-keys.kl contém STEM_1 para o botão auxiliar. Isso comprova o mapeamento do driver, não a entrega física em todas as telas.

| Evento entregue à Activity | Ação |
|---|---|
| STEM_1 simples | Saúde, após janela de 300 ms |
| STEM_1 longo ≥600 ms | Ações |
| STEM_1 duplo ≤300 ms entre releases | Aplicativos |
| Repetição de DOWN | Ignorada |
| UP cancelado / onStop | Estado cancelado |
| STEM_PRIMARY / POWER | Não interceptado; sistema |

ButtonDispatcher é puro, com configuração e cinco testes de regressão. Sem hooks globais, Accessibility, root ou serviço de captura. Só funciona quando o sistema entrega o evento em primeiro plano. Botão superior, tela apagada e watchface seguem o OEM; configurar o atalho permitido pelo sistema para abrir MARK é uma alternativa, ainda não comprovada.

Voz tem alternativa explícita pela interface ASK → Usar voz. Um botão físico não autentica o proprietário. Pressionar e falar não dá autorização administrativa.

Referência primária: https://developer.android.com/training/wearables/user-input/physical-buttons
