package br.com.bannaman.markmobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.CancellationSignal
import android.app.role.RoleManager
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Brush
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicBoolean
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import org.json.JSONObject
import com.google.ar.core.ArCoreApk
import com.google.ar.core.exceptions.UnavailableDeviceNotCompatibleException
import com.google.ar.core.exceptions.UnavailableUserDeclinedInstallationException

private val Ink = Color(0xFF0C1219)
private val Panel = Color(0xFF121E28)
private val Teal = Color(0xFF48D4E4)
private val LightTeal = Color(0xFFBDF5F6)
private val Muted = Color(0xFF91A4B4)
private val UserBubble = Color(0xFF143943)

class MainActivity : ComponentActivity() {
    private var unlocked by mutableStateOf(false)
    private var unlockError by mutableStateOf("")
    private var externalPickerOpen = false
    private var arInstallRequested = false
    private var assistRequested by mutableStateOf(false)
    private val criticalActions by lazy { CriticalActionAuthorizer(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val settings = SecureSettings(this)
        setSecureScreen(settings.secureScreen())
        // Keep the current unlock only across a configuration recreation (for example,
        // rotating the phone). A process restart still requires biometrics again.
        unlocked = (lastCustomNonConfigurationInstance as? Boolean) ?: !settings.paired()
        assistRequested = intent?.action == Intent.ACTION_ASSIST
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(
                primary = Teal,
                onPrimary = Ink,
                background = Ink,
                surface = Panel,
                onSurface = Color(0xFFE6EDF3),
                secondary = LightTeal,
                error = Color(0xFFF39A86),
            )) {
                if (unlocked) MarkHome(settings, assistRequested) { assistRequested = false }
                else LockedScreen(unlockError, onUnlock = ::requestUnlock)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == Intent.ACTION_ASSIST) assistRequested = true
    }

    override fun onStop() {
        criticalActions.cancel()
        MarkApi.clearSession()
        if (SecureSettings(this).paired() && !externalPickerOpen && !isChangingConfigurations) unlocked = false
        super.onStop()
    }

    override fun onRetainCustomNonConfigurationInstance(): Any = unlocked

    fun lock() { unlocked = false }
    fun setSecureScreen(enabled: Boolean) {
        if (enabled) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }
    fun pickerOpened() { externalPickerOpen = true }
    fun pickerClosed() { externalPickerOpen = false }
    fun requestArCoreInstall(): ArCoreApk.InstallStatus {
        val status = ArCoreApk.getInstance().requestInstall(this, !arInstallRequested)
        arInstallRequested = status == ArCoreApk.InstallStatus.INSTALL_REQUESTED
        return status
    }
    fun authorizeCriticalAction(action: String, context: String, onAuthorized: () -> Unit,
                                onDenied: (String) -> Unit) {
        criticalActions.authorize(action, context, onAuthorized, onDenied)
    }

    private fun requestUnlock() {
        val manager = getSystemService(BiometricManager::class.java)
        val authenticators = BiometricManager.Authenticators.BIOMETRIC_STRONG or
            BiometricManager.Authenticators.DEVICE_CREDENTIAL
        if (manager.canAuthenticate(authenticators) != BiometricManager.BIOMETRIC_SUCCESS) {
            unlockError = "Configure biometria ou PIN nas opções de segurança do Android."
            return
        }
        unlockError = ""
        BiometricPrompt.Builder(this)
            .setTitle("Desbloquear MARK")
            .setSubtitle("Confirme sua identidade no aparelho")
            .setAllowedAuthenticators(authenticators)
            .build()
            .authenticate(CancellationSignal(), mainExecutor, object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    unlocked = true
                }
                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    unlockError = errString.toString()
                }
            })
    }
}

