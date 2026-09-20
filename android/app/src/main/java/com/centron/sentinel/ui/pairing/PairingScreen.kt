package com.centron.sentinel.ui.pairing

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
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
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
import androidx.compose.ui.unit.dp
import com.centron.sentinel.net.EngineConfig
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status

/**
 * Pair with an engine.
 *
 * The code comes from `tools/centron-pair.sh` on serverpi and carries three
 * things: where the engine is, the SHA-256 of its certificate, and the
 * pre-shared token. Those last two are what authenticate this phone —
 * `biometric: true` on an approval is a claim about the operator, not proof to
 * the engine.
 *
 * QR pairing is deliberately not built. contracts.md: "Cut QR pairing first if
 * you run out of time. Hardcode the token or use a typed PIN. It costs real
 * hours and adds nothing to the score."
 */
@Composable
fun PairingScreen(
    current: EngineConfig,
    onPair: (String) -> Boolean,
    onUseLocalStub: () -> Unit,
    onBack: (() -> Unit)? = null,
) {
    var code by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    Column(
        Modifier
            .fillMaxSize()
            .background(Ink.Base)
            .verticalScroll(rememberScrollState())
            .imePadding()
            .padding(horizontal = Space.Gutter),
    ) {
        Spacer(Modifier.height(Space.Lg))

        onBack?.let {
            Text(
                "Back",
                style = MaterialTheme.typography.labelSmall,
                color = Ink.Secondary,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(onClick = it)
                    .padding(horizontal = Space.Sm, vertical = Space.Xs),
            )
        }

        Spacer(Modifier.height(Space.Xl))

        Text(
            "Pair with your\nengine.",
            style = MaterialTheme.typography.displaySmall,
            color = Ink.Primary,
        )
        Spacer(Modifier.height(Space.Md))
        Text(
            "On serverpi, run ./tools/centron-pair.sh and paste the line it prints.",
            style = MaterialTheme.typography.bodyLarge,
            color = Ink.Secondary,
        )

        Spacer(Modifier.height(Space.Xl))

        Text("PAIRING CODE", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
        Spacer(Modifier.height(Space.Sm))
        Box(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(Ink.Surface)
                .border(
                    Space.Hair,
                    if (error != null) Status.Bad.copy(alpha = 0.6f) else Ink.Hairline,
                    RoundedCornerShape(12.dp),
                )
                .padding(Space.Md),
        ) {
            if (code.isEmpty()) {
                Text("centron://100.110.30.122:8765/…", style = MonoSmall, color = Ink.Tertiary)
            }
            BasicTextField(
                value = code,
                onValueChange = { code = it; error = null },
                textStyle = MonoSmall.copy(color = Ink.Primary),
                cursorBrush = SolidColor(Accent.Clay),
                modifier = Modifier.fillMaxWidth(),
            )
        }

        error?.let {
            Spacer(Modifier.height(Space.Sm))
            Row {
                Box(
                    Modifier
                        .padding(top = 6.dp)
                        .size(5.dp)
                        .clip(CircleShape)
                        .background(Status.Bad),
                )
                Spacer(Modifier.width(Space.Sm))
                Text(it, style = MaterialTheme.typography.bodyMedium, color = Ink.Secondary)
            }
        }

        Spacer(Modifier.height(Space.Lg))

        val ready = code.isNotBlank()
        Box(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(14.dp))
                .background(if (ready) Accent.Clay else Ink.Raised)
                .clickable(enabled = ready) {
                    if (!onPair(code)) {
                        error = "That does not look like a pairing code. Expected " +
                            "centron://host:port/<64 hex chars>/<token>. Re-run " +
                            "centron-pair.sh and copy the whole line."
                    }
                }
                .padding(vertical = 15.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                "Pair",
                style = MaterialTheme.typography.labelLarge,
                color = if (ready) Ink.Base else Ink.Tertiary,
            )
        }

        Spacer(Modifier.height(Space.Lg))

        // What the pin buys, stated plainly, because "pinned certificate" is
        // not self-explanatory and the failure mode later is confusing.
        Column(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(Ink.Surface)
                .border(Space.Hair, Ink.Hairline, RoundedCornerShape(12.dp))
                .padding(Space.Md),
        ) {
            Text("WHAT THIS DOES", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
            Spacer(Modifier.height(Space.Sm))
            Text(
                "Centron will accept only this one certificate from this one engine — " +
                    "no certificate authority can issue a substitute. If the engine's " +
                    "certificate is ever regenerated, pairing has to be redone. That " +
                    "refusal is the protection working, not a fault.",
                style = MaterialTheme.typography.bodyMedium,
                color = Ink.Secondary,
            )
        }

        Spacer(Modifier.height(Space.Lg))

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Center,
        ) {
            Text(
                if (current.useTls) "Use the local stub instead" else "Using the local stub",
                style = MaterialTheme.typography.bodyMedium,
                color = Ink.Tertiary,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(onClick = onUseLocalStub)
                    .padding(Space.Sm),
            )
        }

        Spacer(Modifier.height(Space.Xxl))
    }
}
