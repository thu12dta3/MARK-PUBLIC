package br.com.bannaman.markwear.protocol
import org.junit.Test
import org.junit.Assert.*
class ButtonDispatcherTest {
 @Test fun single(){val d=ButtonDispatcher();d.press(0);assertNull(d.release(100));assertNull(d.flush(399));assertEquals(ButtonAction.HEALTH,d.flush(400));assertNull(d.flush(900))}
 @Test fun double(){val d=ButtonDispatcher();d.press(0);d.release(50);d.press(100);assertEquals(ButtonAction.APPS,d.release(150));assertNull(d.flush(500))}
 @Test fun longAndRepeat(){val d=ButtonDispatcher();d.press(0);d.press(300,1);assertEquals(ButtonAction.ACTIONS,d.release(700));assertNull(d.flush(1000))}
 @Test fun cancellation(){val d=ButtonDispatcher();d.press(0);d.release(20);d.cancel();assertNull(d.flush(500));d.press(600);assertNull(d.release(1300,true));assertNull(d.flush(2000))}
 @Test fun orphanRelease(){assertNull(ButtonDispatcher().release(500))}
}
