package com.centron.sentinel.ui

import com.centron.sentinel.engine.ChatScope

/**
 * Routes.
 *
 * A sealed hierarchy rather than a nav library: the graph is small, every
 * destination's arguments are typed, and there is no deep linking to satisfy.
 * If the graph grows past this, move to Navigation Compose rather than growing
 * this enum sideways.
 */
sealed interface Route {
    data object Auth : Route
    data object Pairing : Route
    data object Home : Route
    data object Alerts : Route
    data object AddDevice : Route
    data object Settings : Route
    data class DeviceDetail(val deviceId: String) : Route
    data class DeviceSettings(val deviceId: String) : Route
    data class Chat(val scope: ChatScope) : Route
    data class Approval(val eventId: String) : Route
}
