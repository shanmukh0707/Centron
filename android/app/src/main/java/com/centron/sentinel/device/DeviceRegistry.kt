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
}
