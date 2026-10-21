package br.com.bannaman.markwear.protocol
import org.junit.Assert.*
import org.junit.Test
class WearPresentationTest {
    @Test fun externalEntriesNeverOpenApprovalOrDataPages(){
        assertEquals("VOICE",WearPresentation.entryPage("VOICE"))
        assertEquals("HOME",WearPresentation.entryPage(null))
        for(page in listOf("LINK","METRICS","DIAGNOSTICS","../secret"))assertEquals("HOME",WearPresentation.entryPage(page))
    }
    @Test fun connectionLabelsAreReadableAndBounded() {
        assertEquals("Celular desconectado",WearPresentation.status("PHONE OFFLINE"))
        assertEquals("Resposta recebida",WearPresentation.status("ANSWER READY"))
        assertEquals(240,WearPresentation.status("x".repeat(1000)).length)
    }
    @Test fun voiceRequiresLinkAndIdleChannel() {
        for(linked in listOf(false,true))for(busy in listOf(false,true))for(talking in listOf(false,true)) {
            assertEquals(linked && !busy && !talking,WearPresentation.canStartVoice(linked,busy,talking))
        }
        assertEquals("Conectar celular",WearPresentation.voiceAction(false,false,false))
        assertEquals("Canal ativo",WearPresentation.voiceAction(true,false,true))
        assertEquals("Aguarde a resposta",WearPresentation.voiceAction(true,true,false))
    }
    @Test fun backTrailIsBoundedAndHomeClearsIt() {
        assertEquals(listOf("HOME","SETTINGS"),WearPresentation.navigationTrail(listOf("HOME"),"SETTINGS","LAB"))
        assertTrue(WearPresentation.navigationTrail(listOf("HOME"),"LAB","HOME").isEmpty())
        assertEquals(listOf("HOME"),WearPresentation.navigationTrail(listOf("HOME"),"SETTINGS","SETTINGS"))
        assertEquals(12,WearPresentation.navigationTrail(List(30){"SETTINGS"},"LAB","STATUS").size)
    }
}
