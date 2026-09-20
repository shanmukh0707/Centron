package com.centron.sentinel.notify

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * System notifications for security events and for the server going dark.
 *
 * Two channels, because they are answering different questions and a user who
 * silences one should not lose the other:
 *
 *   ALERTS    — something happened on the network. High importance, because a
 *               critical event at 3am is the entire product.
 *   AVAILABILITY — the engine stopped answering. Separate channel so "my
 *               server is down" can be tuned apart from "my server found
 *               something".
 */
object Alerts {

    const val CHANNEL_ALERTS = "sentinel_alerts"
    const val CHANNEL_AVAILABILITY = "sentinel_availability"

    private const val ID_AVAILABILITY = 9001

    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ALERTS,
                "Security alerts",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "High and critical events from your homelab"
                enableVibration(true)
            }
        )

        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_AVAILABILITY,
                "Engine availability",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Raised when an engine stops responding"
                enableVibration(true)
            }
        )
    }

    fun canPost(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
        } else {
            NotificationManagerCompat.from(context).areNotificationsEnabled()
        }

    fun postEvent(
        context: Context,
        eventId: String,
        severity: String,
        title: String,
        body: String,
    ) {
        if (!canPost(context)) return

        val critical = severity.equals("critical", ignoreCase = true)

        val notification: Notification = NotificationCompat.Builder(context, CHANNEL_ALERTS)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(
                if (critical) NotificationCompat.PRIORITY_MAX else NotificationCompat.PRIORITY_HIGH
            )
            .setAutoCancel(true)
            .build()

        // event_id hashed to an int so a re-sent verdict updates the existing
        // notification instead of stacking a second one.
        runCatching {
            NotificationManagerCompat.from(context).notify(eventId.hashCode(), notification)
        }
    }

    fun postEngineDown(context: Context, detail: String) {
        if (!canPost(context)) return
        val notification = NotificationCompat.Builder(context, CHANNEL_AVAILABILITY)
            .setContentTitle("Engine unreachable")
            .setContentText(detail)
            .setSmallIcon(android.R.drawable.stat_sys_warning)
            .setCategory(NotificationCompat.CATEGORY_ERROR)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setOngoing(false)
            .build()
        runCatching {
            NotificationManagerCompat.from(context).notify(ID_AVAILABILITY, notification)
        }
    }

    fun clearEngineDown(context: Context) {
        runCatching { NotificationManagerCompat.from(context).cancel(ID_AVAILABILITY) }
    }

    /** Deep link to this app's notification settings, for the permission gate. */
    fun settingsIntent(context: Context): Intent =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, context.packageName)
        } else {
            Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(android.net.Uri.fromParts("package", context.packageName, null))
        }
}
