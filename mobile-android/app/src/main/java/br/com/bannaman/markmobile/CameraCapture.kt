package br.com.bannaman.markmobile

import android.content.Context
import android.graphics.ImageFormat
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.LifecycleOwner
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private class PhotoCamera(private val context: Context, private val owner: LifecycleOwner) {
    private var provider: ProcessCameraProvider? = null
    private var capture: ImageCapture? = null
    private var active = true

    fun attach(view: PreviewView, onError: (String) -> Unit) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            if (!active) return@addListener
            try {
                val cameraProvider = future.get()
                val preview = Preview.Builder().build().also { it.surfaceProvider = view.surfaceProvider }
                val still = ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY).build()
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, still)
                provider = cameraProvider
                capture = still
            } catch (exc: Exception) {
                onError("Não consegui abrir a câmera: ${exc.javaClass.simpleName}.")
            }
        }, context.mainExecutor)
    }

    fun photograph(onPhoto: (ByteArray, Int) -> Unit, onError: (String) -> Unit) {
        val still = capture ?: run { onError("A câmera ainda está iniciando."); return }
        still.takePicture(context.mainExecutor, object : ImageCapture.OnImageCapturedCallback() {
            override fun onCaptureSuccess(image: ImageProxy) {
                try {
                    require(image.format == ImageFormat.JPEG) { "Formato inesperado da câmera." }
                    val buffer = image.planes.first().buffer
                    require(buffer.remaining() in 1..12_000_000) { "Foto grande demais." }
                    val bytes = ByteArray(buffer.remaining())
                    buffer.get(bytes)
                    onPhoto(bytes, image.imageInfo.rotationDegrees)
                } catch (exc: Exception) {
                    onError(exc.message ?: "Não consegui ler a foto.")
                } finally {
                    image.close()
                }
            }

            override fun onError(exception: ImageCaptureException) {
                onError("Falha ao fotografar: ${exception.imageCaptureError}.")
            }
        })
    }

    fun close() {
        active = false
        provider?.unbindAll()
        capture = null
    }
}

@Composable
fun CameraCaptureScreen(onDismiss: () -> Unit, onPhoto: (ChatAttachment) -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    val camera = remember { PhotoCamera(context, context as LifecycleOwner) }
    var status by remember { mutableStateOf("Aponte a câmera e toque em Fotografar") }
    var busy by remember { mutableStateOf(false) }
    DisposableEffect(camera) { onDispose { camera.close() } }
    Column(Modifier.fillMaxSize().background(Color(0xFF0C1219))) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(onClick = onDismiss) { Text("← Voltar") }
            Text("Foto para o MARK", color = Color(0xFFBDF5F6), modifier = Modifier.padding(14.dp))
        }
        AndroidView(factory = { ctx ->
            PreviewView(ctx).also { view ->
                view.scaleType = PreviewView.ScaleType.FILL_CENTER
                camera.attach(view) { status = it }
            }
        }, modifier = Modifier.fillMaxWidth().weight(1f))
        Text(status, color = Color(0xFFBDF5F6), modifier = Modifier.padding(12.dp))
        Button(onClick = {
            busy = true
            status = "Capturando foto…"
            camera.photograph(onPhoto = { bytes, rotation ->
                scope.launch {
                    runCatching { withContext(Dispatchers.Default) {
                        AttachmentLoader.fromJpeg(bytes, rotation)
                    } }.onSuccess(onPhoto)
                        .onFailure { status = it.message ?: "Não consegui preparar a foto." }
                    busy = false
                }
            }, onError = { status = it; busy = false })
        }, enabled = !busy, modifier = Modifier.fillMaxWidth().padding(12.dp)) {
            Text("Fotografar e anexar")
        }
    }
}
