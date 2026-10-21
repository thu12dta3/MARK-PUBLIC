package br.com.bannaman.markmobile

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.view.Surface
import android.content.res.Configuration
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.border
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.google.ar.core.Anchor
import com.google.ar.core.Frame
import com.google.ar.core.Config
import com.google.ar.core.TrackingFailureReason
import com.google.ar.core.Plane
import com.google.ar.core.TrackingState
import io.github.sceneview.ar.ARSceneView
import io.github.sceneview.ar.highestResolutionCameraConfig
import io.github.sceneview.ar.arcore.isValid
import io.github.sceneview.rememberEngine
import io.github.sceneview.rememberModelInstance
import io.github.sceneview.rememberModelLoader
import io.github.sceneview.rememberOnGestureListener
import java.util.concurrent.atomic.AtomicReference

/** ARCore opcional: ancora um marcador MARK em um plano detectado, sem serviços de nuvem. */
@Composable
fun MarkARScreen(onDismiss: () -> Unit) {
    val engine = rememberEngine()
    val context = LocalContext.current
    val modelLoader = rememberModelLoader(engine)
    val marker = rememberModelInstance(modelLoader, "models/mark-orb.glb")
    var anchor by remember { mutableStateOf<Anchor?>(null) }
    var status by remember { mutableStateOf("Explore o ambiente devagar. O mapa só existe enquanto o AR estiver aberto.") }
    var sessionReady by remember { mutableStateOf(false) }
    var hasSurface by remember { mutableStateOf(false) }
    var surfaceLabel by remember { mutableStateOf("") }
    var mappedPlaneCount by remember { mutableIntStateOf(0) }
    var depthActive by remember { mutableStateOf(false) }
    val isLandscape = LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE
    var levelRoll by remember { mutableFloatStateOf(0f) }
    val sensorManager = remember(context) { context.getSystemService(Context.SENSOR_SERVICE) as SensorManager }
    val rotationSensor = remember(sensorManager) { sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR) }
    DisposableEffect(rotationSensor) {
        val listener = object : SensorEventListener {
            override fun onSensorChanged(event: SensorEvent) {
                val rotation = FloatArray(9)
                val displayRotation = context.display?.rotation ?: Surface.ROTATION_0
                val (axisX, axisY) = when (displayRotation) {
                    Surface.ROTATION_90 -> SensorManager.AXIS_Y to SensorManager.AXIS_MINUS_X
                    Surface.ROTATION_180 -> SensorManager.AXIS_MINUS_X to SensorManager.AXIS_MINUS_Y
                    Surface.ROTATION_270 -> SensorManager.AXIS_MINUS_Y to SensorManager.AXIS_X
                    else -> SensorManager.AXIS_X to SensorManager.AXIS_Y
                }
                val remapped = FloatArray(9)
                val orientation = FloatArray(3)
                SensorManager.getRotationMatrixFromVector(rotation, event.values)
                SensorManager.remapCoordinateSystem(rotation, axisX, axisY, remapped)
                SensorManager.getOrientation(remapped, orientation)
                val degrees = Math.toDegrees(orientation[2].toDouble()).toFloat()
                levelRoll = if (kotlin.math.abs(degrees - levelRoll) > 180f)
                    if (degrees > levelRoll) levelRoll + degrees - 360f else levelRoll + degrees + 360f
                else levelRoll * 0.78f + degrees * 0.22f
                levelRoll = ((levelRoll + 180f) % 360f + 360f) % 360f - 180f
            }
            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
        }
        rotationSensor?.let { sensorManager.registerListener(listener, it, SensorManager.SENSOR_DELAY_UI) }
        onDispose { sensorManager.unregisterListener(listener) }
    }
    val latestFrame = remember { AtomicReference<Frame?>(null) }
    val placementGestures = rememberOnGestureListener(onSingleTapConfirmed = { event, node ->
        if (marker == null) {
            status = "O marcador 3D do MARK não carregou. Feche e reabra o Modo AR."
        } else {
            val frame = latestFrame.get()
            val hit = frame?.takeIf { it.camera.trackingState == TrackingState.TRACKING }
                ?.hitTest(event.x, event.y)?.firstOrNull { result ->
                    result.isValid(
                        planeTypes = Plane.Type.values().toSet(),
                        depthPoint = depthActive,
                        point = false,
                        instantPlacementPoint = false,
                    )
            }
            if (hit != null) {
                anchor?.detach()
                anchor = hit.createAnchor()
                status = "Marcador MARK colocado na mira · toque novamente para mover"
            } else {
                status = if (sessionReady) "Sem plano na mira. Aponte para qualquer superfície e mova devagar para o MARK mapear." else "O rastreamento ainda não está pronto. Mova o celular devagar e tente novamente."
            }
        }
    })
    DisposableEffect(anchor) { onDispose { anchor?.detach() } }

    Box(Modifier.fillMaxSize().background(Color(0xFF0C1219))) {
        ARSceneView(
            modifier = Modifier.fillMaxSize(),
            engine = engine,
            modelLoader = modelLoader,
            planeRenderer = true,
            planeFindingMode = Config.PlaneFindingMode.HORIZONTAL_AND_VERTICAL,
            depthMode = Config.DepthMode.AUTOMATIC,
            focusMode = Config.FocusMode.AUTO,
            sessionCameraConfig = ::highestResolutionCameraConfig,
            onSessionUpdated = { session, frame ->
                latestFrame.set(frame)
                sessionReady = frame.camera.trackingState == TrackingState.TRACKING
                val activeDepth = session.config.depthMode == Config.DepthMode.AUTOMATIC
                if (depthActive != activeDepth) depthActive = activeDepth
                val activePlanes = session.getAllTrackables(Plane::class.java)
                    .count { it.trackingState != TrackingState.STOPPED }
                if (mappedPlaneCount != activePlanes) mappedPlaneCount = activePlanes
                if (sessionReady && anchor == null) {
                    val plane = frame.getUpdatedTrackables(Plane::class.java).firstOrNull {
                        it.trackingState == TrackingState.TRACKING
                    }
                    if (plane != null) {
                        hasSurface = true
                        surfaceLabel = when (plane.type) {
                            Plane.Type.VERTICAL -> "PAREDE"
                            Plane.Type.HORIZONTAL_DOWNWARD_FACING -> "PLANO INFERIOR"
                            else -> "PLANO HORIZONTAL"
                        }
                        status = "$surfaceLabel mapeado. Toque para fixar o marcador."
                    } else if (!hasSurface && status.startsWith("Câmera conectada")) {
                        status = "Rastreamento ativo. Mova a câmera devagar e explore o espaço ao redor."
                    }
                }
            },
            onGestureListener = placementGestures,
            onTrackingFailureChanged = { reason ->
                if (reason != null) status = when (reason) {
                    TrackingFailureReason.INSUFFICIENT_LIGHT -> "Pouca luz. Ilumine o ambiente e mova o celular devagar."
                    TrackingFailureReason.INSUFFICIENT_FEATURES -> "Poucos detalhes visuais. Aponte para uma superfície com textura e mova lentamente; evite áreas lisas ou reflexivas."
                    TrackingFailureReason.EXCESSIVE_MOTION -> "Movimento rápido demais. Pare por um instante e mova o celular lentamente."
                    TrackingFailureReason.CAMERA_UNAVAILABLE -> "A câmera ficou indisponível. Feche outros apps que usam a câmera e tente novamente."
                    TrackingFailureReason.BAD_STATE -> "O rastreamento AR foi interrompido. Volte e abra o Modo AR novamente."
                    else -> "O AR ainda está calibrando a câmera. Mova o celular lentamente pelo ambiente."
                }
            },
            onSessionCreated = { status = "Câmera conectada. Mapa temporário: ao sair do AR, ele é descartado." },
            onSessionFailed = { error ->
                status = when {
                    error.message?.contains("camera", ignoreCase = true) == true -> "Não foi possível abrir a câmera do AR. Feche outros apps de câmera e tente de novo."
                    error.message?.contains("permission", ignoreCase = true) == true -> "Permissão da câmera ausente. Autorize a câmera do MARK nas configurações do Android."
                    error.message?.contains("config", ignoreCase = true) == true -> "A configuração de câmera não é aceita pelo ARCore neste aparelho: ${error.javaClass.simpleName}."
                    else -> "Falha ao iniciar AR: ${error.javaClass.simpleName}${error.message?.let { ": $it" } ?: ""}"
                }
            },
        ) {
            anchor?.let { placed ->
                AnchorNode(anchor = placed) {
                    marker?.let { ModelNode(modelInstance = it, scaleToUnits = 0.24f) }
                }
            }
        }
        Row(
            Modifier.align(Alignment.TopCenter).fillMaxWidth().padding(horizontal = 22.dp, vertical = if (isLandscape) 7.dp else 18.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Canvas(Modifier.width(2.dp).height(30.dp)) { drawRoundRect(Color(0xFF63E6D0), cornerRadius = androidx.compose.ui.geometry.CornerRadius(2.dp.toPx())) }
                Spacer(Modifier.width(10.dp))
                Column(verticalArrangement = Arrangement.spacedBy(1.dp)) {
                    Text("MARK", color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 2.sp)
                    Text("REALIDADE AUMENTADA", color = Color(0xFF9FB8C2), fontSize = 8.sp, letterSpacing = 1.1.sp)
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    Canvas(Modifier.size(6.dp)) { drawCircle(if (sessionReady) Color(0xFF63E6D0) else Color(0xFFFFC56E)) }
                    Text(
                        when {
                            hasSurface -> "$surfaceLabel · $mappedPlaneCount PLANOS"
                            sessionReady && depthActive -> "MAPA 3D · PROFUNDIDADE"
                            sessionReady -> "MAPEANDO · $mappedPlaneCount PLANOS"
                            else -> "INICIANDO"
                        },
                        color = Color(0xFFD7E5E9), fontSize = 9.sp, letterSpacing = 0.8.sp,
                    )
                }
                TextButton(onClick = onDismiss, modifier = Modifier.size(38.dp)) {
                    Text("×", color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.Light)
                }
            }
        }
        Row(
            Modifier.align(Alignment.TopCenter).padding(top = if (isLandscape) 48.dp else 67.dp)
                .background(Color(0x80101820), RoundedCornerShape(14.dp))
                .border(1.dp, Color(0x3348D4E4), RoundedCornerShape(14.dp))
                .padding(horizontal = 12.dp, vertical = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("NÍVEL", color = Color(0xFF9FB8C2), fontSize = 9.sp, letterSpacing = 1.sp)
            Spacer(Modifier.width(10.dp))
            LevelGauge(levelRoll)
        }
        Canvas(
            Modifier.align(Alignment.Center).size(112.dp)
                .semantics { contentDescription = "Mira para posicionar o marcador MARK" },
        ) {
            val scale = size.minDimension / 112f
            val center = Offset(size.width / 2f, size.height / 2f)
            val teal = if (sessionReady) Color(0xFF63E6D0) else Color(0xFFBDF5F6)
            val arm = 9f * scale
            val edge = 52f * scale
            val left = (size.width - edge) / 2f
            val top = (size.height - edge) / 2f
            val brackets = Path().apply {
                moveTo(left + arm, top); lineTo(left, top); lineTo(left, top + arm)
                moveTo(left + edge - arm, top); lineTo(left + edge, top); lineTo(left + edge, top + arm)
                moveTo(left, top + edge - arm); lineTo(left, top + edge); lineTo(left + arm, top + edge)
                moveTo(left + edge - arm, top + edge); lineTo(left + edge, top + edge); lineTo(left + edge, top + edge - arm)
            }
            drawPath(brackets, teal.copy(alpha = 0.88f), style = Stroke(width = 1.4f * scale, cap = StrokeCap.Square))
            drawCircle(teal.copy(alpha = 0.6f), radius = 12f * scale, center = center,
                style = Stroke(width = 1f * scale))
            drawLine(teal.copy(alpha = 0.5f), Offset(center.x - 22f * scale, center.y), Offset(center.x - 9f * scale, center.y), 1f * scale)
            drawLine(teal.copy(alpha = 0.5f), Offset(center.x + 9f * scale, center.y), Offset(center.x + 22f * scale, center.y), 1f * scale)
            drawCircle(Color.White, radius = 1.8f * scale, center = center)
        }
        if (isLandscape) {
            Row(
                Modifier.align(Alignment.BottomCenter).fillMaxWidth().padding(horizontal = 16.dp, vertical = 7.dp)
                    .background(Color(0xD9101820), RoundedCornerShape(15.dp))
                    .border(1.dp, Color(0x443DCBC2), RoundedCornerShape(15.dp))
                    .padding(horizontal = 12.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1.15f).padding(end = 10.dp)) {
                    Text(if (anchor != null) "ÂNCORA FIXADA" else if (hasSurface) "$surfaceLabel · $mappedPlaneCount PLANOS" else if (sessionReady) "MAPEANDO · $mappedPlaneCount PLANOS" else "AGUARDANDO AR", color = Color(0xFF63E6D0), fontSize = 8.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.8.sp)
                    Text(status, color = Color(0xFFD0DADD), fontSize = 10.sp, maxLines = 1)
                }
                Row(
                    Modifier.weight(1.45f).background(Color(0x661D2A32), RoundedCornerShape(10.dp)).padding(horizontal = 7.dp, vertical = 5.dp),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    HudSetting("ISO", "AUTO"); HudDivider()
                    HudSetting("WB", "AUTO"); HudDivider()
                    HudSetting("EV", "AUTO"); HudDivider()
                    HudSetting("FOCO", "AUTO")
                }
                if (anchor != null) {
                    TextButton(onClick = { anchor?.detach(); anchor = null; status = "Toque no ponto desejado para reposicionar" }) {
                        Text("MOVER", color = Color(0xFF63E6D0), fontSize = 9.sp, letterSpacing = 0.5.sp)
                    }
                }
            }
        } else {
            Column(
                Modifier.align(Alignment.BottomCenter).fillMaxWidth().padding(horizontal = 14.dp, vertical = 14.dp)
                    .background(Color(0xD9101820), RoundedCornerShape(20.dp))
                    .border(1.dp, Color(0x443DCBC2), RoundedCornerShape(20.dp))
                    .padding(horizontal = 13.dp, vertical = 11.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
                    Row(Modifier.weight(1f).padding(end = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Canvas(Modifier.size(6.dp)) { drawCircle(if (hasSurface || anchor != null) Color(0xFF63E6D0) else Color(0xFFFFC56E)) }
                        Spacer(Modifier.width(8.dp))
                        Column {
                            Text(if (anchor != null) "MARCADOR FIXADO" else if (hasSurface) surfaceLabel else "PROCURANDO SUPERFÍCIE", color = Color(0xFFBDF5F6), fontSize = 9.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.8.sp)
                            Text(status, color = Color(0xFFC1CDD2), fontSize = 11.sp, maxLines = 2)
                        }
                    }
                    if (anchor != null) {
                        TextButton(onClick = { anchor?.detach(); anchor = null; status = "Toque no ponto desejado para reposicionar" }) {
                            Text("MOVER", color = Color(0xFF63E6D0), fontSize = 10.sp, letterSpacing = 0.8.sp)
                        }
                    }
                }
                Row(
                    Modifier.fillMaxWidth().padding(top = 10.dp).background(Color(0x661D2A32), RoundedCornerShape(12.dp))
                        .padding(vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    HudSetting("ISO", "AUTO"); HudDivider()
                    HudSetting("WB", "AUTO"); HudDivider()
                    HudSetting("EV", "AUTO"); HudDivider()
                    HudSetting("FOCO", "AUTO")
                }
            }
        }
    }
}

@Composable
private fun HudSetting(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, color = Color(0xFF8FA7B0), fontSize = 8.sp, letterSpacing = 0.8.sp)
        Text(value, color = Color(0xFFE7F2F3), fontSize = 10.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.4.sp)
    }
}

