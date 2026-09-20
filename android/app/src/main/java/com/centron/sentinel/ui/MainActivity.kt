package com.centron.sentinel.ui

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.InfiniteRepeatableSpec
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.viewmodel.compose.viewModel
import com.centron.sentinel.contract.Decision
import com.centron.sentinel.data.EventEntity
import com.centron.sentinel.device.Device
import com.centron.sentinel.device.DeviceRegistry
import com.centron.sentinel.device.TelemetryHistory
import com.centron.sentinel.engine.ChatScope
import com.centron.sentinel.engine.ChatTurn
import com.centron.sentinel.engine.EngineResult
import com.centron.sentinel.engine.Engines
import com.centron.sentinel.engine.Speaker
import com.centron.sentinel.net.ActiveClient
import com.centron.sentinel.net.ConnectionState
import com.centron.sentinel.notify.AlertCenter
import com.centron.sentinel.security.BiometricGate
import com.centron.sentinel.service.SentinelService
import com.centron.sentinel.settings.AppSettings
import com.centron.sentinel.ui.action.ApprovalScreen
import com.centron.sentinel.ui.auth.AuthScreen
import com.centron.sentinel.ui.auth.AuthState
import com.centron.sentinel.ui.auth.UnconfiguredAuthGateway
import com.centron.sentinel.ui.chat.ChatController
import com.centron.sentinel.ui.chat.ChatScreen
import com.centron.sentinel.ui.home.AddDeviceScreen
import com.centron.sentinel.ui.home.DeviceDetailScreen
import com.centron.sentinel.ui.home.DeviceSettingsSheet
import com.centron.sentinel.ui.home.HomeScreen
import com.centron.sentinel.ui.pairing.PairingScreen
import com.centron.sentinel.ui.settings.SettingsScreen
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.SentinelTheme
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * FragmentActivity, not ComponentActivity — BiometricPrompt requires one.
 * That is the only reason; nothing else here uses fragments.
 */
class MainActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SentinelService.start(this)
        setContent {
            val mode by AppSettings.themeMode.collectAsState()
            SentinelTheme(mode) { Root(this) }
        }
    }
}

@Composable
private fun Root(activity: FragmentActivity) {
    var route by remember { mutableStateOf<Route>(Route.Auth) }
    var authState by remember { mutableStateOf<AuthState>(AuthState.Idle) }
    val gateway = remember { UnconfiguredAuthGateway() }
    val scope = rememberCoroutineScope()

    /*
     * targetSdk 35 means Android 15+ draws edge to edge whether we ask or not,
     * so the status bar clock will sit on top of the top bar unless we inset.
     * Background stays full bleed; only content is padded.
     */
    Box(
        Modifier
            .fillMaxSize()
            .background(Ink.Base)
            .windowInsetsPadding(WindowInsets.safeDrawing),
    ) {
        if (route is Route.Auth) {
            AuthScreen(
                state = authState,
                onProvider = { provider ->
                    authState = AuthState.Working(provider)
                    scope.launch {
                        val result = gateway.signIn(provider)
                        authState = result
                        if (result is AuthState.Authenticated) route = Route.Home
                    }
                },
                onEmail = {
                    authState = AuthState.Working(null)
                    scope.launch { authState = gateway.continueWithEmail("") }
                },
                onSkip = { route = Route.Pairing },
            )
        } else if (route is Route.Pairing) {
            PairingScreen(
                current = AppSettings.engineConfig.collectAsState().value,
                onPair = { code ->
                    val ok = AppSettings.pair(code)
                    if (ok) {
                        SentinelService.start(activity)
                        route = Route.Home
                    }
                    ok
                },
                onUseLocalStub = {
                    AppSettings.useLocalStub()
                    SentinelService.start(activity)
                    route = Route.Home
                },
            )
        } else {
            // The gate sits between sign-in and everything else deliberately:
            // you cannot reach a screen that implies you are being watched
            // until the app can actually reach you.
            NotificationGate {
                // Named: the trailing lambda would otherwise bind to `vm`.
                Shell(activity = activity, route = route, onRoute = { route = it })
            }
        }
    }
}

