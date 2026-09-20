package com.centron.sentinel.net

/**
 * Connection health, derived ONLY from heartbeat gap.
 *
 * The contract is explicit about this: a TCP socket can sit open and dead for
 * minutes, which is exactly the stale all-quiet screen the non-negotiables
 * forbid. OkHttp's onFailure is a hint for reconnect scheduling, never the
 * source of truth for what colour the UI is.
 */
enum class ConnectionState {
    /** Never connected yet this process. */
    CONNECTING,

    /** Heartbeat seen within 15s. */
    GREEN,

    /** No heartbeat for 15s. */
    AMBER,

    /** No heartbeat for 25s. Treat the pipeline as untrusted. */
    RED;

    companion object {
        const val AMBER_AFTER_MS = 15_000L
        const val RED_AFTER_MS = 25_000L

        fun fromGap(millisSinceHeartbeat: Long): ConnectionState = when {
            millisSinceHeartbeat >= RED_AFTER_MS -> RED
            millisSinceHeartbeat >= AMBER_AFTER_MS -> AMBER
            else -> GREEN
        }
    }
}
