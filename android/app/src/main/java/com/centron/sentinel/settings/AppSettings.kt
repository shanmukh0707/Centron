package com.centron.sentinel.settings

import android.content.Context
import android.content.SharedPreferences
import com.centron.sentinel.net.EngineConfig
import com.centron.sentinel.ui.theme.ThemeMode
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * A site is one Centron Engine — one homelab.
 *
 * UniFi's model, and the right one here: each site owns its own devices,
 * credentials and policy, and switching sites switches everything. There is no
 * cross-site view, deliberately, because an action taken against the wrong
 * homelab is exactly the kind of mistake that is unrecoverable.
 */
data class Site(
    val id: String,
    val name: String,
    val engineAddress: String,
    val paired: Boolean = true,
)

/**
 * Process-wide preferences.
 *
 * In memory only for now. These want to be on disk (DataStore) before anyone
 * relies on them surviving a restart, and the site list wants to come from
 * whatever holds pairings. Kept behind a single object so that move is
 * contained.
 */
object AppSettings {

    private val _themeMode = MutableStateFlow(ThemeMode.DARK)
    val themeMode: StateFlow<ThemeMode> = _themeMode.asStateFlow()

    /**
     * Which engine the service connects to.
     *
     * Defaults to the local stub over the USB tunnel so development keeps
     * working with no pairing. Pasting a pairing code switches it to serverpi
     * with a real pin and token.
     *
     * This belongs in DataStore before anyone relies on it surviving a
     * restart — and the token in particular should move to EncryptedSharedPrefs
     * or a Keystore-wrapped blob rather than sitting in process memory.
     */
    private val _engineConfig = MutableStateFlow(EngineConfig.LOCAL_STUB)
    val engineConfig: StateFlow<EngineConfig> = _engineConfig.asStateFlow()

    private var prefs: SharedPreferences? = null

    /**
     * Restore the pairing from disk.
     *
     * Without this the pairing lives only in process memory, so every app
     * restart — including one Android does on its own to reclaim memory —
     * silently drops the engine and lands the operator back on the pairing
     * screen. Mid-demo that is indistinguishable from the engine being down.
     *
     * The token is a bearer secret sitting in app-private plaintext. That is
     * weaker than the Keystore-wrapped blob this wants to be, and is the
     * known gap to close after submission.
     */
    fun attach(context: Context) {
        if (prefs != null) return
        val p = context.applicationContext
            .getSharedPreferences("centron.engine", Context.MODE_PRIVATE)
        prefs = p

        val host = p.getString(KEY_HOST, null) ?: return
        _engineConfig.value = EngineConfig(
            host = host,
            port = p.getInt(KEY_PORT, 8765),
            certSha256 = p.getString(KEY_PIN, "").orEmpty(),
            token = p.getString(KEY_TOKEN, "").orEmpty(),
            useTls = p.getBoolean(KEY_TLS, true),
        )
        _sites.value = listOf(Site(host, "serverpi", "$host:${p.getInt(KEY_PORT, 8765)}"))
        _currentSiteId.value = host
    }

    private fun persist(config: EngineConfig) {
        prefs?.edit()
            ?.putString(KEY_HOST, config.host)
            ?.putInt(KEY_PORT, config.port)
            ?.putString(KEY_PIN, config.certSha256)
            ?.putString(KEY_TOKEN, config.token)
            ?.putBoolean(KEY_TLS, config.useTls)
            ?.apply()
    }

    /** Returns false if the code is malformed. */
    fun pair(code: String): Boolean {
        val parsed = EngineConfig.fromPairingCode(code) ?: return false
        _engineConfig.value = parsed
        persist(parsed)
        _sites.value = listOf(
            Site(parsed.host, "serverpi", "${parsed.host}:${parsed.port}")
        ) + _sites.value.filterNot { it.id == parsed.host }
        _currentSiteId.value = parsed.host
        return true
    }

    fun useLocalStub() {
        _engineConfig.value = EngineConfig.LOCAL_STUB
        // Clear rather than persist: a stored stub config would quietly win
        // over a real pairing on the next launch.
        prefs?.edit()?.clear()?.apply()
    }

    private const val KEY_HOST = "host"
    private const val KEY_PORT = "port"
    private const val KEY_PIN = "pin"
    private const val KEY_TOKEN = "token"
    private const val KEY_TLS = "tls"

    private val _sites = MutableStateFlow(
        listOf(
            // PLACEHOLDER. Real sites arrive through engine pairing.
            Site("home", "Home lab", "100.110.30.122"),
            Site("bench", "Bench", "192.168.1.7"),
        )
    )
    val sites: StateFlow<List<Site>> = _sites.asStateFlow()

    private val _currentSiteId = MutableStateFlow("home")
    val currentSiteId: StateFlow<String> = _currentSiteId.asStateFlow()

    /**
     * Speak alerts aloud. On by default now that the queue and the backend
     * renderer both exist -- an ambient voice SOC that starts muted is just a
     * notification app until someone finds the switch.
     */
    private val _speakAlerts = MutableStateFlow(true)
    val speakAlerts: StateFlow<Boolean> = _speakAlerts.asStateFlow()

    /** Require biometric re-auth for every action, not just destructive ones. */
    private val _strictApproval = MutableStateFlow(false)
    val strictApproval: StateFlow<Boolean> = _strictApproval.asStateFlow()

    /** How long the engine should retain alert history, in days. */
    private val _retentionDays = MutableStateFlow(30)
    val retentionDays: StateFlow<Int> = _retentionDays.asStateFlow()

    fun current(): Site? = _sites.value.firstOrNull { it.id == _currentSiteId.value }

    fun setTheme(mode: ThemeMode) { _themeMode.value = mode }
    fun selectSite(id: String) { _currentSiteId.value = id }
    fun setSpeakAlerts(on: Boolean) { _speakAlerts.value = on }
    fun setStrictApproval(on: Boolean) { _strictApproval.value = on }
    fun setRetentionDays(days: Int) { _retentionDays.value = days }

    /**
     * Drop a paired engine.
     *
     * Destructive: the phone loses the pairing and re-pairing needs physical
     * access to the engine to read a fresh code. The UI must confirm before
     * calling this.
     */
    fun forgetSite(id: String) {
        _sites.value = _sites.value.filterNot { it.id == id }
        if (_currentSiteId.value == id) {
            _currentSiteId.value = _sites.value.firstOrNull()?.id.orEmpty()
        }
    }
}
