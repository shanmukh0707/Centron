package com.centron.sentinel.ui.home

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.centron.sentinel.device.Capability
import com.centron.sentinel.device.DeviceType
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status

/**
 * Add a device.
 *
 * Three questions in the order they actually matter: where is it, what is it,
 * and what should Centron be allowed to do with it. Type is asked before
 * capabilities because type supplies the defaults — answering "Raspberry Pi"
 * means the operator only has to correct the list rather than build it.
 */
@Composable
fun AddDeviceScreen(
    /** True while the engine is attempting the SSH connection itself. */
    probing: Boolean,
    /** What the engine reported back — success detail or why it failed. */
    probeMessage: String?,
    probeFailed: Boolean,
    onCancel: () -> Unit,
    onAdd: (
        name: String,
        type: DeviceType,
        role: String,
        host: String,
        user: String,
        port: Int,
        capabilities: Set<Capability>,
    ) -> Unit,
) {
    var step by remember { mutableStateOf(0) }

    var host by remember { mutableStateOf("") }
    var user by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("22") }
    var type by remember { mutableStateOf<DeviceType?>(null) }
    var name by remember { mutableStateOf("") }
    var role by remember { mutableStateOf("") }
    var caps by remember { mutableStateOf<Set<Capability>>(emptySet()) }

    Column(
        Modifier
            .fillMaxSize()
            .background(Ink.Base),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .background(Ink.Surface)
                .padding(horizontal = Space.Gutter, vertical = Space.Md),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                if (step == 0) "Cancel" else "Back",
                style = MaterialTheme.typography.labelSmall,
                color = Ink.Secondary,
                modifier = Modifier.clickable { if (step == 0) onCancel() else step-- },
            )
            Spacer(Modifier.weight(1f))
            Text("STEP ${step + 1} OF 3", style = MonoSmall, color = Ink.Tertiary)
        }
        Box(Modifier.fillMaxWidth().height(Space.Hair).background(Ink.Hairline))

        AnimatedContent(
            targetState = step,
            transitionSpec = {
                val forward = targetState > initialState
                (fadeIn(tween(180)) + slideInHorizontally(tween(240)) { if (forward) it / 6 else -it / 6 })
                    .togetherWith(fadeOut(tween(120)))
            },
            label = "step",
        ) { current ->
            Column(
                Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(Space.Gutter),
            ) {
                when (current) {
                    0 -> StepTarget(
                        host = host, onHost = { host = it },
                        user = user, onUser = { user = it },
                        port = port, onPort = { port = it.filter(Char::isDigit).take(5) },
                        onNext = { step = 1 },
                    )

                    1 -> StepType(
                        selected = type,
                        onSelect = {
                            type = it
                            caps = it.defaults
                            if (name.isBlank()) name = host.substringBefore('.')
                            step = 2
                        },
                    )

                    else -> StepDetails(
                        type = type ?: DeviceType.OTHER,
                        name = name, onName = { name = it },
                        role = role, onRole = { role = it },
                        caps = caps,
                        probing = probing,
                        probeMessage = probeMessage,
                        probeFailed = probeFailed,
                        onToggleCap = { cap ->
                            caps = if (cap in caps) caps - cap else caps + cap
                        },
                        onAdd = {
                            onAdd(
                                name.ifBlank { host },
                                type ?: DeviceType.OTHER,
                                role.ifBlank { (type ?: DeviceType.OTHER).label },
                                host,
                                user,
                                port.toIntOrNull() ?: 22,
                                caps,
                            )
                        },
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------- step 1

@Composable
private fun StepTarget(
    host: String, onHost: (String) -> Unit,
    user: String, onUser: (String) -> Unit,
    port: String, onPort: (String) -> Unit,
    onNext: () -> Unit,
) {
    Text("Where is it?", style = MaterialTheme.typography.headlineMedium, color = Ink.Primary)
    Spacer(Modifier.height(Space.Sm))
    Text(
        "Centron reaches devices over SSH from the engine, not from your phone.",
        style = MaterialTheme.typography.bodyMedium,
        color = Ink.Secondary,
    )

    Spacer(Modifier.height(Space.Lg))

    Field("HOST OR IP", host, onHost, placeholder = "100.110.30.122")
    Spacer(Modifier.height(Space.Md))
    Field("SSH USER", user, onUser, placeholder = "pi")
    Spacer(Modifier.height(Space.Md))
    Field("PORT", port, onPort, placeholder = "22", numeric = true)

    Spacer(Modifier.height(Space.Lg))

    /*
     * This notice is not decoration. The whole security model rests on keys
     * living on the engine, and an operator who expects to paste a key here
     * should find out now rather than after typing one.
     */
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(12.dp))
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
        Text(
            "No key or password is entered here. Authorise the engine's public key on this host, the way you would for any other machine.",
            style = MaterialTheme.typography.bodyMedium,
            color = Ink.Secondary,
        )
    }

    Spacer(Modifier.height(Space.Lg))

    PrimaryButton("Continue", enabled = host.isNotBlank() && user.isNotBlank(), onClick = onNext)
}

// ---------------------------------------------------------------- step 2

@Composable
private fun StepType(selected: DeviceType?, onSelect: (DeviceType) -> Unit) {
    Text("What is it?", style = MaterialTheme.typography.headlineMedium, color = Ink.Primary)
    Spacer(Modifier.height(Space.Sm))
    Text(
        "This sets what Centron offers to do with it. You can change any of it next.",
        style = MaterialTheme.typography.bodyMedium,
        color = Ink.Secondary,
    )

    Spacer(Modifier.height(Space.Lg))

    DeviceType.entries.forEach { entry ->
        val chosen = entry == selected
        Column(
            Modifier
                .fillMaxWidth()
                .padding(bottom = Space.Sm)
                .clip(RoundedCornerShape(14.dp))
                .background(if (chosen) Ink.Raised else Ink.Surface)
                .border(
                    Space.Hair,
                    if (chosen) Accent.Clay else Ink.Hairline,
                    RoundedCornerShape(14.dp),
                )
                .clickable { onSelect(entry) }
                .padding(Space.Md),
        ) {
            Text(entry.label, style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
            Spacer(Modifier.height(2.dp))
            Text(entry.blurb, style = MaterialTheme.typography.bodyMedium, color = Ink.Tertiary)
            Spacer(Modifier.height(Space.Sm))
            Text(
                entry.defaults.joinToString(" · ") { it.label },
                style = MonoSmall,
                color = Ink.Tertiary,
            )
        }
    }
}

// ---------------------------------------------------------------- step 3

@Composable
private fun StepDetails(
    type: DeviceType,
    name: String, onName: (String) -> Unit,
    role: String, onRole: (String) -> Unit,
    caps: Set<Capability>,
    probing: Boolean,
    probeMessage: String?,
    probeFailed: Boolean,
    onToggleCap: (Capability) -> Unit,
    onAdd: () -> Unit,
) {
    Text("What does it do?", style = MaterialTheme.typography.headlineMedium, color = Ink.Primary)
    Spacer(Modifier.height(Space.Sm))
    Text(
        "Name it the way you think of it, not the way DNS does.",
        style = MaterialTheme.typography.bodyMedium,
        color = Ink.Secondary,
    )

    Spacer(Modifier.height(Space.Lg))

    Field("NAME", name, onName, placeholder = "serverpi")
    Spacer(Modifier.height(Space.Md))
    Field("ROLE", role, onRole, placeholder = "Pi-hole and log collector")

    Spacer(Modifier.height(Space.Lg))

    Text("CENTRON MAY", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
    Spacer(Modifier.height(Space.Sm))

    Capability.entries.forEach { cap ->
        val on = cap in caps
        Row(
            Modifier
                .fillMaxWidth()
                .padding(bottom = Space.Sm)
                .clip(RoundedCornerShape(12.dp))
                .background(Ink.Surface)
                .border(
                    Space.Hair,
                    if (on) Accent.Clay.copy(alpha = 0.5f) else Ink.Hairline,
                    RoundedCornerShape(12.dp),
                )
                .clickable { onToggleCap(cap) }
                .padding(horizontal = Space.Md, vertical = 13.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                Modifier
                    .size(16.dp)
                    .clip(RoundedCornerShape(5.dp))
                    .background(if (on) Accent.Clay else Ink.Raised)
                    .border(
                        Space.Hair,
                        if (on) Accent.Clay else Ink.Hairline,
                        RoundedCornerShape(5.dp),
                    ),
            )
            Spacer(Modifier.width(Space.Md))
            Text(
                cap.label,
                style = MaterialTheme.typography.labelLarge,
                color = if (on) Ink.Primary else Ink.Secondary,
            )
            Spacer(Modifier.weight(1f))
            if (cap == Capability.POWER && on) {
                // Power is the one capability that can take a box off the
                // network entirely, so it is flagged rather than silently on.
                Text("destructive", style = MonoSmall, color = Status.Warn)
            }
        }
    }

    Spacer(Modifier.height(Space.Md))

    /*
     * The button verifies before it adds. Centron asks the engine to make the
     * SSH connection itself and report what it found; only then does the
     * device join the list.
     *
     * A device added without that round trip is a device you believe you are
     * monitoring and are not — the failure shows up as silence, which is the
     * one failure mode a security tool cannot have.
     */
    probeMessage?.let { message ->
        Row(
            Modifier
                .fillMaxWidth()
                .padding(bottom = Space.Md)
                .clip(RoundedCornerShape(12.dp))
                .background(if (probeFailed) Status.BadSubtle else Status.OkSubtle)
                .border(
                    Space.Hair,
                    (if (probeFailed) Status.Bad else Status.Ok).copy(alpha = 0.4f),
                    RoundedCornerShape(12.dp),
                )
                .padding(Space.Md),
        ) {
            Box(
                Modifier
                    .padding(top = 6.dp)
                    .size(5.dp)
                    .clip(CircleShape)
                    .background(if (probeFailed) Status.Bad else Status.Ok),
            )
            Spacer(Modifier.width(Space.Sm + Space.Xs))
            Text(message, style = MaterialTheme.typography.bodyMedium, color = Ink.Secondary)
        }
    }

    PrimaryButton(
        label = when {
            probing -> "Checking with the engine…"
            probeFailed -> "Try again"
            else -> "Verify and add"
        },
        enabled = !probing,
        onClick = onAdd,
    )
    Spacer(Modifier.height(Space.Xxl))
}

// ---------------------------------------------------------------- shared

@Composable
private fun Field(
    label: String,
    value: String,
    onChange: (String) -> Unit,
    placeholder: String,
    numeric: Boolean = false,
) {
    Column {
        Text(label, style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
        Spacer(Modifier.height(Space.Sm))
        Box(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(Ink.Surface)
                .border(Space.Hair, Ink.Hairline, RoundedCornerShape(12.dp))
                .padding(horizontal = Space.Md, vertical = 14.dp),
        ) {
            if (value.isEmpty()) {
                Text(placeholder, style = MaterialTheme.typography.bodyLarge, color = Ink.Tertiary)
            }
            BasicTextField(
                value = value,
                onValueChange = onChange,
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyLarge.copy(color = Ink.Primary),
                cursorBrush = SolidColor(Accent.Clay),
                keyboardOptions = KeyboardOptions(
                    keyboardType = if (numeric) KeyboardType.Number else KeyboardType.Text
                ),
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun PrimaryButton(label: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(if (enabled) Accent.Clay else Ink.Raised)
            .clickable(enabled = enabled, onClick = onClick)
            .padding(vertical = 15.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelLarge,
            color = if (enabled) Ink.Base else Ink.Tertiary,
        )
    }
}
