package br.com.bannaman.markwear.protocol

/** Monotonic, bounded grant; screen state cannot renew authorization. */
class WearLease private constructor(private val issuedAt:Long,val expiresAt:Long) {
    companion object {
        const val MAX_MS=300000L
        fun issue(now:Long,expiry:Long):WearLease {
            require(now>=0 && expiry>now && expiry-now<=MAX_MS)
            return WearLease(now,expiry)
        }
    }
    fun valid(now:Long)=now>=issuedAt && now<expiresAt
    fun remaining(now:Long)=if(valid(now))expiresAt-now else 0L
}
