package br.com.bannaman.markwear.protocol
import org.junit.Test
import org.junit.Assert.*
class NotificationBudgetTest {
 @Test fun limitsEvents(){val b=NotificationBudget();repeat(20){assertTrue(b.accept(it.toLong()))};assertFalse(b.accept(21));assertTrue(b.accept(60000))}
 @Test fun resetAndInvalidClock(){val b=NotificationBudget();assertFalse(b.accept(-1));assertTrue(b.accept(100));assertTrue(b.accept(1))}
}
