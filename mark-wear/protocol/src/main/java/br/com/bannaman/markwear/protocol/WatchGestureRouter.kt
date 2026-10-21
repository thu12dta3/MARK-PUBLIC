package br.com.bannaman.markwear.protocol
enum class WatchSwipe { VOICE, MENU }
/** Vertical gestures remain with Wear OS. */
class WatchGestureRouter(private val threshold:Float) {
 private var start:Pair<Float,Float>?=null
 fun down(x:Float,y:Float){start=x to y}
 fun cancel(){start=null}
 fun up(x:Float,y:Float,faceAuthorized:Boolean=false):WatchSwipe? {
  val point=start?:return null;start=null
  if(!faceAuthorized)return null
  val dx=x-point.first;val dy=y-point.second
  if(kotlin.math.abs(dx)<threshold || kotlin.math.abs(dx)<kotlin.math.abs(dy)*1.5f)return null
  return if(dx>0)WatchSwipe.VOICE else WatchSwipe.MENU
 }
}
