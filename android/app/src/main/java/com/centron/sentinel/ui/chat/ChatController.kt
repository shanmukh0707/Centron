package com.centron.sentinel.ui.chat

import com.centron.sentinel.engine.ChatScope
import com.centron.sentinel.engine.ChatTurn
import com.centron.sentinel.engine.EngineResult
import com.centron.sentinel.engine.Engines
import com.centron.sentinel.engine.Speaker
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Chat sessions, keyed by scope.
 *
 * Sessions are kept separate per scope rather than pooled into one thread,
 * because mixing a container conversation into a fleet conversation would let
 * context from one scope influence answers in another — which is the same
 * boundary the validator enforces server side, so the client should not quietly
 * undo it.
 *
 * History lives in memory. It belongs on the engine, alongside alert history,
 * so it survives reinstalls and is available from a second device; that is
 * behind CentronEngine and not built yet.
 */
object ChatController {

    private fun key(scope: ChatScope): String = when (scope) {
        is ChatScope.Fleet -> "fleet"
        is ChatScope.DeviceScope -> "device:${scope.deviceId}"
        is ChatScope.ContainerScope -> "container:${scope.deviceId}:${scope.container}"
        is ChatScope.EventScope -> "event:${scope.eventId}"
    }

    private val _sessions = MutableStateFlow<Map<String, List<ChatTurn>>>(emptyMap())
    val sessions: StateFlow<Map<String, List<ChatTurn>>> = _sessions.asStateFlow()

    private val _pending = MutableStateFlow<String?>(null)
    val pending: StateFlow<String?> = _pending.asStateFlow()

    fun turns(scope: ChatScope): List<ChatTurn> = _sessions.value[key(scope)].orEmpty()

    fun isPending(scope: ChatScope): Boolean = _pending.value == key(scope)

    /** Drops a turn in without a round trip. Used to seed an event chat with
     *  the diagnosis the operator already saw, so the first question has
     *  context without the operator retyping it. */
    fun seed(scope: ChatScope, turn: ChatTurn) {
        val k = key(scope)
        if (_sessions.value[k].isNullOrEmpty()) {
            _sessions.value = _sessions.value + (k to listOf(turn))
        }
    }

    suspend fun send(scope: ChatScope, message: String) {
        val k = key(scope)
        val history = _sessions.value[k].orEmpty()

        _sessions.value = _sessions.value + (k to history + ChatTurn(Speaker.YOU, message))
        _pending.value = k

        val reply = when (val result = Engines.current.chat(scope, history, message)) {
            is EngineResult.Ok -> result.value
            is EngineResult.Unreachable -> ChatTurn(Speaker.SYSTEM, result.detail)
            is EngineResult.Refused -> ChatTurn(Speaker.SYSTEM, "Refused: ${result.reason}")
        }

        _sessions.value = _sessions.value + (k to _sessions.value[k].orEmpty() + reply)
        _pending.value = null
    }

    fun clear(scope: ChatScope) {
        _sessions.value = _sessions.value - key(scope)
    }
}
