package com.centron.sentinel.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.centron.sentinel.notify.AlertCenter
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The bell and the panel it drops.
 *
 * Present on every screen inside the app, top right, because an alert that
 * arrives while you are three screens deep still has to be reachable. The
 * badge is the only thing in the chrome allowed to use the accent colour, so
 * it wins attention without anything else having to shout.
 *
 * Critical severity opens the panel by itself. High only lights the badge.
 * That asymmetry is the whole design: if everything interrupts, nothing does.
 */
@Composable
fun BellButton(unread: Int, open: Boolean, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()

    val tilt by animateFloatAsState(
        targetValue = if (unread > 0 && !open) -12f else 0f,
        animationSpec = spring(dampingRatio = Spring.DampingRatioLowBouncy, stiffness = Spring.StiffnessLow),
        label = "tilt",
    )

    Box(
        Modifier
            .size(40.dp)
            .clip(CircleShape)
            .background(if (pressed || open) Ink.Raised else Color.Transparent)
            .clickable(interactionSource = interaction, indication = null, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        BellGlyph(
            tint = if (unread > 0) Ink.Primary else Ink.Secondary,
            modifier = Modifier.rotate(tilt),
        )

        if (unread > 0) {
            Box(
                Modifier
                    .align(Alignment.TopEnd)
                    .padding(top = 6.dp, end = 5.dp)
                    .size(if (unread > 9) 16.dp else 14.dp)
                    .clip(CircleShape)
                    .background(Accent.Clay),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    if (unread > 9) "9+" else unread.toString(),
                    style = MaterialTheme.typography.labelSmall.copy(letterSpacing = 0.sp),
                    color = Ink.Base,
                )
            }
        }
    }
}

@Composable
private fun BellGlyph(tint: Color, modifier: Modifier = Modifier) {
    Canvas(modifier.then(Modifier.size(20.dp))) {
        val s = size.minDimension
        val stroke = s * 0.11f

        val body = Path().apply {
            moveTo(s * 0.18f, s * 0.68f)
            cubicTo(s * 0.18f, s * 0.30f, s * 0.30f, s * 0.16f, s * 0.50f, s * 0.16f)
            cubicTo(s * 0.70f, s * 0.16f, s * 0.82f, s * 0.30f, s * 0.82f, s * 0.68f)
        }
        drawPath(body, tint, style = Stroke(width = stroke))

        drawLine(
            color = tint,
            start = Offset(s * 0.10f, s * 0.70f),
            end = Offset(s * 0.90f, s * 0.70f),
            strokeWidth = stroke,
        )

        drawArc(
            color = tint,
            startAngle = 0f,
            sweepAngle = 180f,
            useCenter = false,
            topLeft = Offset(s * 0.38f, s * 0.72f),
            size = Size(s * 0.24f, s * 0.20f),
            style = Stroke(width = stroke),
        )
    }
}

/**
 * The drop-down.
 *
 * Reworked to match the rest of the app: the panel is a rounded sheet rather
 * than a full-bleed slab, and rows are spaced cards instead of flat bands cut
 * by hairlines. Dividers were doing structural work that spacing and surface
 * steps do better — a stack of ruled rows is the thing that read as dated.
 */
@Composable
fun AlertPanel(
    visible: Boolean,
    alerts: List<AlertCenter.Alert>,
    onDismiss: () -> Unit,
    onMarkRead: () -> Unit,
    onSeeAll: () -> Unit,
) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn(tween(160)) + expandVertically(tween(280, easing = FastOutSlowInEasing)),
        exit = fadeOut(tween(120)) + shrinkVertically(tween(200, easing = FastOutSlowInEasing)),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = Space.Sm, vertical = Space.Sm)
                .clip(RoundedCornerShape(20.dp))
                .background(Ink.Surface)
                .border(Space.Hair, Ink.Hairline, RoundedCornerShape(20.dp))
                .padding(Space.Sm),
        ) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = Space.Sm + Space.Xs, vertical = Space.Sm),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("ALERTS", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
                Spacer(Modifier.weight(1f))
                if (alerts.any { !it.read }) {
                    QuietAction("Mark all read", Accent.Clay, onMarkRead)
                    Spacer(Modifier.width(Space.Xs))
                }
                QuietAction("Close", Ink.Secondary, onDismiss)
            }

            if (alerts.isEmpty()) {
                Text(
                    "Nothing yet. High and critical events land here.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Ink.Tertiary,
                    modifier = Modifier.padding(
                        horizontal = Space.Sm + Space.Xs,
                        vertical = Space.Md,
                    ),
                )
            } else {
                LazyColumn(
                    Modifier.heightIn(max = 340.dp),
                    verticalArrangement = Arrangement.spacedBy(Space.Xs),
                ) {
                    items(alerts, key = { it.eventId }) { AlertRow(it) }
                }
            }

            // Footer. Bottom right, because it is the forward action and the
            // eye lands there last on a left-ranged panel.
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(top = Space.Sm),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    Modifier
                        .clip(RoundedCornerShape(10.dp))
                        .background(Ink.Raised)
                        .clickable(onClick = onSeeAll)
                        .padding(horizontal = Space.Md, vertical = Space.Sm + Space.Xs),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "See all alerts",
                        style = MaterialTheme.typography.labelLarge,
                        color = Ink.Primary,
                    )
                    Spacer(Modifier.width(Space.Sm))
                    Chevron(Accent.Clay)
                }
            }
        }
    }
}

@Composable
private fun QuietAction(label: String, tint: Color, onClick: () -> Unit) {
    Text(
        label,
        style = MaterialTheme.typography.labelSmall,
        color = tint,
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = Space.Sm, vertical = Space.Xs),
    )
}

@Composable
private fun Chevron(tint: Color) {
    Canvas(Modifier.size(10.dp)) {
        val s = size.minDimension
        val stroke = s * 0.18f
        drawLine(
            tint,
            Offset(s * 0.32f, s * 0.18f),
            Offset(s * 0.68f, s * 0.50f),
            strokeWidth = stroke,
        )
        drawLine(
            tint,
            Offset(s * 0.68f, s * 0.50f),
            Offset(s * 0.32f, s * 0.82f),
            strokeWidth = stroke,
        )
    }
}

@Composable
private fun AlertRow(alert: AlertCenter.Alert) {
    val accent = if (alert.severity == "critical") Status.Bad else Status.Warn

    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(if (alert.read) Ink.Base else Ink.Raised)
            .padding(horizontal = Space.Md, vertical = Space.Md),
    ) {
        Box(
            Modifier
                .padding(top = 6.dp)
                .size(6.dp)
                .clip(CircleShape)
                .background(accent),
        )
        Spacer(Modifier.width(Space.Md))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    alert.severity.uppercase(),
                    style = MaterialTheme.typography.labelSmall,
                    color = accent,
                )
                Spacer(Modifier.weight(1f))
                Text(clock.format(Date(alert.atMillis)), style = MonoSmall, color = Ink.Tertiary)
            }
            Spacer(Modifier.height(Space.Xs))
            Text(alert.title, style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
            Spacer(Modifier.height(2.dp))
            // tts_summary: value-free by construction, safe to surface here.
            Text(alert.summary, style = MaterialTheme.typography.bodyMedium, color = Ink.Secondary)
        }
    }
}

private val clock = SimpleDateFormat("HH:mm:ss", Locale.US)
