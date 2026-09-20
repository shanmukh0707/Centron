package com.centron.sentinel.net

/**
 * Where the engine is and how we prove we are allowed to talk to it.
 *
 * Per docs/contracts.md DEPLOYMENT: the only hop that crosses the internet is
 * phone -> serverpi, over Tailscale. The engine authenticates the client with
 * a pinned self-signed certificate plus a pre-shared token. `biometric: true`
 * on an approve_action is a claim by the app about the operator; it is not
 * what authenticates the connection.
 *
 * PAIRING CODE
 * ------------
 * `tools/centron-pair.sh` on the engine prints a single line:
 *
 *     centron://<host>:<port>/<sha256-of-cert>/<token>
 *
 * That string is the pairing code. Paste it into the app, or during
 * development bake it in below. It carries everything the client needs and
 * nothing it should not have: no private key, no Anthropic key.
 */
data class EngineConfig(
    val host: String,
    val port: Int,
    /** Lowercase hex SHA-256 of the server's DER certificate. */
    val certSha256: String,
    val token: String,
    val useTls: Boolean = true,
) {
    val wsUrl: String
        get() = "${if (useTls) "wss" else "ws"}://$host:$port/ws"

    val httpBase: String
        get() = "${if (useTls) "https" else "http"}://$host:$port"

    companion object {
        /**
         * Development default: the local stub over the USB tunnel, plaintext,
         * no pinning. `adb reverse tcp:8765 tcp:8765` on the host.
         */
        val LOCAL_STUB = EngineConfig(
            host = "127.0.0.1",
            port = 8765,
            certSha256 = "",
            token = "",
            useTls = false,
        )

        /**
         * serverpi over Tailscale. The pin and token are filled in by pairing;
         * an empty pin means "not paired yet" and the client refuses to use
         * TLS blindly rather than falling back to trusting anything.
         */
        val SERVERPI = EngineConfig(
            host = "100.110.30.122",
            port = 8765,
            certSha256 = "",
            token = "",
            useTls = true,
        )

        /**
         * Parses a pairing code.
         *
         * Returns null rather than throwing on anything malformed — a typo in
         * a pasted code is a user error, not a crash.
         */
        fun fromPairingCode(code: String): EngineConfig? {
            val trimmed = code.trim().removePrefix("centron://")
            val parts = trimmed.split("/")
            if (parts.size != 3) return null

            val hostPort = parts[0].split(":")
            if (hostPort.size != 2) return null
            val port = hostPort[1].toIntOrNull() ?: return null

            val pin = parts[1].lowercase()
            if (!pin.matches(Regex("^[0-9a-f]{64}$"))) return null

            val token = parts[2]
            if (token.isBlank()) return null

            return EngineConfig(
                host = hostPort[0],
                port = port,
                certSha256 = pin,
                token = token,
                useTls = true,
            )
        }
    }
}
