# Histórico do MARK Mobile

## 0.3.12-security-rc — 2026-09-30
- Ativa Depth API quando o aparelho oferece suporte e aceita DepthPoints no toque para ancorar o HUD em superfícies não planas; planos horizontais e verticais continuam sendo mapeados.
- Mostra a quantidade de planos rastreados e informa que o mapa é temporário. O mapeamento espacial permanece somente na sessão ARCore em memória e é descartado ao sair do AR; nenhum mapa é gravado em arquivo.
- Mantém a câmera traseira na maior resolução ARCore disponível a 30 fps e autofocus automático. A nitidez depende também da lente e do foco do aparelho; confirmar no M23.
- `assembleRelease lintRelease` concluídos; versionCode 18 instalado no M23 com a mesma chave de assinatura. SHA-256 do APK: `a8a02a4ea4f04cfd3cf7d9772b757a19e8c97aa5f4df44ee5d71f58e0acde5a9`.
- A nitidez física da câmera e o suporte de Depth API ainda precisam de confirmação visual no aparelho.

## 0.3.11-security-rc — 2026-09-30
- Detecção, status e posicionamento passam a cobrir todos os tipos de plano ARCore: horizontais e verticais, sem tratar mesa como caso único.
- Orientação paisagem ganha HUD compacto em uma faixa inferior horizontal; cabeçalho e nível são reespaçados para aproveitar a altura curta do visor.
- Estado explicativo usa rótulos de plano (parede/horizontal) e orientação genérica para mapear o espaço.
- Validar a leitura do HUD e o mapeamento de diferentes superfícies no M23 em retrato e paisagem.
- `assembleRelease lintRelease` aprovados; instalado por atualização no M23 (versionCode 17). SHA-256: `94ccbfafa5502d76c3ae8b326b1e227a836ef36a3f94a0de4de54ffeeaf1c3ea`.

## 0.3.10-security-rc — 2026-09-30
- Refeito o HUD AR: cabeçalho leve sem cartão grande, régua de nível alinhada ao centro, mira geométrica fina e faixa inferior única para estado, ação e leituras ISO/WB/EV/AF.
- Mais área da prévia da câmera fica desobstruída; mantidos nivelamento, persistência da tela na rotação e correção de hit test da versão 0.3.9.
- Release assinada, compilação e lint aprovados; instalada como atualização no M23 (versionCode 16). SHA-256: `6501e2d79f57645e8502ca65147420717e753fa3fabdd212849be4baf807698a`.
- A avaliação visual final no telefone continua pendente.

## 0.3.9-security-rc — 2026-09-30
- Mantém AR e chamada abertos ao girar; protege o ciclo de chamada para cancelamento de rotação não ser tratado como erro.
- Corrige a colocação: executa hit test independentemente de a cena 3D reportar um nó sob o toque e aceita apenas planos de mesa/chão voltados para cima.
- HUD ganha nível eletrônico ao vivo baseado no sensor de rotação, com bolha móvel e graus, além de leituras compactas ISO/WB/EV/AF no estilo da câmera manual Zenfone 3.
- O HUD confirma quando uma superfície horizontal foi reconhecida.
- Remove o monograma “M” gigante do estado inicial do chat.
- Release assinada e lint aprovados; instalada como atualização no Galaxy M23 (versionCode 15). SHA-256: `52cf9ecd7b80a37392b9ebf92bd7daa675b0b4bc4883b8d8fe69f76d92f4623d`.
- É necessário validar no aparelho a persistência durante a rotação, o nível e a detecção/colocação de uma superfície real.

## 0.3.8-security-rc — 2026-09-30
- Estado das telas AR, chamada e captura agora sobrevive à rotação; chamada reinicia seu ciclo de áudio sem tratar cancelamento de rotação como falha.
- Corrigido o toque no AR: o teste de plano não é mais ignorado quando o detector de cena informa um nó sob o dedo. Posicionamento exige plano horizontal superior (como a superfície de uma mesa) e rastreamento ativo.
- HUD informa quando o plano da mesa foi realmente reconhecido; feedback de busca orienta manter a superfície central e mover o aparelho devagar.
- Removido o grande monograma “M” da tela inicial sem conversa.

## 0.3.7-security-rc — 2026-09-30
- Refeito o HUD do AR com barras compactas translúcidas, indicador de estado da câmera/rastreamento, mira fina em ciano e ação de reposicionamento discreta, mantendo a imagem livre de grandes painéis.
- Mantidas as mensagens de diagnóstico de inicialização e rastreamento da versão 0.3.6.
- Release assinada e lint aprovado; instalada por atualização no Galaxy M23 (versionCode 13). SHA-256: `54ed2c149201eb9a9d3e3da6ee6a1e343b64cc5395b25aee10230e2df9eaadc1`.
- Requer validação visual no M23 para avaliar a legibilidade sobre a câmera real. Esta mudança é visual; não corrige por si só o AR que ainda não inicia.

