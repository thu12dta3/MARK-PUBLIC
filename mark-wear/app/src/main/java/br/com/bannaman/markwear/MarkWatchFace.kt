package br.com.bannaman.markwear

import android.content.Intent
import android.graphics.*
import android.os.Handler
import android.os.Looper
import android.support.wearable.watchface.CanvasWatchFaceService
import android.support.wearable.watchface.WatchFaceStyle
import android.view.SurfaceHolder
import android.view.MotionEvent
import br.com.bannaman.markwear.protocol.*
import java.text.SimpleDateFormat
import java.util.*

/** Legacy API for the actual Android 9 watch, not a Wear OS 5/WFF face. */
class MarkWatchFace:CanvasWatchFaceService() {
    override fun onCreateEngine():Engine=Face()
    inner class Face:CanvasWatchFaceService.Engine() {
        private val gestures=WatchGestureRouter(resources.displayMetrics.density*48f)
        private var lastLaunch=0L
        private var faceTouchAt=-1L
        private var faceWidth=400f
        private var faceHeight=400f
        private val handler=Handler(Looper.getMainLooper())
        private val paint=Paint(Paint.ANTI_ALIAS_FLAG).apply {textAlign=Paint.Align.CENTER}
        private val clockFormat=SimpleDateFormat("HH:mm",Locale.getDefault())
        private val dateFormat=SimpleDateFormat("EEE, d MMM",Locale.getDefault())
        private val timeFont=Typeface.create("sans-serif-light",Typeface.NORMAL)
        private val labelFont=Typeface.create("sans-serif-medium",Typeface.NORMAL)
        private val chipPaint=Paint(Paint.ANTI_ALIAS_FLAG)
        private val refresh=object:Runnable {override fun run(){if(isVisible && !isInAmbientMode){invalidate();handler.postDelayed(this,60000-System.currentTimeMillis()%60000)}}}
        override fun onCreate(holder:SurfaceHolder){super.onCreate(holder);setTouchEventsEnabled(true);setWatchFaceStyle(WatchFaceStyle.Builder(this@MarkWatchFace).setAcceptsTapEvents(true).build())}
        private fun schedule(){handler.removeCallbacks(refresh);if(isVisible && !isInAmbientMode)handler.post(refresh)}
        override fun onVisibilityChanged(visible:Boolean){super.onVisibilityChanged(visible);schedule()}
        override fun onAmbientModeChanged(ambient:Boolean){super.onAmbientModeChanged(ambient);paint.isAntiAlias=!ambient;schedule();invalidate()}
        override fun onTimeTick(){super.onTimeTick();invalidate()}
        override fun onDraw(canvas:Canvas,bounds:Rect) {
            canvas.drawColor(Color.BLACK)
            faceWidth=bounds.width().toFloat();faceHeight=bounds.height().toFloat()
            val center=bounds.exactCenterX();val h=faceHeight;val width=faceWidth
            val now=Date();val zone=TimeZone.getDefault();clockFormat.timeZone=zone;dateFormat.timeZone=zone
            paint.style=Paint.Style.FILL;paint.color=Color.WHITE;paint.textSize=width*.18f;paint.typeface=timeFont
            val shift=if(isInAmbientMode)((System.currentTimeMillis()/60000)%5-2).toFloat()*2 else 0f
            canvas.drawText(clockFormat.format(now),center+shift,h*.51f+shift,paint)
            if(!isInAmbientMode){
                // Static marks and compact touch targets: no animation/polling on this low-RAM watch.
                chipPaint.style=Paint.Style.STROKE;chipPaint.strokeWidth=width*.004f;chipPaint.color=0xff294149.toInt()
                for(i in 0..11){val angle=i*Math.PI/6;val r=width*.45;val inner=width*.435
                    canvas.drawLine((center+inner*kotlin.math.sin(angle)).toFloat(),(h/2-inner*kotlin.math.cos(angle)).toFloat(),(center+r*kotlin.math.sin(angle)).toFloat(),(h/2-r*kotlin.math.cos(angle)).toFloat(),chipPaint)}
                paint.typeface=labelFont;paint.textSize=width*.045f;paint.color=WearUi.TEAL;canvas.drawText("MARK",center,h*.29f,paint)
                paint.color=WearUi.MUTED;paint.textSize=width*.045f;canvas.drawText(dateFormat.format(now),center,h*.62f,paint)
                for((i,label) in listOf("VOZ","MENU","TEMPO").withIndex()){
                    val x=width*(.27f+i*.23f);val rect=RectF(x-width*.103f,h*.70f,x+width*.103f,h*.82f)
                    chipPaint.style=Paint.Style.FILL;chipPaint.color=WearUi.PANEL;canvas.drawRoundRect(rect,width*.045f,width*.045f,chipPaint)
                    chipPaint.style=Paint.Style.STROKE;chipPaint.color=0xff294149.toInt();canvas.drawRoundRect(rect,width*.045f,width*.045f,chipPaint)
                    paint.color=WearUi.TEAL;paint.textSize=width*.035f;canvas.drawText(label,x,h*.777f,paint)
                }
            }
        }
        private fun open(page:String){
            val now=android.os.SystemClock.elapsedRealtime();if(!isVisible || isInAmbientMode || now-lastLaunch<700)return;lastLaunch=now
            startActivity(Intent(this@MarkWatchFace,MainActivity::class.java).putExtra("mark_page",page).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
        override fun onTouchEvent(event:MotionEvent){
            super.onTouchEvent(event)
            if(!isVisible || isInAmbientMode){gestures.cancel();return}
            when(event.actionMasked){MotionEvent.ACTION_DOWN->gestures.down(event.x,event.y);MotionEvent.ACTION_CANCEL->{gestures.cancel();faceTouchAt=-1};MotionEvent.ACTION_UP->{val authorized=faceTouchAt>=0 && kotlin.math.abs(faceTouchAt-event.downTime)<=500 && event.eventTime-event.downTime<=3000;faceTouchAt=-1;gestures.up(event.x,event.y,authorized)?.let {open(if(it==WatchSwipe.VOICE)"VOICE" else "HOME")}}}
        }
        override fun onTapCommand(type:Int,x:Int,y:Int,eventTime:Long){if(type==TAP_TYPE_TOUCH && isVisible && !isInAmbientMode)faceTouchAt=android.os.SystemClock.uptimeMillis(); if(type==TAP_TYPE_TAP && !isInAmbientMode && y in (faceHeight*.68f).toInt()..(faceHeight*.84f).toInt() && x in (faceWidth*.15f).toInt()..(faceWidth*.85f).toInt())open(if(x<faceWidth*.385f)"VOICE" else if(x>faceWidth*.615f)"WEATHER" else "HOME")}
        override fun onDestroy(){handler.removeCallbacksAndMessages(null);super.onDestroy()}
    }
}
