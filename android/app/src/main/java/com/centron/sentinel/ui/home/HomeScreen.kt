package com.centron.sentinel.ui.home

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.InfiniteRepeatableSpec
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import com.centron.sentinel.device.Capability
import com.centron.sentinel.device.Device
import com.centron.sentinel.device.DeviceState
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status
import java.util.Calendar

/**
 * Home.
 *
 * Top half is the greeting and the mark — deliberately quiet, because the
 * first thing this screen should communicate is "nothing is on fire". If
 * something were wrong the bell would be lit and the connection bar would not
 * be green, so calm here is information, not wasted space.
 *
 * Bottom half is the fleet. Devices are the nouns of this product; events
 * happen *to* them, so they get the persistent home and the stream lives one
 * tap away behind the bell.
 */
@Composable
fun HomeScreen(
    devices: List<Device>,
    onAddDevice: () -> Unit,
    onOpenDevice: (Device) -> Unit,
    onTogglePower: (Device, Boolean) -> Unit,
    onOpenChat: () -> Unit,
) {
    var revealed by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        repeat(3) {
            revealed = it + 1
            kotlinx.coroutines.delay(70)
        }
    }

    LazyColumn(
        Modifier.fillMaxWidth(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(
            start = Space.Gutter,
            end = Space.Gutter,
            top = Space.Xl,
            bottom = Space.Xxl,
        ),
        verticalArrangement = Arrangement.spacedBy(Space.Sm),
    ) {
        item {
            Fade(revealed >= 1) { GreetingBlock() }
            Spacer(Modifier.height(Space.Lg))
        }

        item {
            Fade(revealed >= 1) { AskBar(onOpenChat) }
            Spacer(Modifier.height(Space.Xl))
        }

        item {
            Fade(revealed >= 2) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("DEVICES", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
                    Spacer(Modifier.width(Space.Sm))
                    Text(
                        devices.size.toString(),
                        style = MonoSmall,
                        color = Ink.Tertiary,
                    )
                    Spacer(Modifier.weight(1f))
                    Text(
                        "Add device",
                        style = MaterialTheme.typography.labelSmall,
                        color = Accent.Clay,
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .clickable(onClick = onAddDevice)
                            .padding(horizontal = Space.Sm, vertical = Space.Xs),
                    )
                }
            }
            Spacer(Modifier.height(Space.Sm))
        }

        if (devices.isEmpty()) {
            item { EmptyFleet(onAddDevice) }
        } else {
            items(devices, key = { it.id }) { device ->
                Fade(revealed >= 3) {
                    DeviceCard(
                        device = device,
                        onOpen = { onOpenDevice(device) },
                        onTogglePower = { on -> onTogglePower(device, on) },
                    )
                }
            }
        }
    }
}

/**
 * The way into conversation, sitting directly under the greeting.
 *
 * Styled as an input rather than a button because that is what it becomes —
 * tapping opens the composer already focused. A button would imply a menu; a
 * field implies you can just start typing, which is the whole point.
 */
@Composable
private fun AskBar(onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(16.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = Space.Md, vertical = 15.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "Ask Centron about your homelab…",
            style = MaterialTheme.typography.bodyLarge,
            color = Ink.Tertiary,
        )
        Spacer(Modifier.weight(1f))
        Box(
            Modifier
                .size(30.dp)
                .clip(CircleShape)
                .background(Accent.ClaySubtle),
            contentAlignment = Alignment.Center,
        ) {
            Text("↑", style = MaterialTheme.typography.titleMedium, color = Accent.Clay)
        }
    }
}

// ---------------------------------------------------------------- greeting

@Composable
private fun GreetingBlock() {
    val hour = remember { Calendar.getInstance().get(Calendar.HOUR_OF_DAY) }

    Column {
        CentronMark()
        Spacer(Modifier.height(Space.Lg))
        Text(
            greetingFor(hour),
            style = MaterialTheme.typography.displaySmall,
            color = Ink.Primary,
        )
        Spacer(Modifier.height(Space.Sm))
        Text(
            "Everything is being watched.",
            style = MaterialTheme.typography.bodyLarge,
            color = Ink.Secondary,
        )
    }
}