@Composable
private fun LockedScreen(error: String, onUnlock: () -> Unit) {
    Column(Modifier.fillMaxSize().background(Ink).padding(32.dp),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Text("MARK", color = LightTeal, style = MaterialTheme.typography.displayMedium, fontWeight = FontWeight.Bold)
        Text("Conversa protegida neste aparelho", color = Muted, modifier = Modifier.padding(vertical = 20.dp))
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(onClick = onUnlock) { Text("Desbloquear com biometria ou PIN") }
    }
}

private data class ChatLine(val text: String, val fromOwner: Boolean, val source: String = "")

private fun originLabel(source: String): String = when {
    source.startsWith("desktop-") -> "VOCÊ · PC"
    source.startsWith("wear-") -> "VOCÊ · RELÓGIO"
    source.startsWith("mobile-") -> "VOCÊ · CELULAR"
    source.startsWith("legacy-") -> "VOCÊ · OUTRO CLIENTE"
    else -> "VOCÊ"
}

@Composable
private fun MarkHome(settings: SecureSettings, assistRequested: Boolean, onAssistHandled: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val lines = remember { mutableStateListOf<ChatLine>() }
    val listState = rememberLazyListState()
    var server by rememberSaveable { mutableStateOf(settings.serverUrl()) }
    var pin by rememberSaveable { mutableStateOf(settings.serverPin()) }
    var secureScreen by rememberSaveable { mutableStateOf(settings.secureScreen()) }
    val identity = remember { DeviceIdentity() }
    var paired by remember { mutableStateOf(settings.paired()) }
    var input by remember { mutableStateOf("") }
    var attachment by remember { mutableStateOf<ChatAttachment?>(null) }
    var cameraOpen by rememberSaveable { mutableStateOf(false) }
    var arOpen by rememberSaveable { mutableStateOf(false) }
    var pendingCameraMode by remember { mutableStateOf("photo") }
    var busy by rememberSaveable { mutableStateOf(false) }
    var settingsOpen by rememberSaveable { mutableStateOf(server.isBlank() || pin.isBlank()) }
    var status by rememberSaveable { mutableStateOf(if (!paired && server.isNotBlank())
        "Aguardando aprovação no PC; toque ↻" else "Configure o servidor MARK para começar") }
    var quick by rememberSaveable { mutableStateOf(false) }
    var agendaOpen by remember { mutableStateOf(false) }
    var profileOpen by remember { mutableStateOf(false) }
    var agendaItems by remember { mutableStateOf<List<MarkApi.AgendaItem>>(emptyList()) }
    var callOpen by rememberSaveable { mutableStateOf(false) }
    var callStatus by remember { mutableStateOf("Preparando chamada…") }
    val callActive = remember { AtomicBoolean(false) }
    val microphonePermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) callOpen = true else status = "Autorize o microfone para conversar por voz."
    }
    val cameraPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        (context as? MainActivity)?.pickerClosed()
        if (granted) {
            if (pendingCameraMode == "ar") arOpen = true else cameraOpen = true
        } else status = "Autorize a câmera para fotografar ou usar AR dentro do MARK."
    }
    val assistantRoleRequest = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        (context as? MainActivity)?.pickerClosed()
        val role = context.getSystemService(RoleManager::class.java)
        status = if (role.isRoleHeld(RoleManager.ROLE_ASSISTANT)) "MARK é o assistente padrão deste aparelho"
            else "O MARK não foi selecionado como assistente padrão"
    }
    val filePicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        (context as? MainActivity)?.pickerClosed()
        if (uri != null) scope.launch {
            runCatching { withContext(Dispatchers.IO) { AttachmentLoader.load(context, uri) } }
                .onSuccess { attachment = it; status = "Arquivo pronto para enviar" }
                .onFailure { status = it.message ?: "Não consegui anexar o arquivo" }
        }
    }

    LaunchedEffect(assistRequested, paired) {
        if (assistRequested && paired) {
            onAssistHandled()
            if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                callOpen = true
            } else microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    LaunchedEffect(callOpen) {
        if (callOpen && paired) {
            callActive.set(true)
            val audioManager = context.getSystemService(AudioManager::class.java)
            val previousMode = audioManager.mode
            audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
            try {
                while (callActive.get()) {
                    callStatus = "Ouvindo…"
                    val pcm = withContext(Dispatchers.IO) { VoiceCall.listen(callActive) }
                    if (!callActive.get()) break
                    if (pcm == null) continue
                    callStatus = "MARK está respondendo…"
                    val (wav, dismissed) = withContext(Dispatchers.IO) { MarkApi(server, pin, identity).voiceTurn(pcm) }
                    if (!callActive.get()) break
                    callStatus = "MARK está falando…"
                    withContext(Dispatchers.IO) { VoiceCall.play(context, wav, callActive) }
                    if (dismissed) {
                        callOpen = false
                        break
                    }
                }
            } catch (cancelled: kotlinx.coroutines.CancellationException) {
                throw cancelled
            } catch (exc: Exception) {
                callStatus = exc.message ?: "A chamada foi interrompida."
                callOpen = false
            } finally {
                callActive.set(false)
                audioManager.clearCommunicationDevice()
                audioManager.mode = previousMode
            }
        }
    }

    LaunchedEffect(lines.size) {
        if (lines.isNotEmpty()) listState.animateScrollToItem(lines.lastIndex)
    }

    LaunchedEffect(server, pin, paired) {
        if (server.isNotBlank() && paired && lines.isEmpty()) {
            runCatching {
                withContext(Dispatchers.IO) {
                    val api = MarkApi(server, pin, identity)
                    api.check() to api.history()
                }
            }.onSuccess { (_, history) ->
                if (lines.isEmpty()) history.forEach { turn ->
                    lines.add(ChatLine(turn.user, true, turn.source))
                    lines.add(ChatLine(turn.assistant, false))
                }
                status = "Conectado ao MARK · memória compartilhada"
            }.onFailure { status = it.message ?: "Verifique a conexão com o MARK" }
        }
    }

    if (arOpen) {
        MarkARScreen(onDismiss = { arOpen = false })
    } else if (cameraOpen) {
        CameraCaptureScreen(onDismiss = { cameraOpen = false }, onPhoto = { captured ->
            attachment = captured
            cameraOpen = false
            status = "Foto pronta para enviar ao MARK"
        })
    } else Scaffold(
        contentWindowInsets = WindowInsets.navigationBars,
        containerColor = Ink,
        topBar = {
            Column(Modifier.fillMaxWidth().background(Brush.verticalGradient(listOf(Color(0xFF172F3B), Ink)))
                .padding(horizontal = 18.dp, vertical = 10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Surface(color = Teal, shape = CircleShape, modifier = Modifier.size(12.dp)) {}
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text("MARK", color = LightTeal, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                        Text(status, color = Muted, style = MaterialTheme.typography.labelMedium,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    IconButton(modifier = Modifier.size(40.dp).semantics { contentDescription = "Atualizar conversa" }, onClick = {
                        scope.launch {
                            status = "Atualizando o histórico…"
                            runCatching { withContext(Dispatchers.IO) {
                                val api = MarkApi(server, pin, identity)
                                if (!paired) {
                                    check(api.pairStatus() == "authorized") { "Aguardando aprovação no computador." }
                                }
                                api.history()
                            } }
                                .onSuccess { history ->
                                    if (!paired) {
                                        paired = true
                                        settings.setPaired(true)
                                        (context as? MainActivity)?.lock()
                                    }
                                    lines.clear()
                                    history.forEach { turn ->
                                        lines.add(ChatLine(turn.user, true, turn.source))
                                        lines.add(ChatLine(turn.assistant, false))
                                    }
                                    status = "Conectado ao MARK · memória compartilhada"
                                }
                                .onFailure { status = it.message ?: "Não consegui atualizar" }
                        }
                    }) { MarkGlyph(MarkGlyphKind.Refresh) }
                    IconButton(modifier = Modifier.size(40.dp).semantics { contentDescription = "Configurar servidor" },
                        onClick = { settingsOpen = true }) { MarkGlyph(MarkGlyphKind.Settings) }
                    IconButton(modifier = Modifier.size(40.dp).semantics { contentDescription = "Capacidades do aparelho" },
                        onClick = { profileOpen = true }) { MarkGlyph(MarkGlyphKind.Info) }
                    IconButton(modifier = Modifier.size(40.dp).semantics { contentDescription = "Assistente padrão" }, onClick = {
                        val role = context.getSystemService(RoleManager::class.java)
                        if (role.isRoleAvailable(RoleManager.ROLE_ASSISTANT) && !role.isRoleHeld(RoleManager.ROLE_ASSISTANT)) {
                            (context as? MainActivity)?.pickerOpened()
                            assistantRoleRequest.launch(role.createRequestRoleIntent(RoleManager.ROLE_ASSISTANT))
                        } else status = if (role.isRoleHeld(RoleManager.ROLE_ASSISTANT)) "MARK já é o assistente padrão"
                            else "Este Android não oferece a escolha de assistente"
                    }, enabled = paired) { MarkGlyph(MarkGlyphKind.Assistant) }
                    IconButton(modifier = Modifier.size(40.dp).semantics { contentDescription = "Agenda" }, onClick = {
                        agendaOpen = true
                        scope.launch {
                            runCatching { withContext(Dispatchers.IO) { MarkApi(server, pin, identity).agenda() } }
                                .onSuccess { agendaItems = it }
                                .onFailure { status = it.message ?: "Não consegui abrir a agenda" }
                        }
                    }, enabled = paired) { MarkGlyph(MarkGlyphKind.Calendar) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth().padding(top = 12.dp)) {
                    OutlinedButton(onClick = { quick = false }, modifier = Modifier.weight(1f), colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = if (!quick) LightTeal else Muted,
                    )) { Text("Qwen · Chat") }
                    OutlinedButton(onClick = { quick = true }, modifier = Modifier.weight(1f), colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = if (quick) LightTeal else Muted,
                    )) { Text("Qwen · Rápido") }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth().padding(top = 6.dp)) {
                OutlinedButton(onClick = {
                        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                            callOpen = true
                        } else microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
                    }, enabled = paired, modifier = Modifier.weight(1f)) {
                    MarkGlyph(MarkGlyphKind.Call, Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Chamada")
                }
                OutlinedButton(onClick = {
                    val activity = context as? MainActivity
                    if (activity == null) {
                        status = "Não consegui iniciar a preparação do modo AR."
                    } else {
                        val availability = ArCoreApk.getInstance().checkAvailability(context)
                        if (availability.isSupported) try {
                        when (activity.requestArCoreInstall()) {
                            ArCoreApk.InstallStatus.INSTALLED -> {
                                if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
                                    arOpen = true
                                else {
                                    pendingCameraMode = "ar"
                                    activity.pickerOpened()
                                    cameraPermission.launch(Manifest.permission.CAMERA)
                                }
                            }
                            ArCoreApk.InstallStatus.INSTALL_REQUESTED ->
                                status = "O Android iniciou a instalação dos Serviços do Google Play para RA. Quando terminar, toque em Modo AR novamente."
                        }
                    } catch (_: UnavailableUserDeclinedInstallationException) {
                        status = "Para usar o AR, permita instalar ou atualizar os Serviços do Google Play para RA."
                    } catch (_: UnavailableDeviceNotCompatibleException) {
                        status = "Este aparelho ou a versão atual do Android não oferece suporte ao ARCore."
                    } catch (_: Exception) {
                        status = "Não foi possível preparar o AR. Verifique a Play Store e tente novamente."
                        } else status = if (availability == ArCoreApk.Availability.UNKNOWN_CHECKING ||
                            availability == ArCoreApk.Availability.UNKNOWN_TIMED_OUT ||
                            availability == ArCoreApk.Availability.UNKNOWN_ERROR)
                            "Verificando a compatibilidade do ARCore. Toque em Modo AR novamente em alguns instantes."
                        else "Este aparelho ou a versão atual do Android não oferece suporte ao ARCore."
                    }
                }, enabled = paired, modifier = Modifier.weight(1f)) {
                    MarkGlyph(MarkGlyphKind.Ar, Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Modo AR")
                }
                }
            }
        },
        bottomBar = {
            Column(
                modifier = Modifier.fillMaxWidth().background(Panel, RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp))
                    .border(1.dp, Color(0xFF244653), RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp))
                    .padding(horizontal = 12.dp, vertical = 10.dp),
            ) {
              if (attachment != null) {
                  Row(verticalAlignment = Alignment.CenterVertically) {
                      MarkGlyph(MarkGlyphKind.Attach, Modifier.size(18.dp))
                      Spacer(Modifier.width(6.dp))
                      Text(attachment!!.name, color = LightTeal, modifier = Modifier.weight(1f), maxLines = 1,
                          overflow = TextOverflow.Ellipsis)
                      TextButton(onClick = { attachment = null }) { Text("Remover") }
                  }
              }
              Row(verticalAlignment = Alignment.Bottom) {
                TextButton(onClick = {
                    (context as? MainActivity)?.pickerOpened()
                    filePicker.launch(arrayOf("*/*"))
                }, enabled = paired) { MarkGlyph(MarkGlyphKind.Add, Modifier.size(22.dp)) }
                IconButton(modifier = Modifier.size(48.dp).semantics { contentDescription = "Tirar foto" }, onClick = {
                    if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
                        cameraOpen = true
                    else {
                        pendingCameraMode = "photo"
                        (context as? MainActivity)?.pickerOpened()
                        cameraPermission.launch(Manifest.permission.CAMERA)
                    }
                }, enabled = paired) { MarkGlyph(MarkGlyphKind.Camera, Modifier.size(22.dp)) }
                OutlinedTextField(
                    value = input,
                    onValueChange = { input = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("Pergunte qualquer coisa", color = Muted) },
                    maxLines = 5,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text),
                    shape = RoundedCornerShape(22.dp),
                )
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = {
                        val selected = attachment
                        val question = input.trim().ifEmpty { if (selected != null) "Analise este arquivo." else "" }
                        if (question.isNotEmpty() && !busy) {
                            input = ""
                            attachment = null
                            lines.add(ChatLine(question + (selected?.let { "  [${it.name}]" } ?: ""), true, "mobile-chat"))
                            busy = true
                            status = "MARK está pensando…"
                            scope.launch {
                                runCatching {
                                    withContext(Dispatchers.IO) { MarkApi(server, pin, identity).ask(question, quick, selected) }
                                }.onSuccess {
                                    lines.add(ChatLine(it, false))
                                    status = "Conectado ao MARK"
                                }.onFailure {
                                    lines.add(ChatLine("Não consegui obter uma resposta: ${it.message ?: "erro de conexão"}", false))
                                    status = "Verifique a conexão com o MARK"
                                }
                                busy = false
                            }
                        }
                    },
                    enabled = (input.isNotBlank() || attachment != null) && !busy && server.isNotBlank() && paired,
                    shape = CircleShape,
                    modifier = Modifier.padding(bottom = 4.dp),
                ) {
                    if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    else Text("↑", style = MaterialTheme.typography.titleLarge)
                }
              }
            }
        },
    ) { padding ->
        if (lines.isEmpty()) {
            Column(
                modifier = Modifier.fillMaxSize().padding(padding)
                    .background(Brush.verticalGradient(listOf(Ink, Color(0xFF102B39), Ink)))
                    .padding(28.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Spacer(Modifier.height(12.dp))
                Text("Oi, sou o MARK.", color = LightTeal, style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold)
                Text("Sua conversa continua no PC e neste celular.", color = Muted,
                    modifier = Modifier.padding(top = 10.dp))
                Spacer(Modifier.height(24.dp))
                OutlinedButton(onClick = { input = "O que tenho hoje?" }, enabled = paired,
                    modifier = Modifier.fillMaxWidth()) { Text("Ver minha agenda") }
                OutlinedButton(onClick = { input = "De onde estou falando?" }, enabled = paired,
                    modifier = Modifier.fillMaxWidth()) { Text("Conferir dispositivo") }
                Text("Identidade protegida pelo Android Keystore", color = Muted,
                    style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 22.dp))
            }
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 14.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(lines) { line ->
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (line.fromOwner) Arrangement.End else Arrangement.Start) {
                        Surface(
                            color = if (line.fromOwner) UserBubble else Panel,
                            shape = RoundedCornerShape(18.dp),
                            modifier = Modifier.fillMaxWidth(if (line.fromOwner) .88f else .96f),
                        ) {
                            Column(Modifier.padding(horizontal = 15.dp, vertical = 11.dp)) {
                                Text(if (line.fromOwner) originLabel(line.source) else "MARK",
                                    color = if (line.fromOwner) Teal else LightTeal,
                                    style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
                                Text(line.text, modifier = Modifier.padding(top = 4.dp),
                                    color = Color(0xFFE6EDF3), style = MaterialTheme.typography.bodyLarge)
                            }
                        }
                    }
                }
                if (busy) item {
                    Text("MARK está pensando…", color = Muted, modifier = Modifier.padding(start = 8.dp, bottom = 8.dp))
                }
            }
        }
    }

    if (settingsOpen) {
        ServerSettingsDialog(
            initialServer = server,
            initialPin = pin,
            initialSecureScreen = secureScreen,
            onSecureScreenChange = { enabled, onDenied ->
                val applyChange: () -> Unit = {
                    secureScreen = enabled
                    settings.setSecureScreen(enabled)
                    (context as? MainActivity)?.setSecureScreen(enabled)
                }
                if (enabled || !settings.secureScreen()) applyChange()
                else (context as? MainActivity)?.authorizeCriticalAction(
                    "Desativar proteção de tela", "SECURE_SCREEN=OFF", applyChange, onDenied)
                    ?: onDenied("A confirmação biométrica não está disponível.")
            },
            onDismiss = { settingsOpen = false },
            onSave = { newServer, newPin, code, onDenied ->
                val requestPair: () -> Unit = {
                  scope.launch {
                    status = "Solicitando pareamento…"
                    runCatching {
                        withContext(Dispatchers.IO) { MarkApi(newServer, newPin, identity).requestPair(code) }
                    }.onSuccess {
                        server = newServer
                        pin = newPin
                        settings.saveServerUrl(newServer)
                        settings.saveServerPin(newPin)
                        paired = false
                        settings.setPaired(false)
                        settingsOpen = false
                        status = "Aprove ${identity.id().take(12)}… no PC; depois toque em ↻."
                    }.onFailure { status = it.message ?: "Não consegui solicitar pareamento" }
                  }
                }
                if (settings.serverPin().isNotBlank() || settings.serverUrl().isNotBlank()) {
                    val codeHash = java.security.MessageDigest.getInstance("SHA-256")
                        .digest(code.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
                    (context as? MainActivity)?.authorizeCriticalAction(
                        "Trocar conexão com o MARK", "SERVER=$newServer\nPIN=$newPin\nCODE_SHA256=$codeHash",
                        requestPair, onDenied) ?: onDenied("A confirmação biométrica não está disponível.")
                } else requestPair()
            },
        )
    }

    if (callOpen) {
        AlertDialog(
            onDismissRequest = { callActive.set(false); callOpen = false },
            title = { Text("Chamada com MARK") },
            text = { Text("$callStatus\n\nFale normalmente. O MARK responde pelo alto-falante ou fone conectado.") },
            confirmButton = {
                Button(onClick = { callActive.set(false); callOpen = false }) { Text("Encerrar chamada") }
            },
        )
    }
    if (agendaOpen) {
        AlertDialog(
            onDismissRequest = { agendaOpen = false },
            title = { Text("Agenda do MARK") },
            text = {
                Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (agendaItems.isEmpty()) Text("Nenhum compromisso encontrado.")
                    agendaItems.take(12).forEach { item ->
                        Text((if (item.completed) "✓ " else "• ") + item.title +
                            (item.due?.let { "\n  $it" } ?: ""), color = if (item.completed) Muted else LightTeal)
                    }
                    Text("Você pode adicionar e concluir compromissos conversando com o MARK.", color = Muted)
                }
            },
            confirmButton = { TextButton(onClick = { agendaOpen = false }) { Text("Fechar") } },
        )
    }
    if (profileOpen) {
        AlertDialog(
            onDismissRequest = { profileOpen = false },
            title = { Text("Este aparelho") },
            text = { Text(remember { DeviceProfile.describe(context) }) },
            confirmButton = { TextButton(onClick = { profileOpen = false }) { Text("Fechar") } },
        )
    }
}

