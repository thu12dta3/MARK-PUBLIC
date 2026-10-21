package br.com.bannaman.markwear.protocol

enum class ButtonAction { HEALTH, ACTIONS, APPS }
data class ButtonConfiguration(val longMs:Long=600,val doubleMs:Long=300)
/** Only handles a delivered multifunction key. Power/home is never captured. */
class ButtonDispatcher(private val config:ButtonConfiguration=ButtonConfiguration()) {
    private var down:Long?=null
    private var release:Long?=null
    fun press(now:Long,repeat:Int=0) {if(repeat==0 && down==null)down=now}
    fun release(now:Long,cancelled:Boolean=false):ButtonAction? {
        val start=down?:return null;down=null
        if(cancelled){release=null;return null}
        if(now-start>=config.longMs){release=null;return ButtonAction.ACTIONS}
        val first=release
        if(first!=null && now-first<=config.doubleMs){release=null;return ButtonAction.APPS}
        release=now;return null
    }
    fun flush(now:Long):ButtonAction? {
        val first=release?:return null
        if(now-first<config.doubleMs || down!=null)return null
        release=null;return ButtonAction.HEALTH
    }
    fun cancel(){down=null;release=null}
}
