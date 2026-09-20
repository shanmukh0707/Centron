package com.centron.sentinel.engine

import kotlinx.coroutines.delay

/**
 * The Centron Engine client.
 *
 * Everything the phone cannot decide for itself lives behind this: reaching a
 * device over SSH, running a model, holding chat history, reverting an action.
 * The engine is the only party with credentials, so the phone asks and renders.
 *
 * Nothing here is wired to a real engine yet, because the engine does not
 * exist — the only server in this project is the Sentinel stub, which speaks
 * the v1 event contract and nothing else. The implementations below fail
 * honestly rather than returning plausible fakes, so a screen that depends on
 * the engine looks unfinished instead of looking finished and lying.
 */

sealed interface EngineResult<out T> {
    data class Ok<T>(val value: T) : EngineResult<T>
    data class Unreachable(val detail: String) : EngineResult<Nothing>
    data class Refused(val reason: String) : EngineResult<Nothing>
}

data class ProbeReport(
    val reachable: Boolean,
    val hostKeyFingerprint: String?,
    val osDescription: String?,
    val detectedCapabilities: Set<String>,
    val note: String,
)

interface CentronEngine {

    /**
     * Called before a device is added. The engine attempts the SSH connection
     * itself and reports what it found — reachability, host key, and which
     * capabilities it could actually verify.
     */
    suspend fun probeDevice(host: String, user: String, port: Int): EngineResult<ProbeReport>

    // NOTE: there is deliberately no diagnose(eventId) here. Contract 1
    // already carries the Claude verdict in escalation.verdict, and a late
    // verdict re-emits the same event_id with a fresh seq. Adding an endpoint
    // would be a second source of truth for the same field.

    /** One turn of conversation. [scopeId] pins the model to a single device
     *  or container; the engine enforces it, the prompt does not. */
    suspend fun chat(scope: ChatScope, history: List<ChatTurn>, message: String): EngineResult<ChatTurn>

    /** Alerts older than what the phone still holds in memory. */
    suspend fun alertHistory(sinceMillis: Long): EngineResult<List<HistoricalAlert>>

    /** Undo a previously executed action. Only ever offered for actions the
     *  engine marked reversible when it executed them. */
    suspend fun revertAction(approvalId: String): EngineResult<String>
}

data class HistoricalAlert(
    val eventId: String,
    val severity: String,
    val title: String,
    val summary: String,
    val atMillis: Long,
    val actionTaken: String?,
    val approvalId: String?,
    val reversible: Boolean,
)

/**
 * Stand-in used until an engine exists.
 *
 * Every call reports unreachable. The short delay is not theatre — it keeps
 * the UI's loading states on a realistic path so they are exercised rather
 * than skipped, which is how spinner bugs survive to demo day.
 */
class UnreachableEngine : CentronEngine {

    private suspend fun <T> miss(what: String): EngineResult<T> {
        delay(600)
        return EngineResult.Unreachable(
            "No Centron Engine is paired. $what needs the engine, which is not built yet."
        )
    }

    override suspend fun probeDevice(host: String, user: String, port: Int) =
        miss<ProbeReport>("Verifying $host")

    override suspend fun chat(scope: ChatScope, history: List<ChatTurn>, message: String) =
        miss<ChatTurn>("Chat")

    override suspend fun alertHistory(sinceMillis: Long) =
        miss<List<HistoricalAlert>>("Alert history")

    override suspend fun revertAction(approvalId: String) =
        miss<String>("Reverting an action")
}

object Engines {
    /**
     * Chat reaches the engine for real. Device probing, alert history and
     * revert still report unreachable — see [HttpEngine].
     */
    val current: CentronEngine = HttpEngine {
        com.centron.sentinel.settings.AppSettings.engineConfig.value
    }
}
