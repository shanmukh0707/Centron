package com.centron.sentinel.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.RingtoneManager
import android.os.Build
import android.os.CombinedVibration
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.centron.sentinel.contract.Severity
import com.centron.sentinel.net.EngineConfig
import com.centron.sentinel.net.PinnedTrust
import okhttp3.OkHttpClient
import java.util.ArrayDeque
import java.util.concurrent.TimeUnit

/**
 * Speaks events, and decides what gets to interrupt what.
 *
 * The severity contract is defined by phone behaviour, not by feel, so this
 * class is where those four words actually mean something:
 *
 *   info      never spoken. Not queued, not played, not even fetched.
 *   low       queued behind whatever is currently playing.
 *   high      interrupts the queue and plays now.
 *   critical  interrupts, plus an alarm tone and a haptic, because the whole
 *             point is waking someone who is not looking at the screen.
 *
 * Two details that are easy to get wrong:
 *
 * 1. **Only tts_summary is ever spoken.** The backend guarantees it carries no
 *    address, username, port or token, and there is a test enforcing that.
 *    internal_log carries real hostnames and must never reach a speaker or a
 *    lock screen.
 *
 * 2. **Dedupe on cache_key, not event_id.** A late Claude verdict re-emits the
 *    same event with a new seq and the same phrase. Keying on event_id would
 *    be right for storage and wrong here: the operator should not hear the
 *    same sentence twice because a cloud model answered late.
 */