@Composable
private fun Shell(
    activity: FragmentActivity,
    route: Route,
    onRoute: (Route) -> Unit,
    vm: SentinelViewModel = viewModel(),
) {
    val connection by vm.connection.collectAsState()
    val events by vm.events.collectAsState()
    val devices by DeviceRegistry.devices.collectAsState()
    val telemetryHistory by DeviceRegistry.history.collectAsState()
    val alerts by AlertCenter.alerts.collectAsState()
    val unread by AlertCenter.unread.collectAsState()
    val autoOpen by AlertCenter.autoOpen.collectAsState()
    val sites by AppSettings.sites.collectAsState()
    val currentSiteId by AppSettings.currentSiteId.collectAsState()
    val themeMode by AppSettings.themeMode.collectAsState()
    val speakAlerts by AppSettings.speakAlerts.collectAsState()
    val strictApproval by AppSettings.strictApproval.collectAsState()
    val retentionDays by AppSettings.retentionDays.collectAsState()
    val chatSessions by ChatController.sessions.collectAsState()
    val chatPending by ChatController.pending.collectAsState()

    val scope = rememberCoroutineScope()
    var notice by remember { mutableStateOf<String?>(null) }
    var panelOpen by remember { mutableStateOf(false) }
    var sitePanelOpen by remember { mutableStateOf(false) }

    // Add-device probe state.
    var probing by remember { mutableStateOf(false) }
    var probeMessage by remember { mutableStateOf<String?>(null) }
    var probeFailed by remember { mutableStateOf(false) }

    var loadingHistory by remember { mutableStateOf(false) }

    LaunchedEffect(autoOpen) {
        if (autoOpen) {
            panelOpen = true
            AlertCenter.consumeAutoOpen()
        }
    }

    // ---------------------------------------------------- full-screen routes

    when (route) {
        is Route.AddDevice -> {
            AddDeviceScreen(
                probing = probing,
                probeMessage = probeMessage,
                probeFailed = probeFailed,
                onCancel = {
                    probeMessage = null; probeFailed = false
                    onRoute(Route.Home)
                },
                onAdd = { name, type, role, host, user, port, caps ->
                    scope.launch {
                        probing = true
                        probeMessage = null
                        // Verify before adding: the engine makes the SSH
                        // connection itself and reports what it found.
                        when (val result = Engines.current.probeDevice(host, user, port)) {
                            is EngineResult.Ok -> {
                                probing = false
                                DeviceRegistry.add(name, type, role, host, user, port, caps)
                                notice = "$name verified and added."
                                probeMessage = null
                                onRoute(Route.Home)
                            }
                            is EngineResult.Unreachable -> {
                                probing = false; probeFailed = true
                                probeMessage = result.detail
                            }
                            is EngineResult.Refused -> {
                                probing = false; probeFailed = true
                                probeMessage = result.reason
                            }
                        }
                    }
                },
            )
            return
        }

        is Route.Chat -> {
            val chatScope = route.scope
            ChatScreen(
                scope = chatScope,
                turns = chatSessions[chatKey(chatScope)].orEmpty(),
                pending = chatPending == chatKey(chatScope),
                onSend = { message -> scope.launch { ChatController.send(chatScope, message) } },
                onBack = { onRoute(Route.Home) },
            )
            return
        }

        is Route.Settings -> {
            SettingsScreen(
                site = sites.firstOrNull { it.id == currentSiteId },
                themeMode = themeMode,
                speakAlerts = speakAlerts,
                strictApproval = strictApproval,
                retentionDays = retentionDays,
                onBack = { onRoute(Route.Home) },
                onTheme = AppSettings::setTheme,
                onSpeakAlerts = AppSettings::setSpeakAlerts,
                onStrictApproval = AppSettings::setStrictApproval,
                onRetention = AppSettings::setRetentionDays,
                onChangePassword = {
                    notice = "Changing the signing key needs the engine to re-enrol this phone."
                },
                onRepair = { onRoute(Route.Pairing) },
                onForgetSite = {
                    AppSettings.forgetSite(currentSiteId)
                    notice = "Site forgotten on this phone."
                    onRoute(Route.Home)
                },
            )
            return
        }

        is Route.DeviceDetail -> {
            val device = devices.firstOrNull { it.id == route.deviceId }
            if (device == null) {
                onRoute(Route.Home)
                return
            }
            DeviceDetailScreen(
                device = device,
                history = telemetryHistory[device.id] ?: TelemetryHistory(),
                onBack = { onRoute(Route.Home) },
                onOpenSettings = { onRoute(Route.DeviceSettings(device.id)) },
                onTogglePower = { on ->
                    scope.launch { notice = changePower(activity, device, on) }
                },
                onToggleContainer = { container, run ->
                    DeviceRegistry.setContainerRunning(device.id, container.name, run)
                    notice = "${container.name} ${if (run) "start" else "stop"} requested."
                },
                onAskAboutDevice = {
                    onRoute(Route.Chat(ChatScope.DeviceScope(device.id, device.name)))
                },
                onAskAboutContainer = { container ->
                    onRoute(
                        Route.Chat(
                            ChatScope.ContainerScope(device.id, device.name, container.name)
                        )
                    )
                },
            )
            return
        }

        is Route.DeviceSettings -> {
            val device = devices.firstOrNull { it.id == route.deviceId }
            if (device == null) {
                onRoute(Route.Home)
                return
            }
            DeviceSettingsSheet(
                device = device,
                onDismiss = { onRoute(Route.DeviceDetail(device.id)) },
                onSave = { name, host, role, stats, powerEnabled, powerBiometric ->
                    DeviceRegistry.updateSettings(
                        id = device.id,
                        name = name,
                        host = host,
                        role = role,
                        visibleStats = stats,
                        powerControlsEnabled = powerEnabled,
                        powerRequiresBiometric = powerBiometric,
                    )
                    notice = "${name.trim().ifBlank { device.name }} updated."
                    onRoute(Route.DeviceDetail(device.id))
                },
                onForget = {
                    DeviceRegistry.remove(device.id)
                    notice = "${device.name} removed from this phone."
                    onRoute(Route.Home)
                },
            )
            return
        }

        is Route.Approval -> {
            val event = events.firstOrNull { it.event_id == route.eventId }
            if (event == null) {
                onRoute(Route.Alerts)
                return
            }

            // No fetch. escalation.verdict is already on the event, and a
            // late verdict re-emits the event with a new seq, which Room
            // upserts — so this screen re-renders on its own.
            ApprovalScreen(
                event = event,
                onBack = { onRoute(Route.Alerts) },
                onApprove = {
                    scope.launch {
                        notice = approve(activity, event, Decision.APPROVED)
                        onRoute(Route.Alerts)
                    }
                },
                onDeny = {
                    scope.launch {
                        notice = approve(activity, event, Decision.DENIED)
                        onRoute(Route.Alerts)
                    }
                },
                onAskForBetter = {
                    val chatScope = ChatScope.EventScope(event.event_id, event.title)
                    // Seed the conversation with what the operator already
                    // saw, so the first question does not need re-explaining.
                    ChatController.seed(
                        chatScope,
                        ChatTurn(
                            Speaker.SYSTEM,
                            "Discussing: ${event.title}. Proposed action: " +
                                "${event.action_playbook.orEmpty().replace('_', ' ')}.",
                        ),
                    )
                    onRoute(Route.Chat(chatScope))
                },
            )
            return
        }

        else -> Unit
    }

    // ---------------------------------------------------- shell routes

    Column(
        Modifier
            .fillMaxSize()
            .background(Ink.Base),
    ) {
        TopBar(
            state = connection,
            siteName = sites.firstOrNull { it.id == currentSiteId }?.name,
            sitePanelOpen = sitePanelOpen,
            unread = unread,
            panelOpen = panelOpen,
            showBack = route is Route.Alerts,
            onBack = { onRoute(Route.Home) },
            onSite = {
                sitePanelOpen = !sitePanelOpen
                if (sitePanelOpen) panelOpen = false
            },
            onBell = {
                panelOpen = !panelOpen
                if (panelOpen) {
                    AlertCenter.markAllRead()
                    sitePanelOpen = false
                }
            },
        )

        SitePanel(
            visible = sitePanelOpen,
            sites = sites,
            currentId = currentSiteId,
            onSelect = {
                AppSettings.selectSite(it.id)
                sitePanelOpen = false
                notice = "Switched to ${it.name}."
            },
            onManage = {
                sitePanelOpen = false
                onRoute(Route.Settings)
            },
        )

        AlertPanel(
            visible = panelOpen,
            alerts = alerts,
            onDismiss = { panelOpen = false },
            onMarkRead = { AlertCenter.markAllRead() },
            onSeeAll = {
                panelOpen = false
                onRoute(Route.Alerts)
            },
        )

        AnimatedVisibility(
            visible = notice != null,
            enter = fadeIn(tween(180)) + slideInVertically(tween(220)) { -it },
            exit = fadeOut(tween(140)),
        ) {
            NoticeBar(notice.orEmpty()) { notice = null }
        }

        if (route is Route.Alerts) {
            EventList(
                events = events,
                loadingHistory = loadingHistory,
                onOpenApproval = { onRoute(Route.Approval(it.event_id)) },
                onRevert = { event ->
                    scope.launch {
                        val approvalId = event.approval_id
                        notice = if (approvalId == null) {
                            "No approval id on this event."
                        } else {
                            when (val r = Engines.current.revertAction(approvalId)) {
                                is EngineResult.Ok -> r.value
                                is EngineResult.Unreachable -> r.detail
                                is EngineResult.Refused -> r.reason
                            }
                        }
                    }
                },
                onLoadHistory = {
                    scope.launch {
                        loadingHistory = true
                        val since = System.currentTimeMillis() - retentionDays * 86_400_000L
                        notice = when (val r = Engines.current.alertHistory(since)) {
                            is EngineResult.Ok -> "Loaded ${r.value.size} older alerts."
                            is EngineResult.Unreachable -> r.detail
                            is EngineResult.Refused -> r.reason
                        }
                        loadingHistory = false
                    }
                },
            )
        } else {
            HomeScreen(
                devices = devices,
                onAddDevice = {
                    probeMessage = null; probeFailed = false
                    onRoute(Route.AddDevice)
                },
                onOpenDevice = { onRoute(Route.DeviceDetail(it.id)) },
                onTogglePower = { device, on ->
                    scope.launch { notice = changePower(activity, device, on) }
                },
                onOpenChat = { onRoute(Route.Chat(ChatScope.Fleet)) },
            )
        }
    }
}