@Composable
private fun HudDivider() {
    Box(Modifier.width(1.dp).height(22.dp).background(Color(0x3348D4E4)))
}

@Composable
private fun LevelGauge(rollDegrees: Float) {
    val levelColor = when {
        kotlin.math.abs(rollDegrees) <= 1.2f -> Color(0xFF63E6D0)
        kotlin.math.abs(rollDegrees) <= 4f -> Color(0xFFFFD166)
        else -> Color(0xFFFF8A80)
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Canvas(Modifier.width(118.dp).height(18.dp)) {
            val mid = size.width / 2f
            val y = size.height / 2f
            drawLine(Color(0x887C929B), Offset(2f, y), Offset(size.width - 2f, y), 1f)
            for (i in -4..4) {
                val x = mid + i * (size.width / 9f)
                drawLine(if (i == 0) Color(0xFF63E6D0) else Color(0x997C929B), Offset(x, y - if (i == 0) 6f else 2.5f), Offset(x, y + if (i == 0) 6f else 2.5f), 1f)
            }
            val bubbleX = (mid + (rollDegrees / 12f).coerceIn(-1f, 1f) * (mid - 7f)).coerceIn(7f, size.width - 7f)
            drawCircle(levelColor.copy(alpha = 0.35f), 6f, Offset(bubbleX, y))
            drawCircle(levelColor, 3f, Offset(bubbleX, y))
        }
        Spacer(Modifier.width(9.dp))
        Text("${if (rollDegrees > 0) "+" else ""}${"%.1f".format(java.util.Locale.ROOT, rollDegrees)}°", color = levelColor,
            fontSize = 11.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.3.sp)
    }
}
