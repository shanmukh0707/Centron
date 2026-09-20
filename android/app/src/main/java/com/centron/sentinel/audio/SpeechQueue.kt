package com.centron.sentinel.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
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
    private var alarmTrack: AudioTrack? = null

    /** Bounded so a long session cannot grow this without limit. */
    private val spokenMax = 256

    private companion object {
        const val TONE_RATE = 44100
        const val TONE_TOTAL_MS = 590L
        /** Well below full scale: this plays under, then into, the speech. */
        const val TONE_AMPLITUDE = 0.38
    }

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
            stopAlarm()
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

    /**
     * Two descending tones ahead of the speech, so the words are not the first
     * thing a sleeping person has to parse.
     *
     * Synthesised rather than played from RingtoneManager, for two reasons.
     * The system alarm tone loops by design and has to be told to stop, which
     * is how it ended up running under the whole alert. And whatever the owner
     * happens to have chosen is often cheerful, which is the wrong register
     * for a message about someone attacking your network.
     *
     * A falling minor third at low pitch reads as an alert rather than a
     * notification chime. Fixed length, so there is nothing to stop.
     */
    private fun alarm() {
        try {
            stopAlarm()
            val pcm = alertTone()
            val track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(TONE_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build()
                )
                .setTransferMode(AudioTrack.MODE_STATIC)
                .setBufferSizeInBytes(pcm.size * 2)
                .build()

            track.write(pcm, 0, pcm.size)
            track.play()
            alarmTrack = track
            // Released once it has certainly finished. Without this the track
            // leaks, and enough criticals in a row exhaust the audio session.
            main.postDelayed({ stopAlarm() }, TONE_TOTAL_MS + 300L)
        } catch (e: Exception) {
            Log.w(tag, "alarm tone failed: ${e.message}")
        }
    }

    private fun stopAlarm() {
        val track = alarmTrack ?: return
        alarmTrack = null
        runCatching {
            if (track.state == AudioTrack.STATE_INITIALIZED) track.stop()
            track.release()
        }
    }

    /**
     * Two tones, G3 then E3, with short fades.
     *
     * The fades are not decoration: a square-edged start or end on a sine is
     * an audible click, and a click at the front of an alert sounds like a
     * fault in the app rather than part of the sound.
     */
    private fun alertTone(): ShortArray {
        val toneMs = 260
        val gapMs = 70
        val toneSamples = TONE_RATE * toneMs / 1000
        val gapSamples = TONE_RATE * gapMs / 1000
        val out = ShortArray(toneSamples * 2 + gapSamples)

        fun writeTone(offset: Int, freq: Double) {
            val fade = TONE_RATE * 8 / 1000
            for (i in 0 until toneSamples) {
                val envelope = when {
                    i < fade -> i.toDouble() / fade
                    i > toneSamples - fade -> (toneSamples - i).toDouble() / fade
                    else -> 1.0
                }
                val sample = kotlin.math.sin(2.0 * Math.PI * freq * i / TONE_RATE)
                out[offset + i] = (sample * envelope * TONE_AMPLITUDE * Short.MAX_VALUE).toInt().toShort()
            }
        }

        writeTone(0, 196.0)                                  // G3
        writeTone(toneSamples + gapSamples, 164.81)          // E3
        return out
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