private fun chatKey(scope: ChatScope): String = when (scope) {
    is ChatScope.Fleet -> "fleet"
    is ChatScope.DeviceScope -> "device:${scope.deviceId}"
    is ChatScope.ContainerScope -> "container:${scope.deviceId}:${scope.container}"
    is ChatScope.EventScope -> "event:${scope.eventId}"
}

// ---------------------------------------------------------------- events

@Composable
private fun EventList(
    events: List<EventEntity>,
    loadingHistory: Boolean,
    onOpenApproval: (EventEntity) -> Unit,
    onRevert: (EventEntity) -> Unit,
    onLoadHistory: () -> Unit,
) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = Space.Gutter, end = Space.Gutter,
            top = Space.Md, bottom = Space.Xxl,
        ),
        verticalArrangement = Arrangement.spacedBy(Space.Sm),
    ) {
        items(events, key = { it.event_id }) { event ->
            EventCard(
                event = event,
                onOpenApproval = { onOpenApproval(event) },
                onRevert = { onRevert(event) },
            )
        }

        item {
            Spacer(Modifier.height(Space.Md))
            Box(
                Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(12.dp))
                    .background(Ink.Surface)
                    .border(Space.Hair, Ink.Hairline, RoundedCornerShape(12.dp))
                    .clickable(enabled = !loadingHistory, onClick = onLoadHistory)
                    .padding(vertical = 14.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    if (loadingHistory) "Loading…" else "Load older alerts from engine",
                    style = MaterialTheme.typography.labelLarge,
                    color = Ink.Secondary,
                )
            }
        }
    }
}

