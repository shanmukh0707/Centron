package com.centron.sentinel.ui.chat

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.InfiniteRepeatableSpec
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
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
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.unit.dp
import com.centron.sentinel.engine.ChatScope
import com.centron.sentinel.engine.ChatTurn
import com.centron.sentinel.engine.Speaker
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status

/**
 * Conversation with Centron.
 *
 * Every chat is scoped, and the scope is shown permanently at the top rather
 * than mentioned once in an opening message. That strip is a safety affordance:
 * before you type "restart it", you can see what "it" is bound to. The engine
 * enforces the same boundary in its validator, so the strip is a readout of a
 * real constraint rather than a promise.
 *
 * Turns answered by a cloud model carry the same clay attribution rule used on
 * events. Provenance is consistent across the app or it is not trusted.
 */
@Composable
fun ChatScreen(
    scope: ChatScope,
    turns: List<ChatTurn>,
    pending: Boolean,
    onSend: (String) -> Unit,
    onBack: () -> Unit,
) {
    var draft by remember { mutableStateOf("") }
    val listState = rememberLazyListState()

    LaunchedEffect(turns.size, pending) {
        if (turns.isNotEmpty()) listState.animateScrollToItem(turns.lastIndex)
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(Ink.Base)
            .imePadding(),
    ) {
        ScopeStrip(scope, onBack)

        if (turns.isEmpty() && !pending) {
            EmptyChat(scope)
        } else {
            LazyColumn(
                Modifier.weight(1f),
                state = listState,
                contentPadding = PaddingValues(
                    start = Space.Gutter,
                    end = Space.Gutter,
                    top = Space.Md,
                    bottom = Space.Md,
                ),
                verticalArrangement = Arrangement.spacedBy(Space.Md),
            ) {
                items(turns.size) { index -> TurnBubble(turns[index]) }
                if (pending) item { ThinkingRow() }
            }
        }

        Composer(
            draft = draft,
            onDraft = { draft = it },
            enabled = !pending,
            onSend = {
                val text = draft.trim()
                if (text.isNotEmpty()) {
                    onSend(text)
                    draft = ""
                }
            },
        )
    }
}

@Composable
private fun ScopeStrip(scope: ChatScope, onBack: () -> Unit) {
    val (tint, kind) = when (scope) {
        is ChatScope.Fleet -> Ink.Secondary to "ALL DEVICES"
        is ChatScope.DeviceScope -> Accent.Clay to "DEVICE"
        is ChatScope.ContainerScope -> Accent.Clay to "CONTAINER"
        is ChatScope.EventScope -> Status.Warn to "EVENT"
    }

    Column {
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
            Spacer(Modifier.width(Space.Sm))
            Column(Modifier.weight(1f)) {
                Text(kind, style = MonoSmall, color = tint)
                Text(
                    scope.label,
                    style = MaterialTheme.typography.titleMedium,
                    color = Ink.Primary,
                )
            }
        }
        Box(Modifier.fillMaxWidth().height(Space.Hair).background(Ink.Hairline))
    }
}

@Composable
private fun EmptyChat(scope: ChatScope) {
    val suggestions = when (scope) {
        is ChatScope.Fleet -> listOf(
            "What changed in the last hour?",
            "Is anything running hot?",
            "Which devices are unreachable?",
        )
        is ChatScope.DeviceScope -> listOf(
            "Why is this box busy?",
            "What is using the disk?",
            "Show me recent auth failures.",
        )
        is ChatScope.ContainerScope -> listOf(
            "Why did this container restart?",
            "Show me the last 50 log lines.",
            "Is it healthy?",
        )
        is ChatScope.EventScope -> listOf(
            "Why was this flagged?",
            "What happens if I approve?",
            "Is there a safer fix?",
        )
    }

    Column(
        Modifier
            .fillMaxSize()
            .padding(Space.Gutter),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            "Ask Centron",
            style = MaterialTheme.typography.headlineMedium,
            color = Ink.Primary,
        )
        Spacer(Modifier.height(Space.Sm))
        Text(
            "Scoped to ${scope.label}. Centron can only act inside this scope.",
            style = MaterialTheme.typography.bodyMedium,
            color = Ink.Secondary,
        )
        Spacer(Modifier.height(Space.Lg))
        suggestions.forEach { s ->
            Text(
                s,
                style = MaterialTheme.typography.bodyLarge,
                color = Ink.Secondary,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = Space.Sm)
                    .clip(RoundedCornerShape(12.dp))
                    .background(Ink.Surface)
                    .border(Space.Hair, Ink.Hairline, RoundedCornerShape(12.dp))
                    .padding(Space.Md),
            )
        }
    }
}

