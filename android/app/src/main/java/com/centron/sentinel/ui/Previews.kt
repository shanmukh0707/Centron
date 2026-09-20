package com.centron.sentinel.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import com.centron.sentinel.data.EventEntity
import com.centron.sentinel.net.ConnectionState
import com.centron.sentinel.ui.auth.AuthScreen
import com.centron.sentinel.ui.auth.AuthState
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.SentinelTheme
import com.centron.sentinel.ui.theme.Space

/**
 * Design-time previews. No emulator, no stub, no device needed — open this
 * file in Android Studio and switch the editor to Split or Design.
 */

private fun fixture(
    id: String,
    severity: String,
    title: String,
    log: String,
    escalated: Boolean = false,
    gate: String? = null,
    reason: String? = null,
    verdict: String? = null,
    reviewed: Boolean = true,
    pending: Boolean = false,
) = EventEntity(
    event_id = id,
    seq = 3,
    ts = "2026-09-20T02:00:00.000Z",
    signature = "ssh.bruteforce",
    severity = severity,
    title = title,
    internal_log = log,
    tts_summary = "Repeated failed logins from one outside address await approval.",
    confidence = 0.91,
    reviewed = reviewed,
    escalated = escalated,
    escalation_gate = gate,
    escalation_reason = reason,
    escalation_state = if (escalated) "answered" else "none",
    escalation_verdict = verdict,
    action_playbook = "block_ip",
    action_status = if (pending) "pending_approval" else "auto_executed",
    requires_approval = pending,
    approval_id = "apr_0199612eb401",
    expires_at = if (pending) "2026-09-20T02:02:00.000Z" else null,
    audio_status = "ready",
    audio_url = null,
    audio_cache_key = "tts_1f3a9c",
    raw = "{}",
)

private val demoEvents = listOf(
    fixture(
        id = "a",
        severity = "critical",
        title = "Workstation spraying SMB logins across the LAN",
        log = "HOST_B opened SMB sessions to 14 internal hosts in 40s using USER_3 credentials, 11 rejected. Consistent with lateral movement. Isolation proposed.",
        escalated = true,
        gate = "blast_radius",
        reason = "model returned critical",
        verdict = "Lateral movement in progress. Isolate before the share is reached.",
        pending = true,
    ),
    fixture(
        id = "b",
        severity = "high",
        title = "Password spray from three sources",
        log = "HOST_A, HOST_E and HOST_F each tried the same 6 usernames once against the VPN portal inside one window.",
        escalated = true,
        gate = "correlation",
        reason = "3 distinct sources in one window",
        verdict = "Coordinated low-and-slow spray. Block all three sources for 24h.",
    ),
    fixture(
        id = "c",
        severity = "low",
        title = "Slow port sweep from a single source",
        log = "HOST_A touched 60 distinct ports on the edge firewall over 9 minutes.",
        reviewed = false,
    ),
)

// ------------------------------------------------------------------ auth

@Preview(name = "Auth — sign in", showBackground = true, widthDp = 400, heightDp = 880)
@Composable
fun PreviewAuth() = SentinelTheme {
    AuthScreen(state = AuthState.Idle, onProvider = {}, onEmail = {}, onSkip = {})
}

@Preview(name = "Auth — provider unwired", showBackground = true, widthDp = 400, heightDp = 880)
@Composable
fun PreviewAuthError() = SentinelTheme {
    AuthScreen(
        state = AuthState.Failed("Google sign-in needs an OAuth client and a backend to exchange the code. Not wired yet."),
        onProvider = {},
        onEmail = {},
        onSkip = {},
    )
}

// ---------------------------------------------------------------- events

@Composable
private fun EventShell(state: ConnectionState, events: List<EventEntity>) = SentinelTheme {
    Column(
        Modifier
            .fillMaxSize()
            .background(Ink.Base),
    ) {
        TopBar(
            state = state,
            siteName = "Home lab",
            sitePanelOpen = false,
            unread = 2,
            panelOpen = false,
            onSite = {},
            onBell = {},
        )
        Column(
            Modifier
                .fillMaxWidth()
                .padding(Space.Gutter),
            verticalArrangement = Arrangement.spacedBy(Space.Sm),
        ) {
            events.forEach { EventCard(it) {} }
        }
    }
}

@Preview(name = "Events — connected", showBackground = true, widthDp = 400, heightDp = 940)
@Composable
fun PreviewConnected() = EventShell(ConnectionState.GREEN, demoEvents)

@Preview(name = "Events — amber", showBackground = true, widthDp = 400, heightDp = 460)
@Composable
fun PreviewAmber() = EventShell(ConnectionState.AMBER, demoEvents.take(1))

@Preview(name = "Events — red", showBackground = true, widthDp = 400, heightDp = 460)
@Composable
fun PreviewRed() = EventShell(ConnectionState.RED, demoEvents.drop(1).take(1))
