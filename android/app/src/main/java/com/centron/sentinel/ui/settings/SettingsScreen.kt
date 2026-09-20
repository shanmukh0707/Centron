package com.centron.sentinel.ui.settings

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.centron.sentinel.settings.AppSettings
import com.centron.sentinel.settings.Site
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status
import com.centron.sentinel.ui.theme.ThemeMode

/**
 * Settings.
 *
 * Grouped by what they put at risk rather than by feature area. Appearance
 * first because it is harmless, connection and security in the middle, and
 * anything that can lose access to a homelab at the bottom behind a
 * confirmation. An operator scrolling quickly should not be able to reach
 * "Forget this site" by accident.
 */
@Composable
fun SettingsScreen(
    site: Site?,
    themeMode: ThemeMode,
    speakAlerts: Boolean,
    strictApproval: Boolean,
    retentionDays: Int,
    onBack: () -> Unit,
    onTheme: (ThemeMode) -> Unit,
    onSpeakAlerts: (Boolean) -> Unit,
    onStrictApproval: (Boolean) -> Unit,
    onRetention: (Int) -> Unit,
    onChangePassword: () -> Unit,
    onRepair: () -> Unit,
    onForgetSite: () -> Unit,
) {
    var confirmForget by remember { mutableStateOf(false) }

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
            Text("SETTINGS", style = MonoSmall, color = Ink.Tertiary)
        }
        Box(Modifier.fillMaxWidth().height(Space.Hair).background(Ink.Hairline))

        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(
                start = Space.Gutter, end = Space.Gutter,
                top = Space.Lg, bottom = Space.Xxl,
            ),
            verticalArrangement = Arrangement.spacedBy(Space.Sm),
        ) {
            item { SectionLabel("APPEARANCE") }
            item {
                Card {
                    Text("Theme", style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
                    Spacer(Modifier.height(Space.Sm))
                    Text(
                        "Creme is a warm light mode rather than white — same temperature as night, less glare in daylight.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Ink.Tertiary,
                    )
                    Spacer(Modifier.height(Space.Md))
                    Row(horizontalArrangement = Arrangement.spacedBy(Space.Sm)) {
                        ThemeChip("Night", themeMode == ThemeMode.DARK) { onTheme(ThemeMode.DARK) }
                        ThemeChip("Creme", themeMode == ThemeMode.CREME) { onTheme(ThemeMode.CREME) }
                    }
                }
            }

            item { SectionLabel("ALERTS") }
            item {
                Card {
                    ToggleRow(
                        title = "Speak alerts aloud",
                        subtitle = "Reads the value-free summary. Never speaks hostnames or addresses.",
                        on = speakAlerts,
                        onChange = onSpeakAlerts,
                    )
                }
            }
            item {
                Card {
                    Text("History retention", style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
                    Spacer(Modifier.height(Space.Sm))
                    Text(
                        "How long the engine keeps alerts and chat history. Stored on the engine, not the phone.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Ink.Tertiary,
                    )
                    Spacer(Modifier.height(Space.Md))
                    Row(horizontalArrangement = Arrangement.spacedBy(Space.Sm)) {
                        listOf(7, 30, 90, 365).forEach { days ->
                            ThemeChip(
                                if (days == 365) "1 year" else "${days}d",
                                retentionDays == days,
                            ) { onRetention(days) }
                        }
                    }
                }
            }

            item { SectionLabel("SECURITY") }
            item {
                Card {
                    ToggleRow(
                        title = "Confirm every action",
                        subtitle = "Require biometric approval even for reversible, low blast radius actions.",
                        on = strictApproval,
                        onChange = onStrictApproval,
                    )
                }
            }
            item {
                Card {
                    ActionRow("Change app password", "Re-keys the Keystore entry used to sign approvals.", onChangePassword)
                }
            }

            item { SectionLabel("SITE") }
            item {
                Card {
                    Text(
                        site?.name ?: "No site",
                        style = MaterialTheme.typography.titleMedium,
                        color = Ink.Primary,
                    )
                    Spacer(Modifier.height(Space.Xs))
                    Text(site?.engineAddress ?: "—", style = MonoSmall, color = Ink.Tertiary)
                    Spacer(Modifier.height(Space.Md))
                    ActionRow("Re-pair engine", "Ask the engine for a fresh pairing code.", onRepair)
                }
            }

            item { SectionLabel("DANGER") }
            item {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(16.dp))
                        .background(Ink.Surface)
                        .border(Space.Hair, Status.Bad.copy(alpha = 0.35f), RoundedCornerShape(16.dp))
                        .padding(Space.Md),
                ) {
                    Text("Forget this site", style = MaterialTheme.typography.titleMedium, color = Status.Bad)
                    Spacer(Modifier.height(Space.Sm))
                    Text(
                        "Removes the pairing from this phone. Re-pairing needs physical access to the engine to read a new code.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Ink.Secondary,
                    )
                    Spacer(Modifier.height(Space.Md))

                    if (!confirmForget) {
                        DangerButton("Forget site") { confirmForget = true }
                    } else {
                        Text(
                            "This cannot be undone from the phone.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Status.Bad,
                        )
                        Spacer(Modifier.height(Space.Sm))
                        Row(horizontalArrangement = Arrangement.spacedBy(Space.Sm)) {
                            Box(
                                Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(Ink.Raised)
                                    .clickable { confirmForget = false }
                                    .padding(vertical = 13.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                Text("Keep", style = MaterialTheme.typography.labelLarge, color = Ink.Secondary)
                            }
                            Box(
                                Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(Status.BadSubtle)
                                    .border(Space.Hair, Status.Bad, RoundedCornerShape(12.dp))
                                    .clickable {
                                        confirmForget = false
                                        onForgetSite()
                                    }
                                    .padding(vertical = 13.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                Text("Forget", style = MaterialTheme.typography.labelLarge, color = Status.Bad)
                            }
                        }
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- pieces

@Composable
private fun SectionLabel(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = Ink.Tertiary,
        modifier = Modifier.padding(top = Space.Md, bottom = Space.Xs),
    )
}

@Composable
private fun Card(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(16.dp))
            .padding(Space.Md),
        content = content,
    )
}

@Composable
private fun ThemeChip(label: String, selected: Boolean, onClick: () -> Unit) {
    Box(
        Modifier
            .clip(RoundedCornerShape(10.dp))
            .background(if (selected) Accent.ClaySubtle else Ink.Raised)
            .border(
                Space.Hair,
                if (selected) Accent.Clay else Ink.Hairline,
                RoundedCornerShape(10.dp),
            )
            .clickable(onClick = onClick)
            .padding(horizontal = Space.Md, vertical = Space.Sm + Space.Xs),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelLarge,
            color = if (selected) Accent.Clay else Ink.Secondary,
        )
    }
}

@Composable
private fun ToggleRow(title: String, subtitle: String, on: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
            Spacer(Modifier.height(Space.Xs))
            Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = Ink.Tertiary)
        }
        Spacer(Modifier.width(Space.Md))
        Switch(on, onChange)
    }
}

@Composable
private fun Switch(on: Boolean, onChange: (Boolean) -> Unit) {
    val offset by animateFloatAsState(
        targetValue = if (on) 1f else 0f,
        animationSpec = tween(180),
        label = "switch",
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

@Composable
private fun ActionRow(title: String, subtitle: String, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .clickable(onClick = onClick)
            .padding(vertical = Space.Xs),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
            Spacer(Modifier.height(Space.Xs))
            Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = Ink.Tertiary)
        }
        Text("›", style = MaterialTheme.typography.titleMedium, color = Ink.Tertiary)
    }
}

@Composable
private fun DangerButton(label: String, onClick: () -> Unit) {
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(Status.BadSubtle)
            .border(Space.Hair, Status.Bad.copy(alpha = 0.5f), RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(vertical = 13.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = Status.Bad)
    }
}