/**
 * Runs the gate, then sends the decision.
 *
 * Order matters: authenticate first, send second. Never optimistically mark an
 * action approved and reconcile later — the whole point of the gate is that
 * nothing leaves the phone without a fresh unlock behind it.
 */
private suspend fun approve(
    activity: FragmentActivity,
    event: EventEntity,
    decision: Decision,
): String {
    val approvalId = event.approval_id ?: return "No approval id on this event."
    val client = ActiveClient.get() ?: return "Not connected. Reconnect before approving."

    val verb = if (decision == Decision.APPROVED) "Approve" else "Deny"
    val payload = BiometricGate.payloadFor(
        approvalId = approvalId,
        eventId = event.event_id,
        decision = decision.name.lowercase(),
        clientTs = nowIso(),
    )

    return when (val result = BiometricGate.authorize(
        activity = activity,
        title = "$verb ${event.action_playbook.orEmpty().replace('_', ' ')}",
        subtitle = event.title,
        payload = payload,
    )) {
        is BiometricGate.Result.Signed -> {
            client.approve(approvalId, event.event_id, decision, biometric = true)
            "${verb}d — signed on device."
        }
        BiometricGate.Result.AuthenticatedUnsigned -> {
            client.approve(approvalId, event.event_id, decision, biometric = true)
            "${verb}d — unlocked, unsigned on this OS version."
        }
        BiometricGate.Result.Cancelled -> "Cancelled. Nothing sent."
        is BiometricGate.Result.Unavailable -> result.reason
        is BiometricGate.Result.Failed -> "Authentication failed: ${result.reason}"
    }
}