@Composable
private fun ServerSettingsDialog(
    initialServer: String,
    initialPin: String,
    initialSecureScreen: Boolean,
    onSecureScreenChange: (Boolean, (String) -> Unit) -> Unit,
    onDismiss: () -> Unit,
    onSave: (String, String, String, (String) -> Unit) -> Unit,
) {
    val context = LocalContext.current
    var server by rememberSaveable { mutableStateOf(initialServer) }
    var code by remember { mutableStateOf("") }
    var pin by rememberSaveable { mutableStateOf(initialPin) }
    var note by rememberSaveable { mutableStateOf("") }
    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        (context as? MainActivity)?.pickerClosed()
        val contents = result.contents
        if (contents != null) {
            runCatching {
                val data = JSONObject(contents)
                val scannedServer = data.getString("url").trimEnd('/')
                require(scannedServer.startsWith("https://"))
                server = scannedServer
                code = data.getString("code")
                pin = data.getString("pin")
            }.onFailure { note = "QR de pareamento inválido." }
        }
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Conectar ao MARK") },
        text = {
            Column(Modifier.heightIn(max = 430.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("No PC, abra o pareamento com MARK --mobile-pair seguido do endereço HTTPS do Tailscale. Digite aqui o código temporário exibido no computador.", color = Muted)
                OutlinedTextField(server, { server = it }, label = { Text("Endereço do MARK") }, placeholder = { Text("https://seu-pc.tailnet.ts.net") }, singleLine = true)
                OutlinedTextField(code, { code = it }, label = { Text("Código de pareamento") }, singleLine = true)
                OutlinedTextField(pin, { pin = it }, label = { Text("Identidade SHA-256 do PC") }, singleLine = true)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Bloquear capturas de tela", modifier = Modifier.weight(1f))
                    Switch(checked = initialSecureScreen, onCheckedChange = { enabled ->
                        onSecureScreenChange(enabled) { note = it }
                    })
                }
                OutlinedButton(onClick = {
                    (context as? MainActivity)?.pickerOpened()
                    scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                        .setPrompt("Aponte para o QR do MARK no computador").setBeepEnabled(false))
                }) { Text("Ler QR do computador") }
                OutlinedButton(onClick = { onDismiss(); context.startActivity(Intent(context, WearBridgeActivity::class.java)) },
                    enabled = SecureSettings(context).paired()) { Text("MARK Wear · conectar relógio") }
                Text("Depois, confirme o aparelho pendente no PC com MARK --mobile-approve.", color = Muted, style = MaterialTheme.typography.labelSmall)
                if (note.isNotBlank()) Text(note, color = MaterialTheme.colorScheme.error)
            }
        },
        confirmButton = {
            Button(onClick = {
                val address = server.trim().trimEnd('/')
                val validHttps = address.startsWith("https://")
                if (!validHttps || code.isBlank() || !Regex("[a-f0-9]{64}").matches(pin)) note = "Leia o QR ou informe endereço, código e identidade SHA-256."
                else onSave(address, pin, code.trim()) { note = it }
            }) { Text("Solicitar pareamento") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}
