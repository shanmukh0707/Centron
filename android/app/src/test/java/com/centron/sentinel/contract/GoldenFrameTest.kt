package com.centron.sentinel.contract

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The 8 files in samples/ are known-good frames. Contract says parse those as
 * test fixtures before you ever open a socket. This is that.
 */
class GoldenFrameTest {

    private fun sample(name: String): String =
        requireNotNull(javaClass.getResourceAsStream("/samples/$name.json")) {
            "missing fixture: $name.json"
        }.bufferedReader().readText()

    // ------------------------------------------------------- server frames

    @Test
    fun `decodes hello`() {
        val frame = SentinelJson.decodeFromString<ServerFrame>(sample("hello")) as HelloFrame
        assertEquals(1, frame.v)
        assertEquals(1L, frame.seq)
        assertEquals("sentinel-stub-01", frame.data.server_id)
        assertEquals(42L, frame.data.last_event_seq)
        assertEquals(200, frame.data.resync_buffer_max)
    }

    @Test
    fun `decodes event`() {
        val frame = SentinelJson.decodeFromString<ServerFrame>(sample("event")) as EventFrame
        val d = frame.data

        assertEquals("0199612e-b400-7000-8000-00000000c0de", d.event_id)
        assertEquals("ssh.bruteforce", d.signature)
        assertEquals(Severity.HIGH, d.severity)
        assertEquals(0.91, d.confidence, 0.0001)
        assertFalse(d.reviewed)

        // entities are structured and must never be regexed out of prose
        assertEquals(listOf("185.220.101.34"), d.entities.src_ips)
        assertEquals(listOf("bastion"), d.entities.dst_hosts)
        assertEquals(listOf(22), d.entities.ports)

        assertEquals("sshd", d.source.collector)
        assertEquals(47, d.source.raw_count)

        val action = assertNotNull(d.action).let { d.action!! }
        assertEquals(Playbook.BLOCK_IP, action.playbook)
        assertEquals(ActionStatus.PENDING_APPROVAL, action.status)
        assertTrue(action.requires_approval)
        assertEquals("apr_0199612eb401", action.approval_id)
        assertEquals("185.220.101.34/32", (action.params["target"]?.toString())?.trim('"'))

        assertEquals(AudioStatus.READY, d.audio?.status)
        assertEquals(false, d.escalation?.escalated)
    }

    @Test
    fun `decodes heartbeat`() {
        val frame = SentinelJson.decodeFromString<ServerFrame>(sample("heartbeat")) as HeartbeatFrame
        assertEquals(10, frame.data.interval_s)
        assertEquals(42L, frame.data.last_event_seq)
        assertEquals(0, frame.data.queue_depth)
        assertEquals(SubsystemStatus.OK, frame.data.pipeline.ollama)
        assertEquals(SubsystemStatus.DEGRADED, frame.data.pipeline.tts)
    }

    @Test
    fun `decodes action_update`() {
        val frame =
            SentinelJson.decodeFromString<ServerFrame>(sample("action_update")) as ActionUpdateFrame
        assertEquals(Playbook.BLOCK_IP, frame.data.playbook)
        assertEquals(ActionStatus.APPROVED, frame.data.status)
        assertEquals("apr_0199612eb401", frame.data.approval_id)
    }

    @Test
    fun `decodes audio_ready`() {
        val frame =
            SentinelJson.decodeFromString<ServerFrame>(sample("audio_ready")) as AudioReadyFrame
        assertEquals("0199612e-b400-7000-8000-00000000c0de", frame.data.event_id)
        assertEquals(AudioStatus.READY, frame.data.audio.status)
        assertEquals("audio/mpeg", frame.data.audio.mime)
    }

    // ------------------------------------------------------- client frames

    @Test
    fun `encodes approve_action matching the golden sample`() {
        val golden = Json.parseToJsonElement(sample("approve_action")) as JsonObject
        val goldenData = golden["data"] as JsonObject

        val frame = ApproveActionFrame(
            seq = 7,
            ts = "2026-09-19T12:00:00.000Z",
            data = ApproveActionData(
                approval_id = "apr_0199612eb401",
                event_id = "0199612e-b400-7000-8000-00000000c0de",
                decision = Decision.APPROVED,
                biometric = true,
            ),
        )
        val encoded = Json.parseToJsonElement(
            SentinelJson.encodeToString<ClientFrame>(frame)
        ) as JsonObject

        assertEquals(golden["type"], encoded["type"])
        assertEquals(goldenData, encoded["data"])
    }

    @Test
    fun `decodes ack and resync round trip`() {
        val ack = SentinelJson.decodeFromString<ClientFrame>(sample("ack")) as AckFrame
        assertEquals(43L, ack.data.seq)

        val resync = SentinelJson.decodeFromString<ClientFrame>(sample("resync")) as ResyncFrame
        assertEquals(40L, resync.data.since_seq)
    }

    // ------------------------------------------------------- contract rules

    @Test
    fun `unknown keys do not break decoding`() {
        // A field added Saturday night must not require reinstalling the app.
        val mutated = sample("heartbeat").replace(
            "\"queue_depth\": 0",
            "\"queue_depth\": 0, \"a_field_shan_added_at_2am\": {\"nested\": true}",
        )
        val frame = SentinelJson.decodeFromString<ServerFrame>(mutated) as HeartbeatFrame
        assertEquals(0, frame.data.queue_depth)
    }

    @Test
    fun `info severity is never spoken`() {
        assertFalse(Severity.INFO.isSpoken)
        assertTrue(Severity.LOW.isSpoken)
        assertTrue(Severity.HIGH.isSpoken)
        assertTrue(Severity.CRITICAL.isSpoken)
    }
}
