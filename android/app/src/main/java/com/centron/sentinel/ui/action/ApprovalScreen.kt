package com.centron.sentinel.ui.action

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.centron.sentinel.data.EventEntity
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status
import kotlinx.serialization.json.jsonPrimitive

/**
 * What "Take action" opens into.
 *
 * Everything shown here is already on the event. There is no diagnosis
 * endpoint and there should not be one: Contract 1 carries the Claude verdict
 * in `escalation.verdict`, and a late verdict re-emits the same `event_id`
 * with a fresh `seq`, which Room upserts. So this screen is a renderer, not a
 * fetcher — which also means it works offline against whatever the phone last
 * received, rather than going blank when the engine is unreachable.
 *
 * The earlier version of this screen called an invented `diagnose(event_id)`.
 * That was wrong and is gone.
 */
@Composable
fun ApprovalScreen(
    event: EventEntity,
    onBack: () -> Unit,
    onApprove: () -> Unit,
    onDeny: () -> Unit,
    onAskForBetter: () -> Unit,
) {
    // Params live in the raw frame. Decoded once, not per recomposition.
    val target = remember(event.event_id, event.raw) { targetOf(event) }

    Column(
        Modifier
            .fillMaxSize()
            .background(Ink.Base),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = Space.Md, vertical = Space.Sm + Space.Xs),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "Back",
                style = MaterialTheme.typography.labelSmall,
                color = Ink.Secondary,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(onClick = onBack)
                    .padding(horizontal = Space.Sm, vertical = Space.Xs),
            )
            Spacer(Modifier.weight(1f))
            Text(
                event.action_playbook.orEmpty().replace('_', ' ').uppercase(),
                style = MonoSmall,
                color = Status.Warn,
            )
        }
        Box(Modifier.fillMaxWidth().height(Space.Hair).background(Ink.Hairline))

        LazyColumn(
            Modifier.weight(1f),
            contentPadding = PaddingValues(
                start = Space.Gutter, end = Space.Gutter,
                top = Space.Lg, bottom = Space.Lg,
            ),
            verticalArrangement = Arrangement.spacedBy(Space.Md),
        ) {
            item {
                Text(event.title, style = MaterialTheme.typography.headlineMedium, color = Ink.Primary)
                Spacer(Modifier.height(Space.Sm))
                Text(event.internal_log, style = MaterialTheme.typography.bodyLarge, color = Ink.Secondary)
            }

            // What will actually run, in the executor's terms rather than the
            // narration's. The operator is approving this, not the prose.
            item { WhatRunsBlock(event, target) }

            item { EscalationBlock(event) }

            item {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(Accent.ClaySubtle)
                        .border(Space.Hair, Accent.Clay.copy(alpha = 0.35f), RoundedCornerShape(14.dp))
                        .clickable(onClick = onAskForBetter)
                        .padding(Space.Md),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            "Ask for a better solution",
                            style = MaterialTheme.typography.labelLarge,
                            color = Accent.Clay,
                        )
                        Spacer(Modifier.height(2.dp))
                        Text(
                            "Opens a chat scoped to this event.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Ink.Secondary,
                        )
                    }
                    Text("›", style = MaterialTheme.typography.titleMedium, color = Accent.Clay)
                }
            }
        }

        DecisionBar(event, onApprove, onDeny)
    }
}

// ---------------------------------------------------------------- blocks

@Composable
private fun WhatRunsBlock(event: EventEntity, target: String?) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(16.dp))
            .padding(Space.Md),
    ) {
        Text("WHAT RUNS", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
        Spacer(Modifier.height(Space.Sm))
        Text(
            event.action_playbook.orEmpty().replace('_', ' '),
            style = MaterialTheme.typography.titleMedium,
            color = Ink.Primary,
        )
        target?.let {
            Spacer(Modifier.height(Space.Xs))
            Text(it, style = MonoSmall, color = Ink.Secondary)
        }
        Spacer(Modifier.height(Space.Sm))
        Text(
            "Centron validated this against the playbook caps and the protected asset list before offering it.",
            style = MaterialTheme.typography.bodyMedium,
            color = Ink.Tertiary,
        )
    }
}

/**
 * Escalation, rendered by state rather than presence.
 *
 * `pending` and `unreachable` are not the same as "no verdict", and collapsing
 * them would hide the fail-closed behaviour the backend is careful about.
 */
