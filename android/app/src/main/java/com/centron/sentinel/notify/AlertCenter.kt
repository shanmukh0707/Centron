package com.centron.sentinel.notify

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * In-app alert inbox behind the bell.
 *
 * Distinct from the system notification tray on purpose. The tray is where an
 * alert reaches you when the app is closed; this is where it stays reachable
 * once you are inside the app, because on a security tool "I dismissed the
 * notification and now cannot find what it said" is a real failure.
 *
 * Only high and critical land here. Info and low are the event stream's job —
 * a bell that lights up for everything is a bell you learn to ignore, which
 * defeats the thing it exists for.
 */
object AlertCenter {

    data class Alert(
        val eventId: String,
        val severity: String,
        val title: String,
        val summary: String,
        val atMillis: Long = System.currentTimeMillis(),
        val read: Boolean = false,
    )

    private const val MAX = 50

    private val _alerts = MutableStateFlow<List<Alert>>(emptyList())
    val alerts: StateFlow<List<Alert>> = _alerts.asStateFlow()

    private val _unread = MutableStateFlow(0)
    val unread: StateFlow<Int> = _unread.asStateFlow()

    /** Set when something arrives that should open the panel by itself. */
    private val _autoOpen = MutableStateFlow(false)
    val autoOpen: StateFlow<Boolean> = _autoOpen.asStateFlow()

    fun push(alert: Alert) {
        val existing = _alerts.value
        // Same event_id can arrive again with an updated verdict. Replace in
        // place rather than stacking duplicates — same reasoning as the Room
        // REPLACE strategy.
        val deduped = existing.filterNot { it.eventId == alert.eventId }
        _alerts.value = (listOf(alert) + deduped).take(MAX)
        _unread.value = _alerts.value.count { !it.read }

        // Critical drops the panel open without being asked. High only
        // increments the badge; interrupting for every high would train you
        // to dismiss it.
        if (alert.severity.equals("critical", ignoreCase = true)) {
            _autoOpen.value = true
        }
    }

    fun markAllRead() {
        _alerts.value = _alerts.value.map { it.copy(read = true) }
        _unread.value = 0
    }

    fun consumeAutoOpen() {
        _autoOpen.value = false
    }

    fun clear() {
        _alerts.value = emptyList()
        _unread.value = 0
        _autoOpen.value = false
    }
}
