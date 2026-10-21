# Compatibilidade

## Configuração vigente — 0.3.3

Por determinação do proprietário: mostrador original como interface principal; botão superior abre a lista original de apps. MARK funciona como launcher auxiliar, acessível pelo app/atalho/watchface opcional. HomeEntry está desabilitado e a inicialização do MARK desfaz overrides habilitados deixados por versões antigas, usando a própria identidade do aplicativo. A preferência antiga é limpa e o caminho Home retorna à HomeActivity2 OEM. Não oferecer ativação de Home MARK como solução nessa configuração.

## Evidência disponível

Nenhum aparelho conectado a adb devices na auditoria. TicWatch Pro 2020/WF12106 e M23 são informados pelo proprietário; versão real de Wear OS, API, ABI, sensores, GMS e recursos OEM permanecem UNVERIFIED / PHYSICAL TEST REQUIRED. Não usar ficha técnica como prova.

| Componente | Base implementada | Estado real |
| --- | --- | --- |
| APK Wear | minSdk 25, targetSdk 35, compileSdk 37; telas nativas Android | IMPLEMENTED; instalação UNVERIFIED |
| Data Layer | play-services-wearable 20.0.1, manifesto da AAR exige API 23 | IMPLEMENTED; GMS/pareamento físico UNVERIFIED |
| Mobile | minSdk 31 preservado, release 0.3.13-wear-bridge-rc | IMPLEMENTED; atualização M23 UNVERIFIED |
| Voz | intent oficial do reconhecedor disponível; texto como fallback | PARTIAL; serviços reais UNVERIFIED |
| Sensores | SensorManager TYPE_ALL; leituras limitadas | IMPLEMENTED; hardware UNVERIFIED |
| Watchface/Tiles | Nenhuma registrada/instalada | PLANNED, condicionada ao diagnóstico |
| Launcher | App convencional no launcher do sistema | IMPLEMENTED; HOME/OEM/botões UNVERIFIED |

A opção de Views nativas evita exigir Compose for Wear/versões recentes antes de conhecer o relógio. Não há bibliotecas nativas no módulo Wear; ABI e memória reais devem ser conferidas. Compilação não prova instalação, compatibilidade ou fluidez.

Diagnóstico no app inclui API/release/ABI, memória disponível, armazenamento, resolução/densidade/tela redonda, sensores, áudio e serviços Google. Diagnóstico ADB privado usa scripts/wear_diagnose.py; não publicar dumps sem sanitização.

Watch Face Format depende da versão do sistema. [Tabela oficial de versões](https://developer.android.com/training/wearables/wff) associa WFF v1 a Wear OS 4/API 33. Sem diagnóstico, manter watchface original. Alternativa legada privada só deve ser escolhida após conferir restrições de instalação e suporte do sistema real. Tiles e seleção HOME também dependem da versão/OEM; nada foi remapeado.

## Inspeção física v0.3 — 08/10/2026

Android 9/API 28, ARM32, display 400×400. Home original resolve normalmente; HOME_SETTINGS aponta para tela FakeSettingsActivity do OEM. Home alternativa permanece UNVERIFIED. STEM_1 consta no keylayout do botão auxiliar; botão superior reservado. Watchface usa suporte legado 2.9.0, não WFF moderno. Requisito SDK mínimo 25 preservado. FSTN/Essential Mode exigem teste físico.

Correção 0.3.1: Home replacement via mecanismo oficial de preferência é SUPPORTED para MAIN/HOME neste firmware. Ver LAUNCHER e TEST_RESULTS. Physical STEM_PRIMARY/dual display continuam UNVERIFIED.