@Composable
private fun TurnBubble(turn: ChatTurn) {
    when (turn.speaker) {
        Speaker.YOU -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Text(
                turn.text,
                style = MaterialTheme.typography.bodyLarge,
                color = Ink.Base,
                modifier = Modifier
                    .clip(RoundedCornerShape(16.dp))
                    .background(Accent.Clay)
                    .padding(horizontal = Space.Md, vertical = Space.Sm + Space.Xs),
            )
        }

        Speaker.CENTRON -> Column(Modifier.fillMaxWidth()) {
            turn.viaCloudModel?.let {
                Text(
                    "Answered by $it",
                    style = MaterialTheme.typography.labelSmall,
                    color = Accent.Clay,
                )
                Spacer(Modifier.height(Space.Xs))
            }
            Text(turn.text, style = MaterialTheme.typography.bodyLarge, color = Ink.Primary)
        }

        // Engine errors and scope notices. Visually distinct from Centron's
        // own voice so a failure is never mistaken for an answer.
        Speaker.SYSTEM -> Row(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(Status.WarnSubtle)
                .padding(Space.Md),
        ) {
            Box(
                Modifier
                    .padding(top = 6.dp)
                    .size(5.dp)
                    .clip(CircleShape)
                    .background(Status.Warn),
            )
            Spacer(Modifier.width(Space.Sm + Space.Xs))
            Text(turn.text, style = MaterialTheme.typography.bodyMedium, color = Ink.Secondary)
        }
    }
}

@Composable
private fun ThinkingRow() {
    val transition = rememberInfiniteTransition(label = "think")
    val a by transition.animateFloat(
        initialValue = 0.25f,
        targetValue = 1f,
        animationSpec = InfiniteRepeatableSpec(
            animation = tween(700, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulse",
    )
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(7.dp)
                .alpha(a)
                .clip(CircleShape)
                .background(Accent.Clay),
        )
        Spacer(Modifier.width(Space.Sm))
        Text("Thinking…", style = MaterialTheme.typography.bodyMedium, color = Ink.Tertiary)
    }
}

@Composable
private fun Composer(
    draft: String,
    onDraft: (String) -> Unit,
    enabled: Boolean,
    onSend: () -> Unit,
) {
    Column {
        Box(Modifier.fillMaxWidth().height(Space.Hair).background(Ink.Hairline))
        Row(
            Modifier
                .fillMaxWidth()
                .padding(Space.Md),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(14.dp))
                    .background(Ink.Surface)
                    .border(Space.Hair, Ink.Hairline, RoundedCornerShape(14.dp))
                    .padding(horizontal = Space.Md, vertical = 13.dp),
            ) {
                if (draft.isEmpty()) {
                    Text(
                        "Ask Centron…",
                        style = MaterialTheme.typography.bodyLarge,
                        color = Ink.Tertiary,
                    )
                }
                BasicTextField(
                    value = draft,
                    onValueChange = onDraft,
                    textStyle = MaterialTheme.typography.bodyLarge.copy(color = Ink.Primary),
                    cursorBrush = SolidColor(Accent.Clay),
                    enabled = enabled,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            Spacer(Modifier.width(Space.Sm))
            val live = enabled && draft.isNotBlank()
            Box(
                Modifier
                    .size(46.dp)
                    .clip(CircleShape)
                    .background(if (live) Accent.Clay else Ink.Raised)
                    .clickable(enabled = live, onClick = onSend),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    "↑",
                    style = MaterialTheme.typography.titleMedium,
                    color = if (live) Ink.Base else Ink.Tertiary,
                )
            }
        }
    }
}