/**
 * Runs the gate, then changes device power.
 *
 * Same order as [approve], for the same reason: authenticate first, act
 * second. Powering off the box the engine runs on is the one action in this
 * app you cannot undo from this app — the thing you would use to turn it back
 * on went down with it — so it is gated by default.
 *
 * Wake is gated too. It is the lesser risk, but a single toggle that means
 * "prompt in one direction and not the other" is a control people misread, and
 * the cost of being wrong is a machine booting in an empty house.
 */
private suspend fun changePower(
    activity: FragmentActivity,
    device: Device,
    on: Boolean,
): String {
    if (!device.showsPower()) {
        return "Power controls are switched off for ${device.name}."
    }

    val verb = if (on) "Power on" else "Power off"

    // The per-device setting can waive the prompt; the global strict setting
    // cannot be waived by it. Strict means strict.
    val gated = device.powerRequiresBiometric || AppSettings.strictApproval.value
    if (!gated) {
        DeviceRegistry.setPower(device.id, on)
        return "${device.name}: ${verb.lowercase()} requested."
    }

    val payload = BiometricGate.powerPayloadFor(
        deviceId = device.id,
        host = device.host,
        action = if (on) "power_on" else "power_off",
        clientTs = nowIso(),
    )

    return when (val result = BiometricGate.authorize(
        activity = activity,
        title = "$verb ${device.name}",
        subtitle = if (on) device.host else "${device.host} goes off the network",
        payload = payload,
    )) {
        is BiometricGate.Result.Signed -> {
            DeviceRegistry.setPower(device.id, on)
            "${device.name}: ${verb.lowercase()} requested — signed on device."
        }
        BiometricGate.Result.AuthenticatedUnsigned -> {
            DeviceRegistry.setPower(device.id, on)
            "${device.name}: ${verb.lowercase()} requested — unlocked, unsigned on this OS version."
        }
        BiometricGate.Result.Cancelled -> "Cancelled. ${device.name} untouched."
        is BiometricGate.Result.Unavailable -> result.reason
        is BiometricGate.Result.Failed -> "Authentication failed: ${result.reason}"
    }
}

// ---------------------------------------------------------------- chrome

@Composable
internal fun TopBar(
    state: ConnectionState,
    siteName: String?,
    sitePanelOpen: Boolean,
    unread: Int,
    panelOpen: Boolean,
    showBack: Boolean = false,
    onBack: () -> Unit = {},
    onSite: () -> Unit,
    onBell: () -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = Space.Sm, end = Space.Sm, top = 4.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterStart) {
            if (showBack) BackChevron(onBack) else ConnectionDot(state)
        }

        SiteChip(
            site = com.centron.sentinel.settings.Site("", siteName.orEmpty(), ""),
            open = sitePanelOpen,
            onClick = onSite,
        )

        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
            BellButton(unread = unread, open = panelOpen, onClick = onBell)
        }
    }
}

@Composable
private fun BackChevron(onBack: () -> Unit) {
    val tint = Ink.Secondary
    Box(
        Modifier.size(36.dp).clip(CircleShape).clickable(onClick = onBack),
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.size(12.dp)) {
            val s = size.minDimension
            val stroke = s * 0.16f
            drawLine(tint, Offset(s * 0.65f, s * 0.15f), Offset(s * 0.30f, s * 0.50f), strokeWidth = stroke)
            drawLine(tint, Offset(s * 0.30f, s * 0.50f), Offset(s * 0.65f, s * 0.85f), strokeWidth = stroke)
        }
    }
}

/**
 * Status compressed to a dot with a tooltip-free label only when it is not
 * green. A healthy connection does not need words; an unhealthy one does.
 */
