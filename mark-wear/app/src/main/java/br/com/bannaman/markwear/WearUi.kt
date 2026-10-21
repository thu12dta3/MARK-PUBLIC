package br.com.bannaman.markwear

import android.app.Activity
import android.content.res.ColorStateList
import android.graphics.*
import android.graphics.drawable.*
import android.view.Gravity
import android.widget.*

object WearUi {
    const val INK=0xff05080b.toInt()
    const val PANEL=0xff122027.toInt()
    const val TEAL=0xff79dde0.toInt()
    const val MUTED=0xff91a4b4.toInt()
    fun dp(a: Activity,n: Int)=(n*a.resources.displayMetrics.density).toInt()
    fun scroll(a:Activity):ScrollView?=a.findViewById<android.view.ViewGroup>(android.R.id.content)?.getChildAt(0) as? ScrollView
    fun page(a: Activity,title: String,compact:Boolean=false): LinearLayout {
        val scroll=ScrollView(a).apply {setBackgroundColor(INK);isFillViewport=true;isVerticalScrollBarEnabled=false;clipToPadding=false}
        val box=LinearLayout(a).apply {
            orientation=LinearLayout.VERTICAL;gravity=Gravity.CENTER_HORIZONTAL
            val inset=if(a.resources.configuration.isScreenRound) 30 else 16
            setPadding(dp(a,inset),dp(a,if(compact)12 else 24),dp(a,inset),dp(a,32))
        }
        scroll.addView(box);a.setContentView(scroll)
        label(a,box,title,if(compact)16 else 20,Color.WHITE).apply {typeface=android.graphics.Typeface.create("sans-serif-medium",Typeface.NORMAL);if(compact)setPadding(0,0,0,0)}
        return box
    }
    fun section(a:Activity,box:LinearLayout,title:String)=label(a,box,title,12,TEAL).apply {setPadding(0,dp(a,14),0,dp(a,2))}
    fun quickRow(a:Activity,box:LinearLayout,left:String,leftGlyph:String,right:String,rightGlyph:String,leftAction:()->Unit,rightAction:()->Unit) {
        val row=LinearLayout(a).apply {orientation=LinearLayout.HORIZONTAL;isBaselineAligned=false}
        box.addView(row,LinearLayout.LayoutParams(-1,-2))
        val first=button(a,row,left,leftGlyph,leftAction);val second=button(a,row,right,rightGlyph,rightAction)
        for(button in listOf(first,second)){button.textSize=12f;button.setCompoundDrawables(null,null,null,null);button.gravity=Gravity.CENTER}
        first.layoutParams=LinearLayout.LayoutParams(0,dp(a,48),1f).apply {topMargin=dp(a,5);rightMargin=dp(a,3)}
        second.layoutParams=LinearLayout.LayoutParams(0,dp(a,48),1f).apply {topMargin=dp(a,5);leftMargin=dp(a,3)}
    }
    fun label(a: Activity,box: LinearLayout,text: String,size: Int=14,color: Int=MUTED): TextView {
        val t=TextView(a).apply {this.text=text;textSize=size.toFloat();setTextColor(color);gravity=Gravity.CENTER;setPadding(0,dp(a,5),0,dp(a,5))}
        box.addView(t,LinearLayout.LayoutParams(-1,-2));return t
    }
    fun styleButton(a:Activity,b:Button,glyph:String="") {
        b.apply {
            filterTouchesWhenObscured=true
            isAllCaps=false;textSize=14f;setTextColor(ColorStateList(arrayOf(intArrayOf(-android.R.attr.state_enabled),intArrayOf()),intArrayOf(MUTED,Color.WHITE)))
            minHeight=dp(a,48);minimumHeight=dp(a,48);maxLines=2;ellipsize=android.text.TextUtils.TruncateAt.END
            gravity=Gravity.CENTER_VERTICAL or Gravity.START
            val panel=GradientDrawable().apply {setColor(PANEL);cornerRadius=dp(a,18).toFloat();setStroke(dp(a,1),0xff294149.toInt())}
            background=RippleDrawable(ColorStateList.valueOf(0x5579dde0),panel,null)
            backgroundTintList=null
            if(glyph.isNotEmpty()){val icon=Glyph(glyph,TEAL);icon.setBounds(0,0,dp(a,20),dp(a,20));setCompoundDrawables(icon,null,null,null);compoundDrawablePadding=dp(a,8)}
            setPadding(dp(a,12),dp(a,4),dp(a,10),dp(a,4))
        }
    }
    fun button(a: Activity,box: LinearLayout,text: String,glyph: String="",action:()->Unit): Button {
        val b=Button(a).apply {this.text=text;contentDescription=text;styleButton(a,this,glyph);setOnClickListener {action()}}
        box.addView(b,LinearLayout.LayoutParams(-1,-2).apply {topMargin=dp(a,5)});return b
    }
}

