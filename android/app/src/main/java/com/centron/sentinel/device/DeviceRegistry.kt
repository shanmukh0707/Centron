package com.centron.sentinel.device

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

/**
 * Devices the engine manages.
 *
 * In-memory for now. The engine is the real owner of this list — it is the
 * thing holding SSH credentials and doing the polling — so this will become a
 * projection of engine state rather than a store the phone authors. Kept
 * behind a narrow interface so that swap does not reach into the UI.
 *
 * The seeded devices are marked and must go before this is demoed as real.
 */
object DeviceRegistry {

    private val _devices = MutableStateFlow<List<Device>>(seed())
    val devices: StateFlow<List<Device>> = _devices.asStateFlow()

    /**
     * Telemetry series, keyed by device id.
     *
     * Kept beside the devices rather than on them so that a sample arriving
     * every few seconds does not churn the device list and re-compose every
     * screen that reads it.
     */
    private val _history = MutableStateFlow<Map<String, TelemetryHistory>>(seedHistory())
    val history: StateFlow<Map<String, TelemetryHistory>> = _history.asStateFlow()

    fun historyFor(id: String): TelemetryHistory = _history.value[id] ?: TelemetryHistory()

    fun add(
        name: String,
        type: DeviceType,
        role: String,
        host: String,
        sshUser: String,
        sshPort: Int,
        capabilities: Set<Capability>,
    ): Device {
        val device = Device(
            id = UUID.randomUUID().toString(),
            name = name,
            type = type,
            role = role,
            host = host,
            sshUser = sshUser,
            sshPort = sshPort,
            capabilities = capabilities,
            // Never claim online before the engine has actually reached it.
            state = DeviceState.UNKNOWN,
        )
        _devices.value = _devices.value + device
        return device
    }

    fun remove(id: String) {
        _devices.value = _devices.value.filterNot { it.id == id }
    }

    fun byId(id: String): Device? = _devices.value.firstOrNull { it.id == id }

    /**
     * Optimistic local flip on a container. As with power, the engine's next
     * report is authoritative — this only stops the UI feeling dead between
     * the tap and the round trip.
     */
    fun setContainerRunning(deviceId: String, container: String, running: Boolean) {
        _devices.value = _devices.value.map { device ->
            if (device.id != deviceId) device
            else device.copy(
                containers = device.containers.map {
                    if (it.name == container && it.stoppable) it.copy(running = running) else it
                }
            )
        }
    }

    /** Optimistic local flip. The engine's next report is authoritative. */
    fun setPower(id: String, on: Boolean) {
        _devices.value = _devices.value.map {
            if (it.id == id) it.copy(powerOn = on, state = if (on) it.state else DeviceState.OFFLINE)
            else it
        }
    }

    fun applyTelemetry(id: String, telemetry: Telemetry, state: DeviceState) {
        _devices.value = _devices.value.map {
            if (it.id == id) it.copy(telemetry = telemetry, state = state) else it
        }
        // The snapshot updates the device; the same reading also extends the
        // series. One call site, so the graph cannot silently diverge from the
        // number printed above it.
        val sample = Sample(
            atMillis = System.currentTimeMillis(),
            cpuPercent = telemetry.cpuPercent,
            memPercent = telemetry.memPercent,
            diskPercent = telemetry.diskPercent,
            tempC = telemetry.tempC,
        )
        _history.value = _history.value + (id to historyFor(id).plus(sample))
    }

    /**
     * Operator-editable device settings.
     *
     * Deliberately not a general-purpose copy: host and credentials-adjacent
     * fields that the engine owns are not editable from here, and neither is
     * live state. This edits how the device is described and displayed.
     */
    fun updateSettings(
        id: String,
        name: String,
        host: String,
        role: String,
        visibleStats: Set<StatKind>,
        powerControlsEnabled: Boolean,
        powerRequiresBiometric: Boolean,
    ) {
        _devices.value = _devices.value.map {
            if (it.id != id) it
            else it.copy(
                name = name.trim().ifBlank { it.name },
                host = host.trim().ifBlank { it.host },
                role = role.trim(),
                visibleStats = visibleStats,
                powerControlsEnabled = powerControlsEnabled,
                powerRequiresBiometric = powerRequiresBiometric,
            )
        }
    }

