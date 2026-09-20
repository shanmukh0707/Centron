package com.centron.sentinel.ui.home

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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.centron.sentinel.device.Capability
import com.centron.sentinel.device.Container
import com.centron.sentinel.device.Device
import com.centron.sentinel.device.DeviceState
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status

/**
 * One device, everything you can do to it.
 *
 * Ordered by blast radius ascending: read-only facts first, then per-container
 * controls, then the device-level power control last. The most destructive
 * thing on the screen should never be the first thing under your thumb.
 */
@Composable
fun DeviceDetailScreen(
    device: Device,
    onBack: () -> Unit,
    onTogglePower: (Boolean) -> Unit,
    onToggleContainer: (Container, Boolean) -> Unit,
    onAskAboutDevice: () -> Unit,
    onAskAboutContainer: (Container) -> Unit,
) {
    val dot = when (device.state) {
        DeviceState.ONLINE -> Status.Ok
        DeviceState.DEGRADED -> Status.Warn
        DeviceState.OFFLINE -> Status.Bad
        DeviceState.UNKNOWN -> Ink.Tertiary
    }

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
            Text(device.type.label, style = MonoSmall, color = Ink.Tertiary)
        }
        Box(Modifier.fillMaxWidth().height(Space.Hair).background(Ink.Hairline))

        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(
                start = Space.Gutter,
                end = Space.Gutter,
                top = Space.Lg,
                bottom = Space.Xxl,
            ),
            verticalArrangement = Arrangement.spacedBy(Space.Md),
        ) {
            item {
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(8.dp).clip(CircleShape).background(dot))
                        Spacer(Modifier.width(Space.Sm + Space.Xs))
                        Text(
                            device.name,
                            style = MaterialTheme.typography.headlineMedium,
                            color = Ink.Primary,
                        )
                    }
                    Spacer(Modifier.height(Space.Xs))
                    Text(device.role, style = MaterialTheme.typography.bodyLarge, color = Ink.Secondary)
                    Spacer(Modifier.height(Space.Sm))
                    Text(
                        "${device.sshUser}@${device.host}:${device.sshPort}",
                        style = MonoSmall,
                        color = Ink.Tertiary,
                    )
                }
            }

            item { AskRow("Ask Centron about ${device.name}", onAskAboutDevice) }

            if (device.can(Capability.SYSTEM_USAGE) || device.can(Capability.THERMALS)) {
                item { TelemetryPanel(device) }
            }

            if (device.can(Capability.CONTAINERS)) {
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("CONTAINERS", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
                        Spacer(Modifier.width(Space.Sm))
                        Text(
                            "${device.containers.count { it.running }}/${device.containers.size} running",
                            style = MonoSmall,
                            color = Ink.Tertiary,
                        )
                    }
                }

                items(device.containers.size) { index ->
                    val container = device.containers[index]
                    ContainerRow(
                        container = container,
                        onToggle = { onToggleContainer(container, it) },
                        onAsk = { onAskAboutContainer(container) },
                    )
                }
            }

            if (device.can(Capability.POWER)) {
                item {
                    Spacer(Modifier.height(Space.Md))
                    PowerPanel(device.powerOn, device.name, onTogglePower)
                }
            }
        }
    }
}

@Composable
private fun AskRow(label: String, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(Accent.ClaySubtle)
            .border(Space.Hair, Accent.Clay.copy(alpha = 0.35f), RoundedCornerShape(14.dp))
            .clickable(onClick = onClick)
            .padding(Space.Md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = Accent.Clay)
        Spacer(Modifier.weight(1f))
        Text("›", style = MaterialTheme.typography.titleMedium, color = Accent.Clay)
    }
}

@Composable
private fun TelemetryPanel(device: Device) {
    val t = device.telemetry
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(16.dp))
            .padding(Space.Md),
    ) {
        Text("SYSTEM", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
        Spacer(Modifier.height(Space.Md))
        Stat("CPU", t.cpuPercent?.let { "$it%" })
        Stat("Memory", t.memPercent?.let { "$it%" })
        Stat("Disk", t.diskPercent?.let { "$it%" })
        Stat("Temperature", t.tempC?.let { "${it.toInt()}°C" })
        Stat("Uptime", t.uptimeSeconds?.let { formatUptime(it) })
    }
}

