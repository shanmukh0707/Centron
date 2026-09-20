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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import com.centron.sentinel.device.Capability
import com.centron.sentinel.device.Device
import com.centron.sentinel.device.StatKind
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status

/**
 * Per-device configuration.
 *
 * What is editable here is what the operator owns: the name, the address, the
 * description, which stats are worth their screen space, and whether this box
 * can be powered off from a phone at all.
 *
 * What is not editable here is anything the engine owns — SSH user, port,
 * credentials, capabilities. The phone does not hold credentials by design, so
 * a screen that appeared to edit them would be lying about where the authority
 * lives.
 */
@Composable
fun DeviceSettingsSheet(
    device: Device,
    onDismiss: () -> Unit,
    onSave: (
        name: String,
        host: String,
        role: String,
        stats: Set<StatKind>,
        powerEnabled: Boolean,
        powerBiometric: Boolean,
    ) -> Unit,
    onForget: () -> Unit,
) {
    var name by remember(device.id) { mutableStateOf(device.name) }
    var host by remember(device.id) { mutableStateOf(device.host) }
    var role by remember(device.id) { mutableStateOf(device.role) }
    var stats by remember(device.id) { mutableStateOf(device.visibleStats) }
    var powerEnabled by remember(device.id) { mutableStateOf(device.powerControlsEnabled) }
    var powerBiometric by remember(device.id) { mutableStateOf(device.powerRequiresBiometric) }
    var confirmingForget by remember(device.id) { mutableStateOf(false) }

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
                "Cancel",
                style = MaterialTheme.typography.labelSmall,
                color = Ink.Secondary,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(onClick = onDismiss)
                    .padding(horizontal = Space.Sm, vertical = Space.Xs),
            )
            Spacer(Modifier.weight(1f))
            Text("CONFIGURE", style = MonoSmall, color = Ink.Tertiary)
            Spacer(Modifier.weight(1f))
            Text(
                "Save",
                style = MaterialTheme.typography.labelLarge,
                color = Accent.Clay,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .clickable {
                        onSave(name, host, role, stats, powerEnabled, powerBiometric)
                    }
                    .padding(horizontal = Space.Sm, vertical = Space.Xs),
            )
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
                SectionLabel("IDENTITY")
                Field("Name", name, { name = it })
                Spacer(Modifier.height(Space.Sm))
                Field("Address", host, { host = it }, mono = true)
                Spacer(Modifier.height(Space.Sm))
                Field("Description", role, { role = it })
                Spacer(Modifier.height(Space.Sm))
                Text(
                    "Reached as ${device.sshUser}@${device.host}:${device.sshPort}. " +
                        "The engine holds the credentials for this box — this phone never does.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Ink.Tertiary,
                )
            }

            item {
                Spacer(Modifier.height(Space.Sm))
                SectionLabel("STATS SHOWN")
                Text(
                    "Turning a stat off hides it here. It does not stop the engine collecting it.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Ink.Tertiary,
                )
                Spacer(Modifier.height(Space.Sm))
            }

            items(StatKind.entries.size) { index ->
                val stat = StatKind.entries[index]
                ToggleRow(
                    title = stat.label,
                    subtitle = if (stat.graphable) "Graphed over time" else "Value only",
                    on = stat in stats,
                    onChange = { on ->
                        stats = if (on) stats + stat else stats - stat
                    },
                )
            }

            if (device.can(Capability.POWER)) {
                item {
                    Spacer(Modifier.height(Space.Md))
                    SectionLabel("POWER")
                    Spacer(Modifier.height(Space.Sm))
                    ToggleRow(
                        title = "Show power controls",
                        subtitle = if (powerEnabled) {
                            "Power off and wake appear on the device screen."
                        } else {
                            "Hidden. The control cannot be pressed by accident because it is not there."
                        },
                        on = powerEnabled,
                        onChange = { powerEnabled = it },
                    )
                    if (powerEnabled) {
                        Spacer(Modifier.height(Space.Sm))
                        ToggleRow(
                            title = "Require fingerprint",
                            subtitle = if (powerBiometric) {
                                "A power change needs a fresh unlock, signed on this phone."
                            } else {
                                "One tap powers this box off. Only sensible where losing it costs nothing."
                            },
                            on = powerBiometric,
                            danger = !powerBiometric,
                            onChange = { powerBiometric = it },
                        )
                    }
                }
            }

            item {
                Spacer(Modifier.height(Space.Lg))
                SectionLabel("REMOVE")
                Spacer(Modifier.height(Space.Sm))
                Box(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (confirmingForget) Status.BadSubtle else Ink.Surface)
                        .border(
                            Space.Hair,
                            if (confirmingForget) Status.Bad.copy(alpha = 0.5f) else Ink.Hairline,
                            RoundedCornerShape(12.dp),
                        )
                        .clickable {
                            if (confirmingForget) onForget() else confirmingForget = true
                        }
                        .padding(vertical = 14.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        if (confirmingForget) "Tap again to remove ${device.name}" else "Remove this device",
                        style = MaterialTheme.typography.labelLarge,
                        color = Status.Bad,
                    )
                }
                Spacer(Modifier.height(Space.Sm))
                Text(
                    "Removes it from this view. It does not touch the machine, and the engine " +
                        "keeps managing it until you tell the engine otherwise.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Ink.Tertiary,
                )
            }
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(text, style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
    Spacer(Modifier.height(Space.Sm))
}

@Composable
private fun Field(
    label: String,
    value: String,
    onChange: (String) -> Unit,
    mono: Boolean = false,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        singleLine = true,
        textStyle = if (mono) MonoSmall else MaterialTheme.typography.bodyLarge,
        keyboardOptions = KeyboardOptions(
            // An address typed with an autocapitalised first letter is a
            // support ticket waiting to happen.
            capitalization = if (mono) KeyboardCapitalization.None else KeyboardCapitalization.Words,
        ),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun ToggleRow(
    title: String,
    subtitle: String,
    on: Boolean,
    onChange: (Boolean) -> Unit,
    danger: Boolean = false,
) {
    val accent = if (danger) Status.Bad else Accent.Clay
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(Ink.Surface)
            .border(
                Space.Hair,
                if (danger) Status.Bad.copy(alpha = 0.35f) else Ink.Hairline,
                RoundedCornerShape(12.dp),
            )
            .clickable { onChange(!on) }
            .padding(Space.Md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
            Spacer(Modifier.height(2.dp))
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = Ink.Secondary)
        }
        Spacer(Modifier.width(Space.Md))
        Box(
            Modifier
                .size(width = 44.dp, height = 26.dp)
                .clip(RoundedCornerShape(13.dp))
                .background(if (on) accent.copy(alpha = 0.25f) else Ink.Raised)
                .border(
                    Space.Hair,
                    if (on) accent.copy(alpha = 0.6f) else Ink.Hairline,
                    RoundedCornerShape(13.dp),
                ),
            contentAlignment = if (on) Alignment.CenterEnd else Alignment.CenterStart,
        ) {
            Box(
                Modifier
                    .padding(horizontal = 3.dp)
                    .size(20.dp)
                    .clip(CircleShape)
                    .background(if (on) accent else Ink.Tertiary),
            )
        }
    }
}
