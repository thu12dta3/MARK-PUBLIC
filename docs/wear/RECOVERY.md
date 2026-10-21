# Recuperação sem reset/root

## Configuração vigente — 0.3.3

Por determinação do proprietário: mostrador original como interface principal; botão superior abre a lista original de apps. MARK funciona como launcher auxiliar, acessível pelo app/atalho/watchface opcional. HomeEntry está desabilitado e a inicialização do MARK desfaz overrides habilitados deixados por versões antigas, usando a própria identidade do aplicativo. A preferência antiga é limpa e o caminho Home retorna à HomeActivity2 OEM. Não oferecer ativação de Home MARK como solução nessa configuração.

1. MARK → Configurações → Restaurar interface original desabilita HomeEntry, limpa preferências Home deste pacote e volta à Home OEM.
2. Ações → Relógio original retorna à Home original por componente explícito. BACK sai da página e depois do MARK. Nenhum pacote OEM foi removido/desabilitado.
3. Se a UI MARK falhar, abra o launcher/configurações originais pelo sistema. A Home alternativa é opt-in e a watchface original permanece instalada/selecionável.
4. Pelo ADB autorizado (substitua SERIAL):

```sh
adb -s SERIAL shell cmd package set-home-activity --user 0 com.google.android.wearable.app/com.google.android.clockwork.home2.activity.HomeActivity2
adb -s SERIAL shell am start -a android.intent.action.MAIN -c android.intent.category.HOME
```

Último recurso: desinstalar somente o app do relógio com `adb -s SERIAL uninstall br.com.bannaman.markmobile`. Atenção: o mesmo package ID no M23 contém MARK Mobile, portanto escolha o dispositivo correto. Desinstalação não foi executada nos testes.

No M23: ponte → Revogar e encerrar, ou notificação → Encerrar. Após boot/morte do processo, a lease não restaura autorização. Relacionar novamente requer códigos e biometria forte. Nunca remover supervisão/serviços de sistema para contornar a ponte.

## Preferência Home na revisão 0.3.1

O MARK está configurado como default no relógio observado. A Home OEM permanece instalada e sua watchface original continua selecionada. O comando de recuperação acima usa o componente OEM comprovado nesse firmware; em outro relógio, consulte primeiro MAIN/HOME candidates. `pm enable/disable` de componente foi recusado ao shell neste firmware: não apresentar esse caminho como recuperador validado. A própria UI MARK consegue alterar seu alias. Nenhuma permissão protegida foi burlada.
