package com.centron.sentinel.settings

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

    /** Returns false if the code is malformed. */
    fun pair(code: String): Boolean {
        val parsed = EngineConfig.fromPairingCode(code) ?: return false
        _engineConfig.value = parsed
        _sites.value = listOf(
            Site(parsed.host, "serverpi", "${parsed.host}:${parsed.port}")
        ) + _sites.value.filterNot { it.id == parsed.host }
        _currentSiteId.value = parsed.host
        return true
    }

    fun useLocalStub() {
        _engineConfig.value = EngineConfig.LOCAL_STUB
    }

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