/**
 * Lowercase and conversational rather than "Welcome back!". The late-night
 * variant exists because this is a tool you are most often opening at an hour
 * you did not choose.
 *
 * OWNER NAME is hardcoded until the auth seam yields a real identity.
 */
private fun greetingFor(hour: Int): String = when (hour) {
    in 0..4 -> "up late, $OWNER"
    in 5..11 -> "good morning, $OWNER"
    in 12..16 -> "good afternoon, $OWNER"
    in 17..21 -> "good evening, $OWNER"
    else -> "up late, $OWNER"
}

private const val OWNER = "Shamit"

/**
 * PLACEHOLDER LOGO.
 *
 * A concentric sweep — something watching a perimeter — drawn rather than
 * dropped in as an asset so there is nothing to license and nothing to
 * migrate. Replace the whole composable when the real mark exists; nothing
 * else references its internals.
 */
@Composable
private fun CentronMark() {
    val transition = rememberInfiniteTransition(label = "mark")
    val sweep by transition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = InfiniteRepeatableSpec(
            animation = tween(9000, easing = androidx.compose.animation.core.LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "sweep",
    )

    // Read out of composition before the draw lambda: token accessors are
    // @Composable and DrawScope is not.
    val ring = Ink.Hairline
    val clay = Accent.Clay

    Row(verticalAlignment = Alignment.CenterVertically) {
        Canvas(Modifier.size(44.dp)) {
            val s = size.minDimension
            val c = Offset(s / 2f, s / 2f)

            drawCircle(ring, radius = s * 0.46f, center = c, style = Stroke(width = s * 0.045f))
            drawCircle(ring, radius = s * 0.30f, center = c, style = Stroke(width = s * 0.045f))

            // The sweep hand. Slow enough to read as patient rather than busy.
            drawArc(
                color = clay,
                startAngle = sweep,
                sweepAngle = 64f,
                useCenter = false,
                topLeft = Offset(s * 0.04f, s * 0.04f),
                size = Size(s * 0.92f, s * 0.92f),
                style = Stroke(width = s * 0.045f),
            )
            drawCircle(clay, radius = s * 0.075f, center = c)
        }

        Spacer(Modifier.width(Space.Md))

        Column {
            Text("CENTRON", style = MaterialTheme.typography.labelSmall, color = Ink.Secondary)
            Spacer(Modifier.height(2.dp))
            Text("placeholder mark", style = MonoSmall, color = Ink.Tertiary)
        }
    }
}

// ---------------------------------------------------------------- device card

@Composable
fun DeviceCard(device: Device, onOpen: () -> Unit, onTogglePower: (Boolean) -> Unit) {
    val dot = when (device.state) {
        DeviceState.ONLINE -> Status.Ok
        DeviceState.DEGRADED -> Status.Warn
        DeviceState.OFFLINE -> Status.Bad
        DeviceState.UNKNOWN -> Ink.Tertiary
    }

    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(16.dp))
            .clickable(onClick = onOpen)
            .padding(Space.Md),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(dot))
            Spacer(Modifier.width(Space.Sm + Space.Xs))
            Column(Modifier.weight(1f)) {
                Text(device.name, style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
                Text(device.role, style = MaterialTheme.typography.bodyMedium, color = Ink.Tertiary)
            }
            if (device.can(Capability.POWER)) {
                PowerSwitch(on = device.powerOn, onChange = onTogglePower)
            }
        }

        val metrics = buildMetrics(device, warn = Status.Warn, bad = Status.Bad)
        if (metrics.isNotEmpty()) {
            Spacer(Modifier.height(Space.Md))
            Row(horizontalArrangement = Arrangement.spacedBy(Space.Sm)) {
                metrics.forEach { MetricChip(it.first, it.second, it.third) }
            }
        }
    }
}

