package com.centron.sentinel.net

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Connection colour is a pure function of heartbeat gap. Pinning the
 * thresholds here because they are a contract value, not a UI preference.
 */
class ConnectionStateTest {

    @Test
    fun `fresh heartbeat is green`() {
        assertEquals(ConnectionState.GREEN, ConnectionState.fromGap(0))
        assertEquals(ConnectionState.GREEN, ConnectionState.fromGap(14_999))
    }

    @Test
    fun `amber at fifteen seconds`() {
        assertEquals(ConnectionState.AMBER, ConnectionState.fromGap(15_000))
        assertEquals(ConnectionState.AMBER, ConnectionState.fromGap(24_999))
    }

    @Test
    fun `red at twenty five seconds`() {
        assertEquals(ConnectionState.RED, ConnectionState.fromGap(25_000))
        assertEquals(ConnectionState.RED, ConnectionState.fromGap(600_000))
    }
}
