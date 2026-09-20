package com.centron.sentinel.engine

/**
 * Chat scope.
 *
 * A scope is a permission boundary, not a prompt hint. When a conversation is
 * pinned to one container, the engine's validator rejects any tool call
 * targeting anything else — asking the model nicely to stay on topic is not a
 * control, because the log text that reaches it is attacker-influenced.
 */
sealed interface ChatScope {
    val label: String

    /** Whole homelab. Broadest, and the only scope that can discuss devices
     *  it was not opened from. */
    data object Fleet : ChatScope {
        override val label = "All devices"
    }

    data class DeviceScope(val deviceId: String, val deviceName: String) : ChatScope {
        override val label = deviceName
    }

    data class ContainerScope(
        val deviceId: String,
        val deviceName: String,
        val container: String,
    ) : ChatScope {
        override val label = "$container on $deviceName"
    }

    /** Opened from an event. Carries the event so the first turn does not
     *  require the operator to explain what they are asking about. */
    data class EventScope(val eventId: String, val title: String) : ChatScope {
        override val label = title
    }
}

enum class Speaker { YOU, CENTRON, SYSTEM }

data class ChatTurn(
    val speaker: Speaker,
    val text: String,
    val atMillis: Long = System.currentTimeMillis(),
    /** Set when this turn was produced by a cloud model rather than local
     *  Ollama. Drives the same attribution bar used on events. */
    val viaCloudModel: String? = null,
)