@Composable
private fun Stat(label: String, value: String?) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = Ink.Secondary)
        Spacer(Modifier.weight(1f))
        // A device that cannot report is shown as unknown, never as zero.
        Text(value ?: "unknown", style = MonoSmall, color = if (value == null) Ink.Tertiary else Ink.Primary)
    }
}

private fun formatUptime(seconds: Long): String {
    val d = seconds / 86_400
    val h = (seconds % 86_400) / 3_600
    return if (d > 0) "${d}d ${h}h" else "${h}h"
}

@Composable
private fun ContainerRow(
    container: Container,
    onToggle: (Boolean) -> Unit,
    onAsk: () -> Unit,
) {
    val dot: Color = when {
        !container.running -> Ink.Tertiary
        container.health == "unhealthy" -> Status.Bad
        container.health == "healthy" -> Status.Ok
        else -> Status.Warn
    }

    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(14.dp))
            .padding(Space.Md),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(dot))
            Spacer(Modifier.width(Space.Sm + Space.Xs))
            Column(Modifier.weight(1f)) {
                Text(container.name, style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
                Text(container.image, style = MonoSmall, color = Ink.Tertiary)
            }
            if (container.stoppable) {
                SmallToggle(container.running, onToggle)
            } else {
                Text("pinned", style = MonoSmall, color = Ink.Tertiary)
            }
        }

        if (container.running && (container.cpuPercent != null || container.memMb != null)) {
            Spacer(Modifier.height(Space.Sm))
            Row(horizontalArrangement = Arrangement.spacedBy(Space.Md)) {
                container.cpuPercent?.let { Text("cpu ${it}%", style = MonoSmall, color = Ink.Tertiary) }
                container.memMb?.let { Text("mem ${it}MB", style = MonoSmall, color = Ink.Tertiary) }
                container.health?.let { Text(it, style = MonoSmall, color = dot) }
            }
        }

        Spacer(Modifier.height(Space.Sm))
        Text(
            "Ask Centron about ${container.name}",
            style = MaterialTheme.typography.labelSmall,
            color = Accent.Clay,
            modifier = Modifier
                .clip(RoundedCornerShape(8.dp))
                .clickable(onClick = onAsk)
                .padding(vertical = Space.Xs),
        )
    }
}

@Composable
private fun SmallToggle(on: Boolean, onChange: (Boolean) -> Unit) {
    Box(
        Modifier
            .clip(RoundedCornerShape(9.dp))
            .background(if (on) Accent.ClaySubtle else Ink.Raised)
            .border(
                Space.Hair,
                if (on) Accent.Clay.copy(alpha = 0.5f) else Ink.Hairline,
                RoundedCornerShape(9.dp),
            )
            .clickable { onChange(!on) }
            .padding(horizontal = Space.Md, vertical = Space.Sm),
    ) {
        Text(
            if (on) "Stop" else "Start",
            style = MaterialTheme.typography.labelSmall,
            color = if (on) Accent.Clay else Ink.Secondary,
        )
    }
}

@Composable
private fun PowerPanel(on: Boolean, name: String, onToggle: (Boolean) -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Status.Bad.copy(alpha = 0.35f), RoundedCornerShape(16.dp))
            .padding(Space.Md),
    ) {
        Text("POWER", style = MaterialTheme.typography.labelSmall, color = Status.Bad)
        Spacer(Modifier.height(Space.Sm))
        Text(
            if (on) {
                "Powering off $name takes it off the network. If this is the box Centron runs on, you will lose the engine with it."
            } else {
                "$name is powered off. Wake requires the engine to still be reachable."
            },
            style = MaterialTheme.typography.bodyMedium,
            color = Ink.Secondary,
        )
        Spacer(Modifier.height(Space.Md))
        Box(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(if (on) Status.BadSubtle else Ink.Raised)
                .border(
                    Space.Hair,
                    if (on) Status.Bad.copy(alpha = 0.5f) else Ink.Hairline,
                    RoundedCornerShape(12.dp),
                )
                .clickable { onToggle(!on) }
                .padding(vertical = 14.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                if (on) "Power off $name" else "Power on $name",
                style = MaterialTheme.typography.labelLarge,
                color = if (on) Status.Bad else Ink.Secondary,
            )
        }
    }
}
