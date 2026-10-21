package br.com.bannaman.markmobile

import java.net.InetAddress
import java.net.Socket
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/** Platform trust and hostname checks remain active; the peer is pinned during the TLS handshake. */
class Tls13SocketFactory(pin: String) : SSLSocketFactory() {
    private val delegate: SSLSocketFactory

    init {
        require(Regex("[a-f0-9]{64}").matches(pin))
        val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        factory.init(null as KeyStore?)
        val platform = factory.trustManagers.filterIsInstance<X509TrustManager>().single()
        val pinned = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = platform.acceptedIssuers
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
                platform.checkClientTrusted(chain, authType)
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                platform.checkServerTrusted(chain, authType)
                val leaf = chain.firstOrNull() ?: throw CertificateException("Certificado do MARK ausente.")
                val actual = MessageDigest.getInstance("SHA-256").digest(leaf.encoded)
                    .joinToString("") { "%02x".format(it) }
                if (actual != pin) throw CertificateException("Identidade do servidor MARK mudou.")
            }
        }
        val context = SSLContext.getInstance("TLSv1.3")
        context.init(null, arrayOf(pinned), null)
        delegate = context.socketFactory
    }
    private fun enforce(socket: Socket): Socket {
        val ssl = socket as SSLSocket
        require("TLSv1.3" in ssl.supportedProtocols) { "Este Android não oferece TLS 1.3." }
        ssl.enabledProtocols = arrayOf("TLSv1.3")
        return ssl
    }

    override fun getDefaultCipherSuites(): Array<String> = delegate.defaultCipherSuites
    override fun getSupportedCipherSuites(): Array<String> = delegate.supportedCipherSuites
    override fun createSocket(host: String, port: Int): Socket = enforce(delegate.createSocket(host, port))
    override fun createSocket(host: String, port: Int, localHost: InetAddress, localPort: Int): Socket =
        enforce(delegate.createSocket(host, port, localHost, localPort))
    override fun createSocket(host: InetAddress, port: Int): Socket = enforce(delegate.createSocket(host, port))
    override fun createSocket(address: InetAddress, port: Int, localAddress: InetAddress, localPort: Int): Socket =
        enforce(delegate.createSocket(address, port, localAddress, localPort))
    override fun createSocket(socket: Socket, host: String, port: Int, autoClose: Boolean): Socket =
        enforce(delegate.createSocket(socket, host, port, autoClose))
}
