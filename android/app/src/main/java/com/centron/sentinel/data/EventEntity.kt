package com.centron.sentinel.data

import androidx.room.Entity
import androidx.room.PrimaryKey
import com.centron.sentinel.contract.EventData
import com.centron.sentinel.contract.SentinelJson
import kotlinx.serialization.encodeToString

/**
 * Room row for one event.
 *
 * Primary key is event_id (UUIDv7, time sortable), which is the join key
 * between server SQLite and phone Room. Replays and resyncs dedupe for free.
 *
 * Enums are stored as their wire strings rather than Room TypeConverters, so a
 * severity value added later lands in the DB instead of crashing the insert.
 *
 * `raw` keeps the full EventData JSON so the detail view never has to
 * reconstruct anything, and so a field we do not yet column-ise is not lost.
 */
@Entity(tableName = "events")
data class EventEntity(
    @PrimaryKey val event_id: String,

    /** Reserved for multi-engine (Centron v2). Single server today. */
    val server_id: String = "default",

    val seq: Long,
    val ts: String,

    val signature: String,
    val severity: String,
    val title: String,
    val internal_log: String,
    val tts_summary: String,
    val confidence: Double,
    val reviewed: Boolean,

    val escalated: Boolean,
    val escalation_gate: String?,
    val escalation_reason: String?,
    val escalation_state: String?,
    val escalation_verdict: String?,

    val action_playbook: String?,
    val action_status: String?,
    val requires_approval: Boolean,
    val approval_id: String?,
    val expires_at: String?,

    val audio_status: String?,
    val audio_url: String?,
    val audio_cache_key: String?,

    /** Local only. Not on the wire. Stops TTS replaying on resync. */
    val spoken: Boolean = false,

    val raw: String,
) {
    fun toEventData(): EventData = SentinelJson.decodeFromString(raw)
}

fun EventData.toEntity(seq: Long, ts: String, serverId: String = "default"): EventEntity =
    EventEntity(
        event_id = event_id,
        server_id = serverId,
        seq = seq,
        ts = ts,
        signature = signature,
        severity = severity.name.lowercase(),
        title = title,
        internal_log = internal_log,
        tts_summary = tts_summary,
        confidence = confidence,
        reviewed = reviewed,
        escalated = escalation?.escalated ?: false,
        escalation_gate = escalation?.gate?.name?.lowercase(),
        escalation_reason = escalation?.reason,
        escalation_state = escalation?.state?.name?.lowercase(),
        escalation_verdict = escalation?.verdict,
        action_playbook = action?.playbook?.name?.lowercase(),
        action_status = action?.status?.name?.lowercase(),
        requires_approval = action?.requires_approval ?: false,
        approval_id = action?.approval_id,
        expires_at = action?.expires_at,
        audio_status = audio?.status?.name?.lowercase(),
        audio_url = audio?.url,
        audio_cache_key = audio?.cache_key,
        raw = SentinelJson.encodeToString(this),
    )
