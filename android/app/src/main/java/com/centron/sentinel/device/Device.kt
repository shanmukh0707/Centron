package com.centron.sentinel.device

/**
 * A machine the engine manages.
 *
 * Capabilities are declared per device rather than inferred from type, because
 * two Raspberry Pis are not the same device: one on a smart plug can be power
 * cycled, one wired directly cannot. The type picks sensible defaults at add
 * time; the truth is what the engine reports it can actually do.
 *
 * Note what is NOT on this model: no password, no private key, no passphrase.
 * Per the v2 security model the phone never holds SSH credentials. It holds
 * the description of a device; the engine holds the means to reach it.
 */
enum class Capability(val label: String) {
    POWER("Power"),
    THERMALS("Thermals"),
    SYSTEM_USAGE("System usage"),
    CONTAINERS("Containers"),
    STORAGE("Storage"),
    NETWORK("Network"),
    SERVICES("Services"),
}

enum class DeviceType(
    val label: String,
    val blurb: String,
    val defaults: Set<Capability>,
) {
    RASPBERRY_PI(
        "Raspberry Pi",
        "Small ARM board. Pi-hole, sensors, kiosks.",
        setOf(Capability.POWER, Capability.THERMALS, Capability.SYSTEM_USAGE, Capability.SERVICES),
    ),
    SERVER(
        "Server",
        "Always-on box running workloads.",
        setOf(
            Capability.POWER, Capability.THERMALS, Capability.SYSTEM_USAGE,
            Capability.CONTAINERS, Capability.STORAGE, Capability.SERVICES,
        ),
    ),
    NAS(
        "NAS",
        "Network storage. Disks are the point.",
        setOf(Capability.POWER, Capability.STORAGE, Capability.SYSTEM_USAGE, Capability.THERMALS),
    ),
    ROUTER(
        "Router / firewall",
        "Edge device. Where blocks actually land.",
        setOf(Capability.NETWORK, Capability.SYSTEM_USAGE, Capability.SERVICES),
    ),
    WORKSTATION(
        "Workstation",
        "Desktop or laptop you also use directly.",
        setOf(Capability.SYSTEM_USAGE, Capability.THERMALS, Capability.POWER),
    ),
    OTHER(
        "Other",
        "Anything reachable over SSH.",
        setOf(Capability.SYSTEM_USAGE),
    ),
}

enum class DeviceState { ONLINE, DEGRADED, OFFLINE, UNKNOWN }

/**
 * A single readable statistic.
 *
 * Separate from [Capability] on purpose: a capability is what the engine can
 * do with a box, a StatKind is what the operator wants on screen. A Pi that
 * reports thermals is not a Pi whose owner wants a temperature graph.
 */
enum class StatKind(val label: String, val unit: String, val graphable: Boolean) {
    CPU("CPU", "%", true),
    MEMORY("Memory", "%", true),
    DISK("Disk", "%", true),
    TEMPERATURE("Temperature", "°C", true),
    /** A counter, not a series. Graphing it would draw a straight line. */
    UPTIME("Uptime", "", false),
}

/**
 * Live telemetry. All nullable — a device that cannot report thermals is not
 * a device reporting 0°C, and a dashboard that cannot tell those apart lies.
 */
data class Telemetry(
    val cpuPercent: Int? = null,
    val memPercent: Int? = null,
    val tempC: Double? = null,
    val diskPercent: Int? = null,
    val uptimeSeconds: Long? = null,
    val containersRunning: Int? = null,
    val containersTotal: Int? = null,
)

/**
 * A Docker container on a device.
 *
 * Blast radius is carried on the model rather than inferred in the UI, because
 * the engine is the one that knows whether a given container has a restart
 * policy, a volume, or is holding the thing you are presenting from.
 */
data class Container(
    val name: String,
    val image: String,
    val running: Boolean,
    val health: String? = null,
    val cpuPercent: Int? = null,
    val memMb: Int? = null,
    /** False for anything the engine refuses to stop, e.g. itself. */
    val stoppable: Boolean = true,
)

data class Device(
    val id: String,
    val name: String,
    val type: DeviceType,
    /** What this box is for, in the operator's own words. Shown under the name. */
    val role: String,
    val host: String,
    val sshUser: String,
    val sshPort: Int = 22,
    val capabilities: Set<Capability> = type.defaults,
    val state: DeviceState = DeviceState.UNKNOWN,
    val telemetry: Telemetry = Telemetry(),
    val powerOn: Boolean = true,
    val containers: List<Container> = emptyList(),
    /** Which stats this device shows. Operator's choice, per device. */
    val visibleStats: Set<StatKind> = StatKind.entries.toSet(),
    /**
     * Hides the power control entirely.
     *
     * For the box you cannot afford to lose — the one the engine runs on, or
     * anything you would have to walk to in order to bring back. Removing the
     * button is a stronger guarantee than trusting yourself not to press it.
     */
    val powerControlsEnabled: Boolean = true,
    /**
     * Require a fresh unlock before a power change reaches the engine.
     *
     * Defaults on. Off is a deliberate choice for a box where an accidental
     * press costs nothing, and the sheet says so before it lets you.
     */
    val powerRequiresBiometric: Boolean = true,
) {
    fun can(capability: Capability): Boolean = capability in capabilities

    /** Capability and operator preference both have to agree. */
    fun showsPower(): Boolean = can(Capability.POWER) && powerControlsEnabled

    fun shows(stat: StatKind): Boolean = stat in visibleStats
}
