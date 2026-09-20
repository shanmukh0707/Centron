package com.centron.sentinel.net

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Process-wide connection state, published by SentinelService and read by the
 * UI. A singleton because the socket lives in the service and the UI comes and
 * goes; a bound-service dance buys nothing here.
 */
object ConnectionStatus {
    private val _state = MutableStateFlow(ConnectionState.CONNECTING)
    val state: StateFlow<ConnectionState> = _state.asStateFlow()

    fun publish(state: ConnectionState) {
        _state.value = state
    }
}
