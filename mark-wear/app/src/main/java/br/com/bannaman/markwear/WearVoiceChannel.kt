package br.com.bannaman.markwear
import android.content.Context
import android.content.Intent
import android.os.*
import android.speech.*
import android.speech.tts.*
import java.util.Locale

/** Visible voice conversation; no background mic or speech history. */
class WearVoiceChannel(private val context:Context,private val state:(String)->Unit,private val text:(String)->Unit,private val stopped:()->Unit={}) {
 private val handler=Handler(Looper.getMainLooper())
 private var closed=false
 private var ready=false
 private var requested=false
 private var generation=0L
 private var utterance=""
 private var recognizer:SpeechRecognizer?=null
 private var tts:TextToSpeech?=null
 private fun fail(message:String){if(closed)return;requested=false;utterance="";recognizer?.cancel();state(message);stopped()}
 private val initializationTimeout=Runnable {if(!closed && !ready)fail("A voz não iniciou. Toque para tentar novamente.")}
 init {
  handler.postDelayed(initializationTimeout,10000)
  tts=TextToSpeech(context) {result->handler.post {
   if(closed)return@post
   handler.removeCallbacks(initializationTimeout)
   if(result!=TextToSpeech.SUCCESS){fail("Voz do sistema indisponível");return@post}
   val lang=tts?.setLanguage(Locale("pt","BR"))?:TextToSpeech.LANG_NOT_SUPPORTED
   if(lang<0){fail("Português de voz não disponível no sistema");return@post}
   ready=true;if(requested)listen()
  }}
  tts?.setOnUtteranceProgressListener(object:UtteranceProgressListener(){
   override fun onStart(id:String?){}
   override fun onDone(id:String?){handler.post {if(!closed && requested && id==utterance){utterance="";listen()}}}
   @Deprecated("Deprecated in Java") override fun onError(id:String?){handler.post {if(!closed && id==utterance){fail("Falha ao reproduzir voz. Toque para tentar novamente.")}}}
  })
 }
 fun begin(){if(closed)return;requested=true;if(ready)listen() else state("Preparando voz…")}
 private fun listen(){
  if(closed || !requested)return
  if(!SpeechRecognizer.isRecognitionAvailable(context)){fail("Reconhecimento de voz indisponível");return}
  generation++;val turn=generation
  recognizer?.destroy();recognizer=SpeechRecognizer.createSpeechRecognizer(context)
  recognizer?.setRecognitionListener(object:RecognitionListener {
   override fun onReadyForSpeech(p:Bundle?){if(!closed && turn==generation && requested)state("Ouvindo · microfone ativo")}
   override fun onBeginningOfSpeech(){}
   override fun onRmsChanged(value:Float){}
   override fun onBufferReceived(buffer:ByteArray?){}
   override fun onEndOfSpeech(){if(!closed && turn==generation && requested)state("Processando fala…")}
   override fun onError(error:Int){if(!closed && turn==generation && requested){val old=recognizer;recognizer=null;old?.destroy();fail(errorMessage(error))}}
   override fun onResults(results:Bundle?){if(closed || !requested || turn!=generation)return
    val result=results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
    recognizer?.destroy();recognizer=null
    if(result.isNullOrBlank()){fail("Não ouvi uma pergunta. Toque para tentar novamente.")}else text(result)
   }
   override fun onPartialResults(p:Bundle?){}
   override fun onEvent(type:Int,p:Bundle?){}
  })
  val request=Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
   .putExtra(RecognizerIntent.EXTRA_LANGUAGE,"pt-BR").putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE,true).putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS,false)
  try {recognizer?.startListening(request)}catch(_:SecurityException){fail("Permissão de microfone não concedida")}
 }
 fun speak(answer:String){if(closed || !requested)return
  state("MARK falando…");utterance="mark-reply-${++generation}"
  if(tts?.speak(answer.take(2000),TextToSpeech.QUEUE_FLUSH,null,utterance)!=TextToSpeech.SUCCESS){fail("Resposta recebida, mas a voz falhou")}
 }
 fun failed(){fail("Pedido falhou. Confira a ponte com o celular.")}
 private fun errorMessage(code:Int)=when(code){
  SpeechRecognizer.ERROR_NO_MATCH,SpeechRecognizer.ERROR_SPEECH_TIMEOUT->"Não ouvi uma pergunta. Toque para tentar novamente."
  SpeechRecognizer.ERROR_NETWORK,SpeechRecognizer.ERROR_NETWORK_TIMEOUT->"Voz sem conexão de rede. Toque para tentar novamente."
  SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS->"Microfone não autorizado. Confira as permissões."
  SpeechRecognizer.ERROR_RECOGNIZER_BUSY->"Voz do sistema ocupada. Toque para tentar novamente."
  else->"A voz foi interrompida ($code). Toque para tentar novamente."
 }
 fun close(){if(closed)return;closed=true;generation++;requested=false;handler.removeCallbacksAndMessages(null);recognizer?.cancel();recognizer?.destroy();recognizer=null;tts?.stop();tts?.shutdown();tts=null}
}
