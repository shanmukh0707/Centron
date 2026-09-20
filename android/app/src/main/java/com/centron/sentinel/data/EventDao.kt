package com.centron.sentinel.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface EventDao {

    /**
     * REPLACE, not IGNORE. This is load bearing.
     *
     * When a Claude verdict lands late, the server re-sends the same event_id
     * with a new seq. Under IGNORE the row is silently kept at its old state
     * and the updated verdict never reaches the UI. Under REPLACE the new
     * escalation.verdict lands. Do not change this without reading that twice.
     */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(event: EventEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(events: List<EventEntity>)

    @Query("SELECT * FROM events ORDER BY event_id DESC")
    fun observeAll(): Flow<List<EventEntity>>

    @Query("SELECT * FROM events WHERE event_id = :id")
    suspend fun byId(id: String): EventEntity?

    /** Highest seq we have actually persisted, for resync { since_seq }. */
    @Query("SELECT COALESCE(MAX(seq), 0) FROM events")
    suspend fun maxSeq(): Long

    @Query("SELECT * FROM events WHERE spoken = 0 AND severity != 'info' ORDER BY event_id ASC")
    suspend fun pendingSpeech(): List<EventEntity>

    @Query("UPDATE events SET spoken = 1 WHERE event_id = :id")
    suspend fun markSpoken(id: String)

    /**
     * action_update carries no full event, so patch the action columns in place.
     * Leaves `raw` alone deliberately: it is the last full EventData we saw.
     */
    @Query(
        """
        UPDATE events
        SET action_status = :status,
            approval_id   = COALESCE(:approvalId, approval_id),
            expires_at    = COALESCE(:expiresAt, expires_at)
        WHERE event_id = :id
        """
    )
    suspend fun applyActionUpdate(
        id: String,
        status: String,
        approvalId: String?,
        expiresAt: String?,
    )

    @Query("UPDATE events SET audio_status = :status, audio_url = :url WHERE event_id = :id")
    suspend fun applyAudioReady(id: String, status: String, url: String?)
}
