package com.centron.sentinel.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import com.centron.sentinel.notify.Alerts
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status

/**
 * Hard gate on notification permission.
 *
 * Worth being precise about what "force" can mean here: Android will not let
 * an app grant itself POST_NOTIFICATIONS, and from API 33 the system only
 * shows the rationale dialog a limited number of times before permanently
 * denying. So there is no way to literally compel it.
 *
 * What this does instead is refuse to run without it, and route the user to
 * the one place that can still turn it on. That is legitimate here in a way it
 * would not be for most apps: an alerting tool whose alerts are switched off
 * is not a degraded product, it is a misleading one. A green "Connected" bar
 * on a phone that will never wake you is worse than no app at all.
 *
 * Re-checks on every resume, so returning from Settings lets you straight in.
 */
@Composable
fun NotificationGate(content: @Composable () -> Unit) {
    val context = LocalContext.current
    var granted by remember { mutableStateOf(Alerts.canPost(context)) }
    var asked by remember { mutableStateOf(false) }

    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { result ->
        granted = result || Alerts.canPost(context)
        asked = true
    }

    // Returning from the system settings screen has to re-evaluate, otherwise
    // the user enables the permission and still stares at the blocker.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) granted = Alerts.canPost(context)
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    if (granted) {
        content()
        return
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(Ink.Base)
            .padding(horizontal = Space.Gutter),
        verticalArrangement = Arrangement.Center,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(Status.Bad))
            Spacer(Modifier.width(Space.Sm + Space.Xs))
            Text(
                "NOTIFICATIONS REQUIRED",
                style = MaterialTheme.typography.labelSmall,
                color = Status.Bad,
            )
        }

        Spacer(Modifier.height(Space.Lg))

        Text(
            "Sentinel is an alarm.",
            style = MaterialTheme.typography.displaySmall,
            color = Ink.Primary,
        )

        Spacer(Modifier.height(Space.Md))

        Text(
            "Without notification permission this app cannot wake you when " +
                "your homelab is attacked or your engine goes dark. It would " +
                "show a reassuring green bar and tell you nothing. " +
                "That is worse than not running it.",
            style = MaterialTheme.typography.bodyLarge,
            color = Ink.Secondary,
        )

        Spacer(Modifier.height(Space.Xl))

        GateButton(
            label = if (asked) "Open notification settings" else "Enable notifications",
            fill = Accent.Clay,
            content = Ink.Base,
        ) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !asked) {
                launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
            } else {
                // Either the OS will no longer show the dialog, or we are on a
                // version where the channel toggle is the only control.
                context.startActivity(Alerts.settingsIntent(context))
            }
        }

        Spacer(Modifier.height(Space.Md))

        Text(
            "Sentinel will re-check as soon as you come back.",
            style = MaterialTheme.typography.bodyMedium,
            color = Ink.Tertiary,
        )
    }
}

@Composable
private fun GateButton(
    label: String,
    fill: androidx.compose.ui.graphics.Color,
    content: androidx.compose.ui.graphics.Color,
    onClick: () -> Unit,
) {
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(fill)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(14.dp))
            .clickable(onClick = onClick)
            .padding(vertical = 15.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = content)
    }
}