/** Vector strokes; no font emoji dependency. */
class Glyph(private val type:String,private val color:Int):Drawable() {
    private val p=Paint(Paint.ANTI_ALIAS_FLAG).apply {style=Paint.Style.STROKE;strokeWidth=1.8f;strokeCap=Paint.Cap.ROUND}
    override fun draw(c:Canvas){
        c.save();c.translate(bounds.left.toFloat(),bounds.top.toFloat());c.scale(bounds.width()/24f,bounds.height()/24f);p.color=color
        when(type){
            "media"->{c.drawRect(3f,5f,21f,19f,p);c.drawLine(6f,9f,6f,15f,p);c.drawLine(10f,7f,10f,17f,p);c.drawLine(14f,10f,14f,14f,p);c.drawLine(18f,8f,18f,16f,p)}
            "play"->{c.drawPath(Path().apply {moveTo(7f,4f);lineTo(20f,12f);lineTo(7f,20f);close()},p)}
            "pause"->{c.drawRect(6f,4f,9f,20f,p);c.drawRect(15f,4f,18f,20f,p)}
            "next","previous"->{val flip=type=="previous";if(flip){c.translate(24f,0f);c.scale(-1f,1f)};c.drawPath(Path().apply {moveTo(5f,5f);lineTo(16f,12f);lineTo(5f,19f);close()},p);c.drawLine(19f,5f,19f,19f,p)}
            "apps"->{for(x in listOf(4f,14f))for(y in listOf(4f,14f))c.drawRoundRect(x,y,x+6f,y+6f,1f,1f,p)}
            "mic"->{c.drawRoundRect(8f,2f,16f,15f,4f,4f,p);c.drawArc(5f,5f,19f,19f,0f,180f,false,p);c.drawLine(12f,19f,12f,23f,p)}
            "call"->{val handset=Path().apply {moveTo(7f,3f);lineTo(3f,3f);lineTo(3f,6f);cubicTo(3f,14f,10f,21f,18f,21f);lineTo(21f,21f);lineTo(21f,16f);lineTo(16f,14f);lineTo(13f,17f);quadTo(8f,14f,6f,10f);lineTo(9f,8f);close()};c.drawPath(handset,p)}
            "chat"->{c.drawRoundRect(3f,3f,21f,18f,4f,4f,p);c.drawLine(8f,18f,5f,22f,p);c.drawLine(5f,22f,14f,18f,p);c.drawLine(7f,8f,17f,8f,p);c.drawLine(7f,12f,14f,12f,p)}
            "weather"->{c.drawCircle(9f,8f,4f,p);c.drawLine(9f,1f,9f,2f,p);c.drawLine(2f,8f,3f,8f,p);c.drawArc(5f,10f,17f,22f,180f,180f,false,p);c.drawArc(13f,13f,23f,23f,180f,180f,false,p);c.drawLine(5f,21f,23f,21f,p)}
            "calculator"->{c.drawRoundRect(5f,2f,19f,22f,2f,2f,p);c.drawRect(8f,5f,16f,9f,p);for(x in listOf(9f,15f))for(y in listOf(13f,18f))c.drawPoint(x,y,p)}
            "health"->{c.drawLine(2f,12f,7f,12f,p);c.drawLine(7f,12f,10f,5f,p);c.drawLine(10f,5f,14f,19f,p);c.drawLine(14f,19f,17f,12f,p);c.drawLine(17f,12f,22f,12f,p)}
            "status"->{c.drawRoundRect(3f,3f,21f,21f,4f,4f,p);c.drawCircle(8f,8f,1f,p);c.drawLine(7f,15f,17f,15f,p)}
            "settings"->{c.drawCircle(12f,12f,7f,p);c.drawCircle(12f,12f,3f,p);for(i in 0..7){val angle=i*Math.PI/4;c.drawLine((12+7*kotlin.math.cos(angle)).toFloat(),(12+7*kotlin.math.sin(angle)).toFloat(),(12+10*kotlin.math.cos(angle)).toFloat(),(12+10*kotlin.math.sin(angle)).toFloat(),p)}}
            "stop"->c.drawRoundRect(5f,5f,19f,19f,2f,2f,p)
            "refresh"->{c.drawArc(4f,4f,20f,20f,40f,280f,false,p);c.drawLine(18f,3f,20f,8f,p);c.drawLine(20f,8f,15f,8f,p)}
            else->{c.drawLine(17f,5f,7f,12f,p);c.drawLine(7f,12f,17f,19f,p)}
        };c.restore()
    }
    override fun setAlpha(alpha:Int){p.alpha=alpha}
    override fun setColorFilter(filter:ColorFilter?){p.colorFilter=filter}
    @Deprecated("Deprecated in Java") override fun getOpacity():Int=PixelFormat.TRANSLUCENT
}