    /**
     * PLACEHOLDER DATA — remove before this is shown as a real inventory.
     * Mirrors the machines actually in play so the layout is honest about the
     * shapes it has to handle: a Pi with thermals, a GPU box with containers,
     * and one that is offline.
     */
    private fun seed(): List<Device> = listOf(
        Device(
            id = "seed-serverpi",
            name = "serverpi",
            type = DeviceType.RASPBERRY_PI,
            role = "Pi-hole and log collector",
            host = "100.110.30.122",
            sshUser = "pi",
            state = DeviceState.ONLINE,
            telemetry = Telemetry(
                cpuPercent = 12, memPercent = 41, tempC = 47.2,
                diskPercent = 38, uptimeSeconds = 986_400,
            ),
        ),
        Device(
            id = "seed-gpubox",
            name = "shamitsgamingpc",
            type = DeviceType.SERVER,
            role = "Ollama host and demo machine",
            host = "100.112.187.20",
            sshUser = "shami",
            state = DeviceState.ONLINE,
            telemetry = Telemetry(
                cpuPercent = 34, memPercent = 62, tempC = 58.0,
                diskPercent = 71, containersRunning = 4, containersTotal = 6,
                uptimeSeconds = 14_200,
            ),
            containers = listOf(
                // The engine refuses to stop itself — a control that takes
                // away your ability to issue controls is not a control.
                Container("centron-engine", "centron/engine:0.1", true, "healthy", 3, 210, stoppable = false),
                Container("ollama", "ollama/ollama:latest", true, "healthy", 21, 9_400),
                Container("pihole", "pihole/pihole:latest", true, "healthy", 1, 96),
                Container("grafana", "grafana/grafana:11", true, null, 2, 180),
                Container("prometheus", "prom/prometheus:v2", false, null, null, null),
                Container("minio", "minio/minio:latest", false, "unhealthy", null, null),
            ),
        ),
        Device(
            id = "seed-nas",
            name = "vault",
            type = DeviceType.NAS,
            role = "Backups and media",
            host = "192.168.1.40",
            sshUser = "admin",
            state = DeviceState.OFFLINE,
            powerOn = false,
            telemetry = Telemetry(),
        ),
    )

    /**
     * PLACEHOLDER SERIES — goes with [seed], and goes when it goes.
     *
     * Nothing feeds [applyTelemetry] yet: the engine does not report device
     * telemetry over the contract, so on a real paired site these graphs are
     * empty until that exists. This exists so the panel can be looked at and
     * judged, not so it can be presented as live.
     *
     * "vault" deliberately gets nothing. An offline device with no history is
     * a case the graph has to handle, and seeding it would hide that.
     */
    private fun seedHistory(): Map<String, TelemetryHistory> {
        val now = System.currentTimeMillis()
        val stepMs = 5_000L
        val count = 60

        fun series(build: (Int) -> Sample): TelemetryHistory =
            TelemetryHistory((0 until count).map { build(it) })

        return mapOf(
            "seed-serverpi" to series { i ->
                val phase = i / 6.0
                Sample(
                    atMillis = now - (count - i) * stepMs,
                    cpuPercent = (12 + 9 * kotlin.math.sin(phase)).toInt().coerceIn(2, 99),
                    memPercent = (41 + i * 0.05).toInt().coerceIn(2, 99),
                    diskPercent = 38,
                    tempC = 47.2 + 3.0 * kotlin.math.sin(phase / 1.7),
                )
            },
            "seed-gpubox" to series { i ->
                val phase = i / 4.0
                Sample(
                    atMillis = now - (count - i) * stepMs,
                    // A GPU box under bursty inference load, which is what
                    // makes it the useful one to look at.
                    cpuPercent = (34 + 26 * kotlin.math.sin(phase)).toInt().coerceIn(2, 99),
                    memPercent = (62 + 6 * kotlin.math.sin(phase / 2.3)).toInt().coerceIn(2, 99),
                    diskPercent = 71,
                    tempC = 58.0 + 9.0 * kotlin.math.sin(phase / 1.3),
                )
            },
        )
    }
}
