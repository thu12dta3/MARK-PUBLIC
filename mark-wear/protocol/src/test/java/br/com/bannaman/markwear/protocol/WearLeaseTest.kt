package br.com.bannaman.markwear.protocol
import org.junit.Assert.*
import org.junit.Test
class WearLeaseTest {
    @Test fun expiryDoesNotDependOnScreen(){val lease=WearLease.issue(10,300010);assertTrue(lease.valid(11));assertTrue(lease.valid(300009));assertFalse(lease.valid(300010));assertEquals(0L,lease.remaining(300010))}
    @Test fun grantsAreBounded(){for(expiry in listOf(9L,10L,300011L)){try{WearLease.issue(10,expiry);fail("invalid grant")}catch(_:IllegalArgumentException){}}}
    @Test fun resetClockCannotReviveGrant(){val lease=WearLease.issue(100,200);assertFalse(lease.valid(99));assertEquals(0L,lease.remaining(99));assertEquals(99L,lease.remaining(101))}
}
