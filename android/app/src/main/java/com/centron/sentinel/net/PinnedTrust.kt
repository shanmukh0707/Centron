package com.centron.sentinel.net

import okhttp3.OkHttpClient
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSession
import javax.net.ssl.X509TrustManager

/**
 * Trust exactly one certificate, identified by its SHA-256.
 *
 * Why not OkHttp's CertificatePinner: pinning runs *after* the platform has
 * already built and validated a chain to a trusted root. The engine's
 * certificate is self-signed, so that validation fails before any pin is
 * consulted, and the connection is rejected. Pinning complements the CA system;
 * it does not replace it.
 *
 * So the trust manager below replaces the CA system outright for this one
 * connection. It trusts a certificate if, and only if, its DER encoding hashes
 * to the pin that came out of pairing. That is strictly narrower than the
 * public CA set: no certificate authority can issue something this accepts.
 *
 * This is deliberately NOT a "trust all certificates" shim. Those show up in
 * Android codebases constantly and they turn TLS into an unauthenticated
 * tunnel. If the pin is empty we refuse to build a TLS client at all rather
 * than silently degrading.
 */
object PinnedTrust {

    class MissingPin : IllegalStateException(
        "No certificate pin. Pair with the engine before connecting over TLS."
    )

    private class SinglePinTrustManager(private val expectedSha256: String) : X509TrustManager {

        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            val leaf = chain?.firstOrNull()
                ?: throw CertificateException("Server presented no certificate")

            val actual = sha256Hex(leaf.encoded)
            if (!actual.equals(expectedSha256, ignoreCase = true)) {
                throw CertificateException(
                    "Certificate does not match the paired engine. " +
                        "Expected $expectedSha256, got $actual. " +
                        "If the engine's certificate was regenerated, re-pair."
                )
            }
            // Expiry is still worth enforcing: a pinned but expired cert
            // usually means the engine was rebuilt and nobody re-paired.
            leaf.checkValidity()
        }

        /** The phone is never a TLS server here. */
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            throw CertificateException("Client authentication is not used")
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    /**
     * Hostname verification against the address we intended to reach.
     *
     * The cert's SAN must carry serverpi's Tailscale address — a cert issued
     * only for the LAN address is rejected the moment the phone connects from
     * the venue, which is the failure contracts.md calls out. Since the pin
     * already fixes *which* certificate is acceptable, this check is about
     * catching a redirect to a different host, not about identity.
     */
    private class ExpectedHostVerifier(private val expectedHost: String) : HostnameVerifier {
        override fun verify(hostname: String?, session: SSLSession?): Boolean =
            hostname != null && hostname.equals(expectedHost, ignoreCase = true)
    }

    fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { "%02x".format(it) }

    /**
     * Applies pinning to a builder. Plaintext configs pass through untouched
     * so local stub development keeps working.
     */
    fun apply(builder: OkHttpClient.Builder, config: EngineConfig): OkHttpClient.Builder {
        if (!config.useTls) return builder
        if (config.certSha256.isBlank()) throw MissingPin()

        val trustManager = SinglePinTrustManager(config.certSha256)
        val sslContext = SSLContext.getInstance("TLS").apply {
            init(null, arrayOf(trustManager), java.security.SecureRandom())
        }

        return builder
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            .hostnameVerifier(ExpectedHostVerifier(config.host))
    }
}
