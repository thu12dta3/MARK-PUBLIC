package br.com.bannaman.markwear.protocol
import org.junit.Test
import org.junit.Assert.*
class WatchGestureRouterTest {
 @Test fun horizontal(){val g=WatchGestureRouter(80f);g.down(50f,200f);assertEquals(WatchSwipe.VOICE,g.up(300f,205f,true));g.down(300f,200f);assertEquals(WatchSwipe.MENU,g.up(50f,205f,true))}
 @Test fun vertical(){val g=WatchGestureRouter(80f);g.down(200f,50f);assertNull(g.up(202f,350f,true));g.down(200f,350f);assertNull(g.up(200f,50f,true))}
 @Test fun tapAndCancel(){val g=WatchGestureRouter(80f);g.down(100f,100f);assertNull(g.up(110f,100f,true));g.down(20f,100f);g.cancel();assertNull(g.up(350f,100f,true));assertNull(g.up(350f,100f,true))}
 @Test fun diagonal(){val g=WatchGestureRouter(80f);g.down(0f,0f);assertNull(g.up(120f,100f,true))}
 @Test fun outsideFaceDoesNotRoute(){val g=WatchGestureRouter(80f);g.down(20f,100f);assertNull(g.up(350f,100f));g.down(20f,100f);assertEquals(WatchSwipe.VOICE,g.up(350f,100f,true))}
}
