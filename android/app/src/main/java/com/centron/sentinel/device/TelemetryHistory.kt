package com.centron.sentinel.device

/**
 * A bounded series of telemetry samples, for drawing.
 *
 * [Telemetry] is a snapshot — the number right now. A graph needs the shape
 * over time, which is a different thing, so it lives in a different place
 * rather than growing lists onto the device model.
 *
 * Every field stays nullable the whole way through. A device that stopped
 * reporting CPU for two minutes has a gap, and a gap is not a run of zeroes;
 * the renderer breaks the line rather than drawing a cliff to the floor that
 * looks like the box went idle.
 */
data class Sample(
    val atMillis: Long,
    val cpuPercent: Int? = null,
    val memPercent: Int? = null,
    val diskPercent: Int? = null,
    val tempC: Double? = null,
)

data class TelemetryHistory(
    val samples: List<Sample> = emptyList(),
) {
    fun plus(sample: Sample): TelemetryHistory =
        TelemetryHistory((samples + sample).takeLast(MAX_SAMPLES))

    /**
     * The series for one stat, in order, with nulls preserved.
     *
     * Returns an empty list for a stat that has never reported, which the UI
     * renders as "no history" — deliberately distinct from a flat line at zero.
     */
    fun series(stat: StatKind): List<Float?> = when (stat) {
        StatKind.CPU -> samples.map { it.cpuPercent?.toFloat() }
        StatKind.MEMORY -> samples.map { it.memPercent?.toFloat() }
        StatKind.DISK -> samples.map { it.diskPercent?.toFloat() }
        StatKind.TEMPERATURE -> samples.map { it.tempC?.toFloat() }
        StatKind.UPTIME -> emptyList()
    }.takeIf { series -> series.any { it != null } } ?: emptyList()

    companion object {
        /** At one sample per 5s this is ten minutes, which is all a phone
         *  screen can usefully resolve anyway. */
        const val MAX_SAMPLES = 120
    }
}