@Composable
private fun ConnectionDot(state: ConnectionState) {
    val (dot, label) = when (state) {
        ConnectionState.CONNECTING -> Status.Idle to "Connecting"
        ConnectionState.GREEN -> Status.Ok to null
        ConnectionState.AMBER -> Status.Warn to "15s"
        ConnectionState.RED -> Status.Bad to "Offline"
    }

    val restless = state == ConnectionState.AMBER || state == ConnectionState.RED
    val transition = rememberInfiniteTransition(label = "conn")
    val pulse by transition.animateFloat(
        initialValue = if (restless) 0.35f else 1f,
        targetValue = 1f,
        animationSpec = InfiniteRepeatableSpec(
            animation = tween(900, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulse",
    )

    Row(
        Modifier.padding(start = Space.Sm + Space.Xs),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .size(7.dp)
                .alpha(if (restless) pulse else 1f)
                .clip(CircleShape)
                .background(dot),
        )
        label?.let {
            Spacer(Modifier.width(Space.Sm))
            Text(it, style = MaterialTheme.typography.labelSmall, color = dot)
        }
    }
}

@Composable
private fun NoticeBar(message: String, onDismiss: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = Space.Sm)
            .clip(RoundedCornerShape(14.dp))
            .background(Accent.ClaySubtle)
            .clickable(onClick = onDismiss)
            .padding(horizontal = Space.Md, vertical = Space.Md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(message, style = MaterialTheme.typography.bodyMedium, color = Ink.Primary)
        Spacer(Modifier.weight(1f))
        Text("Dismiss", style = MaterialTheme.typography.labelSmall, color = Accent.Clay)
    }
}

// ---------------------------------------------------------------- event card

@Composable
internal fun EventCard(
    event: EventEntity,
    onOpenApproval: () -> Unit = {},
    onRevert: () -> Unit = {},
) {
    val severity = event.severity.lowercase()
    val accent = when (severity) {
        "critical" -> Status.Bad
        "high" -> Status.Warn
        "low" -> Status.Ok
        else -> Ink.Tertiary
    }

    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(16.dp))
            .padding(Space.Md),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            SeverityTag(severity, accent)
            Spacer(Modifier.weight(1f))
            if (!event.reviewed) {
                Text("UNREVIEWED", style = MaterialTheme.typography.labelSmall, color = Ink.Tertiary)
            }
        }

        Spacer(Modifier.height(Space.Sm + Space.Xs))
        Text(event.title, style = MaterialTheme.typography.titleMedium, color = Ink.Primary)
        Spacer(Modifier.height(Space.Xs + 2.dp))
        Text(event.internal_log, style = MaterialTheme.typography.bodyMedium, color = Ink.Secondary)

        if (event.escalated) {
            Spacer(Modifier.height(Space.Md))
            EscalationBlock(event)
        }

        when {
            event.action_status == "pending_approval" && event.requires_approval -> {
                Spacer(Modifier.height(Space.Md))
                PrimaryRow("Review and decide", Accent.Clay, Ink.Base, onOpenApproval)
            }

            // Revert is only offered for things that actually ran. Offering it
            // on a denied or expired action would imply state that never existed.
            event.action_status == "approved" || event.action_status == "auto_executed" -> {
                Spacer(Modifier.height(Space.Md))
                PrimaryRow("Revert this action", Ink.Raised, Ink.Secondary, onRevert)
            }
        }
    }
}

@Composable
private fun PrimaryRow(label: String, fill: Color, content: Color, onClick: () -> Unit) {
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(fill)
            .clickable(onClick = onClick)
            .padding(vertical = 13.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = content)
    }
}

@Composable
private fun SeverityTag(severity: String, accent: Color) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(accent))
        Spacer(Modifier.width(Space.Sm))
        Text(severity.uppercase(), style = MaterialTheme.typography.labelSmall, color = accent)
    }
}

/**
 * The Claude attribution bar.
 *
 * Deliberately quiet: a left rule and secondary type, not a badge. It is
 * provenance, not an achievement — the operator needs to know a cloud model
 * saw this, without it competing with the severity tag.
 */
@Composable
private fun EscalationBlock(event: EventEntity) {
    Row(Modifier.fillMaxWidth()) {
        Box(
            Modifier
                .width(2.dp)
                .height(if (event.escalation_verdict != null) 46.dp else 24.dp)
                .clip(RoundedCornerShape(1.dp))
                .background(Accent.Clay.copy(alpha = 0.55f)),
        )
        Spacer(Modifier.width(Space.Md))
        Column {
            Text(
                buildString {
                    append("Answered by Claude")
                    event.escalation_reason?.let { append(" — ").append(it) }
                },
                style = MaterialTheme.typography.labelSmall,
                color = Accent.Clay,
            )
            event.escalation_verdict?.let {
                Spacer(Modifier.height(Space.Xs))
                Text(it, style = MaterialTheme.typography.bodyMedium, color = Ink.Secondary)
            }
        }
    }
}

private val iso = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
    .apply { timeZone = TimeZone.getTimeZone("UTC") }

private fun nowIso(): String = synchronized(iso) { iso.format(Date()) }
