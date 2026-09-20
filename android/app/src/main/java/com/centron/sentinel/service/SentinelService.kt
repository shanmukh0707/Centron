package com.centron.sentinel.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.centron.sentinel.audio.SpeechQueue
import com.centron.sentinel.contract.AudioReadyFrame
import com.centron.sentinel.contract.EventFrame
import com.centron.sentinel.contract.Severity
import com.centron.sentinel.data.EventDao
import com.centron.sentinel.data.EventRepository
import com.centron.sentinel.data.SentinelDatabase
import com.centron.sentinel.net.ActiveClient
import com.centron.sentinel.net.EngineConfig
import com.centron.sentinel.net.PinnedTrust
import com.centron.sentinel.settings.AppSettings
import com.centron.sentinel.notify.AlertCenter
import com.centron.sentinel.notify.Alerts
import com.centron.sentinel.net.ConnectionState
import com.centron.sentinel.net.ConnectionStatus
import com.centron.sentinel.net.SentinelClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * Keeps the event stream alive while the app is backgrounded.
 *
 * Required for the demo: the phone must speak across the room without someone
 * holding it. Battery optimization must be disabled on the demo device or
 * Android will eventually throttle this anyway.
 */
class SentinelService : Service() {

    private val scope = CoroutineScope(SupervisorJob())
    private var client: SentinelClient? = null
    private var collectors: Job? = null
    private var activeConfig: EngineConfig? = null
    private var speech: SpeechQueue? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val config = AppSettings.engineConfig.value

        createChannel()
        startForeground(NOTIFICATION_ID, buildNotification(ConnectionState.CONNECTING))

        // Re-pairing changes the address, pin and token. Tear the old socket
        // down rather than leaving it pointed at the previous engine.
        if (client == null || activeConfig != config) {
            client?.disconnect()
            collectors?.cancel()
            client = null
            activeConfig = config
            start(config)
        }
        // Restart if the system kills us. The stream is the product.
        return START_STICKY
    }

    private fun start(config: EngineConfig) {
        val repo = EventRepository(SentinelDatabase.get(this).events())
        val c = try {
            SentinelClient(config = config, scope = scope, highWaterMark = repo::highWaterMark)
        } catch (e: PinnedTrust.MissingPin) {
            // Configured for TLS but never paired. Refusing is correct: the
            // alternative is trusting any certificate, which is worse than
            // not connecting.
            android.util.Log.e("SentinelService", e.message.orEmpty())
            ConnectionStatus.publish(ConnectionState.RED)
            return
        }
        client = c
        ActiveClient.publish(c)

        Alerts.ensureChannels(this)

        val dao = SentinelDatabase.get(this).events()
        speech = SpeechQueue(applicationContext) { AppSettings.engineConfig.value }

        collectors = scope.launch {
            launch {
                c.frames.collect { frame ->
                    repo.apply(frame, c)
                    when (frame) {
                        is EventFrame -> {
                            raiseAlertIfNeeded(frame)
                            speakEvent(frame)
                        }
                        // Audio that arrived after its event. Nothing was
                        // spoken at the time because there was nothing to
                        // play; this is where the phone catches up.
                        is AudioReadyFrame -> speakLateAudio(dao, frame)
                        else -> Unit
                    }
                }
            }
            launch {
                c.state.collectLatest { state ->
                    ConnectionStatus.publish(state)
                    notificationManager().notify(NOTIFICATION_ID, buildNotification(state))

                    // Red means the heartbeat has been silent for 25s. The
                    // engine runs on the homelab, so if the homelab is down
                    // it cannot tell us — noticing the silence is the phone's
                    // job, and this is where that surfaces.
                    if (state == ConnectionState.RED) {
                        Alerts.postEngineDown(
                            this@SentinelService,
                            "No heartbeat for 25 seconds.",
                        )
                    } else if (state == ConnectionState.GREEN) {
                        Alerts.clearEngineDown(this@SentinelService)
                    }
                }
            }
        }

        c.connect()
    }

    /**
     * High and critical only. Info logs silently and low queues behind
     * playback per the severity contract; neither earns an interruption.
     */
    private fun raiseAlertIfNeeded(frame: EventFrame) {
        val data = frame.data
        val severity = data.severity.name.lowercase()
        if (severity != "high" && severity != "critical") return

        AlertCenter.push(
            AlertCenter.Alert(
                eventId = data.event_id,
                severity = severity,
                title = data.title,
                // tts_summary is value-free by construction, so it is the safe
                // string to show on a lock screen. internal_log carries real
                // hostnames and addresses and must not leak there.
                summary = data.tts_summary,
            )
        )

        Alerts.postEvent(
            context = this,
            eventId = data.event_id,
            severity = severity,
            title = data.title,
            body = data.tts_summary,
        )
    }

    /**
     * Speak an event, if the operator has speech on and the audio is ready.
     *
     * SpeechQueue enforces the severity contract; this only decides whether to
     * offer it at all.
     */
    private fun speakEvent(frame: EventFrame) {
        if (!AppSettings.speakAlerts.value) return
        val d = frame.data
        speech?.submit(
            eventId = d.event_id,
            severity = d.severity,
            audioUrl = d.audio?.url,
            cacheKey = d.audio?.cache_key,
            audioStatus = d.audio?.status?.name?.lowercase(),
        )
    }

    /**
     * audio_ready carries only event_id and the audio ref, so severity has to
     * come from the stored event. Room already has it: the event was written
     * before this frame could arrive.
     */
    private suspend fun speakLateAudio(dao: EventDao, frame: AudioReadyFrame) {
        if (!AppSettings.speakAlerts.value) return
        val stored = dao.byId(frame.data.event_id) ?: return
        val severity = runCatching {
            Severity.valueOf(stored.severity.uppercase())
        }.getOrNull() ?: return

        speech?.submit(
            eventId = frame.data.event_id,
            severity = severity,
            audioUrl = frame.data.audio.url,
            cacheKey = frame.data.audio.cache_key,
            audioStatus = frame.data.audio.status.name.lowercase(),
        )
    }

    override fun onDestroy() {
        speech?.stop()
        speech = null
        ActiveClient.publish(null)
        client?.disconnect()
        collectors?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    // ------------------------------------------------------------ notification

    private fun notificationManager() =
        getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Sentinel connection",
                NotificationManager.IMPORTANCE_LOW,
            ).apply { description = "Ongoing connection to the Sentinel server" }
            notificationManager().createNotificationChannel(channel)
        }
    }

    private fun buildNotification(state: ConnectionState): Notification {
        val text = when (state) {
            ConnectionState.CONNECTING -> "Connecting…"
            ConnectionState.GREEN -> "Connected"
            ConnectionState.AMBER -> "No heartbeat for 15s"
            ConnectionState.RED -> "Disconnected — pipeline untrusted"
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Sentinel")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    companion object {
        const val CHANNEL_ID = "sentinel_connection"
        const val NOTIFICATION_ID = 1

        fun start(context: Context) {
            val intent = Intent(context, SentinelService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