@Composable
private fun EscalationBlock(event: EventEntity) {
    val state = event.escalation_state ?: "none"

    val (tint, heading, body) = when {
        !event.escalated && state == "none" -> Triple(
            Ink.Tertiary,
            "LOCAL MODEL ONLY",
            "No escalation gate fired. This was triaged on the homelab by qwen2.5, and no cloud model saw it.",
        )
        state == "answered" -> Triple(
            Accent.Clay,
            "ANSWERED BY CLAUDE",
            event.escalation_verdict
                ?: "Claude answered but the verdict field was empty.",
        )
        state == "pending" -> Triple(
            Status.Warn,
            "CLAUDE STILL LOOKING",
            "The verdict has not arrived. You can decide now without it — the answer will re-emit this event when it lands.",
        )
        state == "unreachable" -> Triple(
            Status.Bad,
            "CLAUDE UNREACHABLE",
            "Escalation failed closed. This event ships unreviewed, so you are deciding on the local model's read alone.",
        )
        else -> Triple(Ink.Tertiary, "ESCALATION", state)
    }

    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, tint.copy(alpha = 0.3f), RoundedCornerShape(16.dp))
            .padding(Space.Md),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(tint))
            Spacer(Modifier.width(Space.Sm))
            Text(heading, style = MaterialTheme.typography.labelSmall, color = tint)
            Spacer(Modifier.weight(1f))
            Text(
                "confidence ${"%.2f".format(event.confidence)}",
                style = MonoSmall,
                color = Ink.Tertiary,
            )
        }

        event.escalation_reason?.let {
            Spacer(Modifier.height(Space.Sm))
            Text(
                "Gate: ${event.escalation_gate ?: "unknown"} — $it",
                style = MaterialTheme.typography.bodyMedium,
                color = Ink.Tertiary,
            )
        }

        Spacer(Modifier.height(Space.Sm))
        Text(body, style = MaterialTheme.typography.bodyLarge, color = Ink.Primary)

        if (!event.reviewed) {
            Spacer(Modifier.height(Space.Sm))
            Text(
                "UNREVIEWED",
                style = MaterialTheme.typography.labelSmall,
                color = Status.Warn,
            )
        }
    }
}

@Composable
private fun DecisionBar(event: EventEntity, onApprove: () -> Unit, onDeny: () -> Unit) {
    // isolate_host is destructive and never auto-executes. Naming that here
    // is cheaper than the operator discovering it afterwards.
    val destructive = event.action_playbook == "isolate_host" ||
        event.action_playbook == "revoke_session"

    Column {
        Box(Modifier.fillMaxWidth().height(Space.Hair).background(Ink.Hairline))
        Column(Modifier.padding(Space.Md)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(6.dp)
                        .clip(CircleShape)
                        .background(if (destructive) Status.Bad else Status.Ok),
                )
                Spacer(Modifier.width(Space.Sm))
                Text(
                    if (destructive) "Destructive" else "Reversible, auto-expires",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (destructive) Status.Bad else Status.Ok,
                )
                Spacer(Modifier.weight(1f))
                event.expires_at?.let { Text("expires $it", style = MonoSmall, color = Ink.Tertiary) }
            }

            Spacer(Modifier.height(Space.Md))

            Row(horizontalArrangement = Arrangement.spacedBy(Space.Sm)) {
                Box(
                    Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(12.dp))
                        .background(Ink.Raised)
                        .clickable(onClick = onDeny)
                        .padding(vertical = 14.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("Deny", style = MaterialTheme.typography.labelLarge, color = Ink.Secondary)
                }
                Box(
                    Modifier
                        .weight(1.4f)
                        .clip(RoundedCornerShape(12.dp))
                        .background(Accent.Clay)
                        .clickable(onClick = onApprove)
                        .padding(vertical = 14.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("Approve", style = MaterialTheme.typography.labelLarge, color = Ink.Base)
                }
            }

            Spacer(Modifier.height(Space.Sm))
            Text(
                "Approving unlocks this phone first. The engine trusts the pinned certificate and token, not the unlock.",
                style = MaterialTheme.typography.bodyMedium,
                color = Ink.Tertiary,
            )
        }
    }
}

/** Pulls the human-meaningful target out of the action params. */
private fun targetOf(event: EventEntity): String? = runCatching {
    val action = event.toEventData().action ?: return null
    val params = action.params
    val target = params["target"]?.jsonPrimitive?.content
    val duration = params["duration_sec"]?.jsonPrimitive?.content?.toLongOrNull()
    buildString {
        append(target ?: return null)
        duration?.let { append("  for ").append(it / 3600).append("h") }
    }
}.getOrNull()
