package com.centron.sentinel.data

import com.centron.sentinel.contract.ActionUpdateFrame
import com.centron.sentinel.contract.AudioReadyFrame
import com.centron.sentinel.contract.EventFrame
import com.centron.sentinel.contract.HeartbeatFrame
import com.centron.sentinel.contract.HelloFrame
import com.centron.sentinel.contract.ServerFrame
import com.centron.sentinel.net.SentinelClient
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Applies decoded frames to Room and tracks the seq high-water mark.
 *
 * Kept free of Android framework types so it is unit-testable.
 */
class EventRepository(private val dao: EventDao) {

    private val _missedFrames = MutableStateFlow(false)
    /** True when the server says it has sent more than we have stored. */
    val missedFrames: StateFlow<Boolean> = _missedFrames.asStateFlow()

    fun observeEvents(): Flow<List<EventEntity>> = dao.observeAll()

    suspend fun highWaterMark(): Long = dao.maxSeq()

    suspend fun apply(frame: ServerFrame, client: SentinelClient?) {
        when (frame) {
            is EventFrame -> {
                // REPLACE semantics: a late verdict for a known event_id
                // overwrites the stored row rather than being dropped.
                dao.upsert(frame.data.toEntity(seq = frame.seq, ts = frame.ts))
                client?.ack(frame.seq, frame.data.event_id)
            }

            is ActionUpdateFrame -> dao.applyActionUpdate(
                id = frame.data.event_id,
                status = frame.data.status.name.lowercase(),
                approvalId = frame.data.approval_id,
                expiresAt = frame.data.expires_at,
            )

            is AudioReadyFrame -> dao.applyAudioReady(
                id = frame.data.event_id,
                status = frame.data.audio.status.name.lowercase(),
                url = frame.data.audio.url,
            )

            is HeartbeatFrame -> {
                // last_event_seq lets us notice frames missed during a
                // reconnect. Without it the app loses events silently.
                _missedFrames.value = frame.data.last_event_seq > dao.maxSeq()
            }

            is HelloFrame -> {
                _missedFrames.value = frame.data.last_event_seq > dao.maxSeq()
            }
        }
    }
}
