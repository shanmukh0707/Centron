package com.centron.sentinel.engine

import com.centron.sentinel.net.EngineConfig
import com.centron.sentinel.net.PinnedTrust
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * The parts of the engine that exist.
 *
 * Only [chat] is wired. Everything else still reports unreachable, because
 * probing a device over SSH, reading alert history and reverting an action all
 * need engine features that are not built — and a stub returning a plausible
 * answer for those would be worse than one that admits it.
 *
 * Chat is HTTP, not a new frame type. The WebSocket contract belongs to the
 * engine's author and is not mine to extend; chat is request/response anyway
 * and has no business sharing the event stream's ordering guarantees.
 *
 * Same client rules as the socket and the audio fetch: pinned certificate,
 * bearer token. There is no second, laxer way into the engine.
 */
class HttpEngine(
    private val configProvider: () -> EngineConfig,
) : CentronEngine {

    private val json = Json { ignoreUnknownKeys = true }

    private fun client(config: EngineConfig): OkHttpClient =
        PinnedTrust.apply(
            OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                // Generous: a cloud model answering a real question is slower
                // than any other call this app makes.
                .readTimeout(45, TimeUnit.SECONDS),
            config,
        ).build()

    override suspend fun chat(
        scope: ChatScope,
        history: List<ChatTurn>,
        message: String,
    ): EngineResult<ChatTurn> = withContext(Dispatchers.IO) {
        val config = configProvider()
        if (config.certSha256.isBlank() && config.useTls) {
            return@withContext EngineResult.Unreachable("Not paired with an engine yet.")
        }

        val payload = buildJsonObject {
            put("scope", JsonPrimitive(scope.label))
            put("message", JsonPrimitive(message))
            put("history", buildJsonArray {
                // SYSTEM turns are the app talking to itself (seeded context
                // banners). Sending them as user text would confuse the model
                // about who said what.
                history.filter { it.speaker != Speaker.SYSTEM }.forEach { turn ->
                    add(buildJsonObject {
                        put(
                            "role",
                            JsonPrimitive(if (turn.speaker == Speaker.CENTRON) "assistant" else "user"),
                        )
                        put("text", JsonPrimitive(turn.text))
                    })
                }
            })
        }

        val request = Request.Builder()
            .url("${config.httpBase}/chat")
            .apply { if (config.token.isNotBlank()) header("Authorization", "Bearer ${config.token}") }
            .post(payload.toString().toRequestBody(JSON_MEDIA))
            .build()

        try {
            client(config).newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    // 503 is the engine telling us it has no API key. That is
                    // a configuration fact the operator can act on, so say it
                    // plainly rather than reporting a generic failure.
                    return@withContext when (response.code) {
                        503 -> EngineResult.Unreachable(
                            "The engine has no Anthropic API key set, so it cannot answer."
                        )
                        401 -> EngineResult.Refused("This phone is not authorised. Re-pair with the engine.")
                        else -> EngineResult.Unreachable("Engine returned ${response.code}.")
                    }
                }
                val reply = runCatching {
                    json.parseToJsonElement(body).jsonObject["reply"]?.jsonPrimitive?.content
                }.getOrNull()

                if (reply.isNullOrBlank()) {
                    EngineResult.Unreachable("The engine replied with nothing.")
                } else {
                    EngineResult.Ok(
                        ChatTurn(speaker = Speaker.CENTRON, text = reply, viaCloudModel = "Claude")
                    )
                }
            }
        } catch (e: PinnedTrust.MissingPin) {
            EngineResult.Unreachable("Not paired with an engine yet.")
        } catch (e: Exception) {
            EngineResult.Unreachable("Could not reach the engine: ${e.message ?: "no route"}")
        }
    }

    // ---------------------------------------------------- not built yet

    private val notBuilt = UnreachableEngine()

    override suspend fun probeDevice(host: String, user: String, port: Int) =
        notBuilt.probeDevice(host, user, port)

    override suspend fun alertHistory(sinceMillis: Long) =
        notBuilt.alertHistory(sinceMillis)

    override suspend fun revertAction(approvalId: String) =
        notBuilt.revertAction(approvalId)

    private companion object {
        val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