/** Colours are passed in rather than read here: this is a plain function and
 *  the token accessors are @Composable. */
private fun buildMetrics(
    device: Device,
    warn: Color,
    bad: Color,
): List<Triple<String, String, Color?>> {
    val t = device.telemetry
    val out = mutableListOf<Triple<String, String, Color?>>()

    if (device.can(Capability.THERMALS)) {
        val temp = t.tempC
        out += Triple(
            "TEMP",
            temp?.let { "${it.toInt()}°" } ?: "—",
            // Thresholds are deliberately conservative; a Pi at 70C is
            // throttling and the operator should see amber before it does.
            when {
                temp == null -> null
                temp >= 75 -> bad
                temp >= 65 -> warn
                else -> null
            },
        )
    }
    if (device.can(Capability.SYSTEM_USAGE)) {
        out += Triple("CPU", t.cpuPercent?.let { "$it%" } ?: "—", null)
        out += Triple("MEM", t.memPercent?.let { "$it%" } ?: "—", null)
    }
    if (device.can(Capability.CONTAINERS) && t.containersTotal != null) {
        out += Triple("CTR", "${t.containersRunning ?: 0}/${t.containersTotal}", null)
    }
    if (device.can(Capability.STORAGE) && !device.can(Capability.SYSTEM_USAGE)) {
        out += Triple("DISK", t.diskPercent?.let { "$it%" } ?: "—", null)
    }
    return out.take(4)
}

@Composable
private fun MetricChip(label: String, value: String, warn: Color?) {
    Column(
        Modifier
            .clip(RoundedCornerShape(10.dp))
            .background(Ink.Raised)
            .padding(horizontal = Space.Md, vertical = Space.Sm),
    ) {
        Text(label, style = MonoSmall, color = Ink.Tertiary)
        Spacer(Modifier.height(2.dp))
        Text(
            value,
            style = MaterialTheme.typography.titleMedium,
            color = warn ?: Ink.Primary,
        )
    }
}

@Composable
private fun PowerSwitch(on: Boolean, onChange: (Boolean) -> Unit) {
    val offset by animateFloatAsState(
        targetValue = if (on) 1f else 0f,
        animationSpec = tween(180, easing = FastOutSlowInEasing),
        label = "power",
    )

    Box(
        Modifier
            .width(46.dp)
            .height(28.dp)
            .clip(RoundedCornerShape(14.dp))
            .background(if (on) Accent.Clay else Ink.Raised)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(14.dp))
            .clickable { onChange(!on) },
    ) {
        // offset, not padding: padding applied after size shrinks the knob
        // instead of moving it, which renders it as a squished bar.
        Box(
            Modifier
                .padding(3.dp)
                .offset(x = (offset * 18f).dp)
                .size(22.dp)
                .clip(CircleShape)
                .background(if (on) Ink.Base else Ink.Secondary),
        )
    }
}

// ---------------------------------------------------------------- empty

@Composable
private fun EmptyFleet(onAddDevice: () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(16.dp))
            .clickable(onClick = onAddDevice)
            .padding(Space.Lg),
    ) {
        Text("No devices yet", style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
        Spacer(Modifier.height(Space.Xs))
        Text(
            "Add the machines this engine should watch. You will be asked what each one is, so Centron knows what it can do with it.",
            style = MaterialTheme.typography.bodyMedium,
            color = Ink.Tertiary,
        )
    }
}

@Composable
private fun Fade(visible: Boolean, content: @Composable () -> Unit) {
    val a by animateFloatAsState(
        targetValue = if (visible) 1f else 0f,
        animationSpec = tween(360, easing = FastOutSlowInEasing),
        label = "fade",
    )
    Box(Modifier.alpha(a)) { content() }
}
