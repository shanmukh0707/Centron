package com.centron.sentinel.net

import android.util.Log
import com.centron.sentinel.contract.AckData
import com.centron.sentinel.contract.AckFrame
import com.centron.sentinel.contract.ActionUpdateFrame
import com.centron.sentinel.contract.ApproveActionData
import com.centron.sentinel.contract.ApproveActionFrame
import com.centron.sentinel.contract.AudioReadyFrame
import com.centron.sentinel.contract.ClientFrame
import com.centron.sentinel.contract.Decision
import com.centron.sentinel.contract.EventFrame
import com.centron.sentinel.contract.HeartbeatFrame
import com.centron.sentinel.contract.HelloFrame
import com.centron.sentinel.contract.ResyncData
import com.centron.sentinel.contract.ResyncFrame
import com.centron.sentinel.contract.SentinelJson
import com.centron.sentinel.contract.ServerFrame
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * WebSocket client for the Sentinel event stream.
 *
 * Responsibilities kept deliberately narrow: connect, decode, expose frames,
 * track seq, resync after reconnect. Persistence and TTS live elsewhere.
 */
class SentinelClient(
    private val config: EngineConfig,
    private val scope: CoroutineScope,
    /** Highest seq already persisted. Used for resync after reconnect. */
    private val highWaterMark: suspend () -> Long,
) {
    private val tag = "SentinelClient"

    private val http = PinnedTrust.apply(
        OkHttpClient.Builder()
            // Contract: 20-30s. Keeps NAT alive and surfaces dead sockets sooner.
            .pingInterval(25, TimeUnit.SECONDS)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS),
        config,
    ).build()

    private var socket: WebSocket? = null
    private var watchdog: Job? = null
    private var reconnectJob: Job? = null
    private var closedByUs = false

    private val outboundSeq = AtomicLong(0)
    private val lastHeartbeatAt = AtomicLong(0)

    private val _state = MutableStateFlow(ConnectionState.CONNECTING)
    val state: StateFlow<ConnectionState> = _state.asStateFlow()

    private val _frames = MutableSharedFlow<ServerFrame>(extraBufferCapacity = 128)
    val frames = _frames.asSharedFlow()

    private val _serverId = MutableStateFlow<String?>(null)
    val serverId: StateFlow<String?> = _serverId.asStateFlow()

    fun connect() {
        closedByUs = false
        openSocket(attempt = 0)
        startWatchdog()
    }

    fun disconnect() {
        closedByUs = true
        watchdog?.cancel()
        reconnectJob?.cancel()
        socket?.close(1000, "client shutdown")
        socket = null
    }

    // ------------------------------------------------------------ outbound

    fun ack(seq: Long, eventId: String?) =
        send(AckFrame(seq = nextSeq(), ts = nowIso(), data = AckData(seq, eventId)))

    fun approve(approvalId: String, eventId: String, decision: Decision, biometric: Boolean) =
        send(
            ApproveActionFrame(
                seq = nextSeq(),
                ts = nowIso(),
                data = ApproveActionData(approvalId, eventId, decision, biometric),
            )
        )

    fun resync(sinceSeq: Long) =
        send(ResyncFrame(seq = nextSeq(), ts = nowIso(), data = ResyncData(sinceSeq)))

    private fun send(frame: ClientFrame): Boolean {
        val s = socket ?: return false
        return s.send(SentinelJson.encodeToString(frame))
    }

    private fun nextSeq(): Long = outboundSeq.incrementAndGet()

    // ------------------------------------------------------------ socket

    private fun openSocket(attempt: Int) {
        val request = Request.Builder()
            .url(config.wsUrl)
            .apply {
                // Pre-shared token. Per contracts.md this plus the pinned
                // certificate is what actually authenticates the client.
                if (config.token.isNotBlank()) {
                    header("Authorization", "Bearer ${config.token}")
                }
            }
            .build()
        socket = http.newWebSocket(request, object : WebSocketListener() {

            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(tag, "socket open")
                // Do NOT set GREEN here. Only a heartbeat may do that.
                scope.launch {
                    val since = highWaterMark()
                    // Resync after EVERY reconnect, not just after a known gap.
                    resync(since)
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val frame = try {
                    SentinelJson.decodeFromString<ServerFrame>(text)
                } catch (e: Exception) {
                    // A frame we cannot decode is a contract problem worth
                    // shouting about, but it must never kill the stream.
                    Log.e(tag, "undecodable frame: ${e.message}")
                    return
                }

                if (frame is HeartbeatFrame) {
                    lastHeartbeatAt.set(System.currentTimeMillis())
                }
                if (frame is HelloFrame) {
                    _serverId.value = frame.data.server_id
                    lastHeartbeatAt.set(System.currentTimeMillis())
                }

                scope.launch { _frames.emit(frame) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                // Reconnect trigger only. State stays heartbeat-driven.
                Log.w(tag, "socket failure: ${t.message}")
                scheduleReconnect(attempt + 1)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (!closedByUs) scheduleReconnect(attempt + 1)
            }
        })
    }

    private fun scheduleReconnect(attempt: Int) {
        if (closedByUs) return
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            val backoff = minOf(30_000L, 500L * (1L shl minOf(attempt, 6)))
            Log.i(tag, "reconnect in ${backoff}ms (attempt $attempt)")
            delay(backoff)
            if (!closedByUs) openSocket(attempt)
        }
    }

    /**
     * The only thing that sets connection colour. Runs regardless of socket
     * state, so a silently dead-but-open socket still goes red on schedule.
     */
    private fun startWatchdog() {
        watchdog?.cancel()
        watchdog = scope.launch {
            while (true) {
                val last = lastHeartbeatAt.get()
                _state.value =
                    if (last == 0L) ConnectionState.CONNECTING
                    else ConnectionState.fromGap(System.currentTimeMillis() - last)
                delay(1_000)
            }
        }
    }

    companion object {
        private val iso = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
            .apply { timeZone = TimeZone.getTimeZone("UTC") }

        /** Client frames carry a ts. Phone formats timestamps; it never
         *  generates them for events. This is our own envelope only. */
        fun nowIso(): String = synchronized(iso) { iso.format(Date()) }
    }
}