## 0.3.6-security-rc — 2026-09-30
- Mostra falhas do ARCore e causas de perda de rastreamento em mensagens explicativas, cobrindo pouca luz, falta de textura, movimento rápido e câmera ocupada.
- O toque de posicionamento agora só tenta ancorar quando o tracking está ativo e rejeita pontos de posicionamento instantâneo que não são planos físicos.
- Erro explícito se o modelo 3D não carregar; antes o toque falhava sem explicação.
- A qualidade visual e o início real da sessão ainda exigem confirmação na tela AR do M23. Observação: a câmera de maior resolução já era o padrão da biblioteca; configurá-la explicitamente na 0.3.5 não alterou a qualidade.
- APK versionCode 12 assinado, lint aprovado e instalado como atualização no M23. SHA-256: `37d419dddb7aa8334286dc7dbbd66d0629b19f257542b5b2dea1e02a26aee2d9`.

## 0.3.5-security-rc — 2026-09-30
- Corrigido o ciclo de vida da permissão de câmera para a autenticação não interromper os diálogos do sistema ao abrir AR ou fotografia.
- Modo AR seleciona explicitamente a câmera traseira de maior resolução disponível e foco automático; tela informa estado de rastreamento e falhas detalhadas.
- Release assinada, `assembleRelease lintRelease` concluídos; instalada como atualização no Galaxy M23 (versionCode 11).
- Qualidade visual, detecção de plano e posicionamento ainda aguardam validação visual com o telefone desbloqueado.

## 0.3.4-security-rc — 2026-09-30

- Remove o aviso sobre Google Play/AR da tela de realidade aumentada; não muda a dependência de ARCore nem o instalador oficial sob demanda.
- Acrescenta uma mira quadrada translúcida com quatro cantos, anel e ponto central. A mira é acessível por leitor de tela e o toque dentro dela tenta posicionar o marcador na superfície detectada.
- APK `versionCode 10`, compilado como release, validado com lint, assinado com a mesma chave e instalado por atualização no M23.
- SHA-256 do APK: `eb8153ffdf310dbc2508ca365a77f3ecc2938fa4fff619f97b10b8a0cad90ae`.
- A conferência visual da mira e o posicionamento físico ainda aguardam desbloqueio do aparelho.

## 0.3.3-security-rc — 2026-09-30

- Corrige o fluxo oficial de instalação do ARCore: verifica se o aparelho é compatível, sinaliza a primeira solicitação como iniciada pelo usuário e confirma a instalação ao tentar novamente depois que o Android retorna da Play Store.
- O Galaxy M23 conectado já tinha o pacote `com.google.ar.core` instalado (versão 1.56.262080393); não foi necessário procurar nem instalar um APK separado.
- APK de atualização `versionCode 9`, assinado com a mesma chave. `assembleRelease lintRelease` terminou com sucesso, e a instalação por atualização no M23 foi confirmada.
- SHA-256 do APK: `fb70462af37a5b66e522a345a227a3f6c492a916578cbfccd17ab1a5a60632ad`.

## 0.3.2-security-rc — 2026-09-30

- Substitui os emojis e caracteres tipográficos usados em ações principais por ícones vetoriais próprios, nítidos em diferentes fontes e tamanhos do Android. Chamada agora usa um telefone; foto usa uma câmera; anexos, agenda, AR e controles principais também seguem o mesmo traço.
- Mantém o estado de desbloqueio durante a recriação da tela causada pela rotação do telefone. Ao voltar do segundo plano ou reiniciar o processo, a proteção biométrica continua sendo exigida.
- O botão **Modo AR** começou a solicitar a instalação/atualização oficial dos Serviços do Google Play para RA antes de abrir a sessão ARCore e, depois, pede a permissão de câmera. O ciclo completo de retorno/compatibilidade foi corrigido em 0.3.3.
- Inclui os limites de leitura adicionados na revisão local de segurança: respostas HTTPS e áudio têm tamanho máximo, respostas de áudio são validadas, redirecionamentos HTTPS são recusados e PDFs passam por limite antes de entrar no leitor nativo.
- A lista oficial do ARCore inclui o Samsung Galaxy M23 5G. No aparelho conectado, o pacote `com.google.ar.core` já estava instalado (versão 1.56.262080393), portanto não foi necessário instalar um APK separado.
- APK `0.3.2-security-rc` compilado como release, assinado com a mesma chave privada da versão anterior e instalado sobre ela sem apagar os dados. O comando `assembleRelease lintRelease` terminou com sucesso.
- A instalação e a inicialização da atividade foram confirmadas. A checagem visual dos novos ícones, a rotação no uso real e a colocação do marcador 3D ainda dependem de abrir o app com o telefone desbloqueado; para validar o AR, também é preciso tocar numa superfície.

Arquivo local: `~/Downloads/MARK-Mobile-0.3.2-security-rc.apk`.
## 0.3.5-security-rc — 2026-09-30
- Corrigido o fluxo de permissão da câmera para o bloqueio biométrico não interromper os diálogos de permissão ao abrir AR ou fotografia.
- Câmera AR traseira configurada explicitamente para a maior resolução disponível, com foco automático.
- Melhorado o estado inicial e os diagnósticos de sessão AR; a tela explica como procurar um plano e posicionar o marcador.
- Validação visual no dispositivo ainda necessária para confirmar qualidade, detecção de plano e colocação do objeto.