@OptIn(UnstableApi::class)
class SpeechQueue(
    private val context: Context,
    private val configProvider: () -> EngineConfig,
) {

    private data class Utterance(
        val eventId: String,
        val cacheKey: String,
        val url: String,
        val severity: Severity,
    )

    private val tag = "SpeechQueue"

    /**
     * ExoPlayer is single-threaded: it must be built and driven from one
     * Looper thread, and it throws if you touch it from anywhere else.
     * Frames arrive on the socket's coroutine, so every player call hops
     * here first. This is not tidiness — without it the first spoken event
     * takes the app down with an IllegalStateException.
     */
    private val main = Handler(Looper.getMainLooper())

    private val queue = ArrayDeque<Utterance>()
    private val spoken = LinkedHashSet<String>()
    private var player: ExoPlayer? = null
    private var current: Utterance? = null

    /** Bounded so a long session cannot grow this without limit. */
    private val spokenMax = 256

    // ------------------------------------------------------------------ api

    @Synchronized
    fun submit(
        eventId: String,
        severity: Severity,
        audioUrl: String?,
        cacheKey: String?,
        audioStatus: String?,
    ) {
        // The contract's first rule about speech. Checked before anything else
        // so an info event costs nothing at all.
        if (!severity.isSpoken) return

        if (audioStatus != "ready" || audioUrl.isNullOrBlank()) {
            // Still pending, failed, or suppressed. An audio_ready frame for
            // the same event will arrive later and come back through here.
            return
        }

        val key = cacheKey ?: audioUrl
        if (key in spoken) return

        val utterance = Utterance(eventId, key, audioUrl, severity)

        when (severity) {
            Severity.INFO -> return
            Severity.LOW -> {
                queue.addLast(utterance)
                if (current == null) playNext()
            }
            Severity.HIGH -> interruptWith(utterance)
            Severity.CRITICAL -> {
                alarm()
                haptic()
                interruptWith(utterance)
            }
        }
    }

    @Synchronized
    fun stop() {
        queue.clear()
        current = null
        main.post {
            player?.release()
            player = null
        }
    }

    // -------------------------------------------------------------- internals

    private fun interruptWith(utterance: Utterance) {
        // Drop anything queued. If something more urgent has arrived, the
        // backlog is stale by definition -- an operator does not want to hear
        // four minutes of low-severity narration before the critical one.
        queue.clear()
        queue.addLast(utterance)
        playNext(force = true)
    }

    private fun playNext(force: Boolean = false) {
        if (!force && current != null) return
        val next = queue.pollFirst() ?: run { current = null; return }

        current = next
        remember(next.cacheKey)
        main.post { start(next) }
    }

    /** Runs on the main looper. Every ExoPlayer call in this class does. */
    private fun start(utterance: Utterance) {
        val p = ensurePlayer()
        if (p == null) {
            Log.w(tag, "no player available; cannot speak")
            synchronized(this) { current = null }
            return
        }
        try {
            p.setMediaItem(MediaItem.fromUri(utterance.url))
            p.prepare()
            p.play()
            Log.i(tag, "speaking ${utterance.severity} ${utterance.eventId}")
        } catch (e: Exception) {
            // Never let a playback failure take down the stream.
            Log.w(tag, "playback failed for ${utterance.eventId}: ${e.message}")
            synchronized(this) {
                current = null
                playNext()
            }
        }
    }

    private fun remember(key: String) {
        spoken.add(key)
        while (spoken.size > spokenMax) {
            val oldest = spoken.iterator()
            if (oldest.hasNext()) {
                oldest.next()
                oldest.remove()
            } else {
                break
            }
        }
    }

    private fun ensurePlayer(): ExoPlayer? {
        player?.let { return it }

        val config = configProvider()

        // Same client rules as the socket: pinned certificate, bearer token.
        // Building this from PinnedTrust rather than a fresh OkHttpClient is
        // the point -- audio fetched over an unpinned connection would be a
        // hole beside a locked door.
        val http = try {
            PinnedTrust.apply(
                OkHttpClient.Builder()
                    .connectTimeout(10, TimeUnit.SECONDS)
                    .readTimeout(20, TimeUnit.SECONDS),
                config,
            ).build()
        } catch (e: PinnedTrust.MissingPin) {
            Log.w(tag, "not paired; refusing to fetch audio unpinned")
            return null
        }

        val factory = OkHttpDataSource.Factory(http).apply {
            if (config.token.isNotBlank()) {
                setDefaultRequestProperties(
                    mapOf("Authorization" to "Bearer ${config.token}")
                )
            }
        }

        return ExoPlayer.Builder(context)
            .setMediaSourceFactory(DefaultMediaSourceFactory(factory))
            // Pin the player to the main looper rather than whichever thread
            // happened to build it.
            .setLooper(Looper.getMainLooper())
            .build()
            .apply {
                setAudioAttributes(
                    androidx.media3.common.AudioAttributes.Builder()
                        .setUsage(androidx.media3.common.C.USAGE_ALARM)
                        .setContentType(androidx.media3.common.C.AUDIO_CONTENT_TYPE_SPEECH)
                        .build(),
                    // Duck other audio rather than stopping it. This is an
                    // alert, not a media session.
                    false,
                )
                addListener(object : Player.Listener {
                    override fun onPlaybackStateChanged(state: Int) {
                        if (state == Player.STATE_ENDED) {
                            synchronized(this@SpeechQueue) {
                                current = null
                                playNext()
                            }
                        }
                    }

                    override fun onPlayerError(error: androidx.media3.common.PlaybackException) {
                        Log.w(tag, "player error: ${error.errorCodeName}")
                        synchronized(this@SpeechQueue) {
                            current = null
                            playNext()
                        }
                    }
                })
                player = this
            }
    }

    // ------------------------------------------------------------- critical

    /** Short alarm tone ahead of the speech, so the words are not the first
     *  thing a sleeping person has to parse. */
    private fun alarm() {
        try {
            val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
                ?: return
            val ringtone = RingtoneManager.getRingtone(context, uri) ?: return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                ringtone.audioAttributes = AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
            } else {
                @Suppress("DEPRECATION")
                ringtone.streamType = AudioManager.STREAM_ALARM
            }
            ringtone.play()
        } catch (e: Exception) {
            Log.w(tag, "alarm tone failed: ${e.message}")
        }
    }

    private fun haptic() {
        try {
            val pattern = longArrayOf(0, 400, 200, 400)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val manager = context.getSystemService(VibratorManager::class.java) ?: return
                manager.vibrate(
                    CombinedVibration.createParallel(
                        VibrationEffect.createWaveform(pattern, -1)
                    )
                )
            } else {
                @Suppress("DEPRECATION")
                val vibrator = context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
                    ?: return
                vibrator.vibrate(VibrationEffect.createWaveform(pattern, -1))
            }
        } catch (e: Exception) {
            Log.w(tag, "haptic failed: ${e.message}")
        }
    }
}
