package com.centron.sentinel.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import com.centron.sentinel.device.StatKind
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status

/**
 * One stat, as a line over time.
 *
 * Drawn by hand rather than pulled from a charting library. The requirement is
 * a few dozen points on a phone, and every library that does this also brings
 * a theming system that would fight the one already here.
 *
 * Two behaviours worth knowing:
 *
 * 1. **Gaps break the line.** A null sample is a moment the device did not
 *    report, which is not the same as a moment it reported zero. Interpolating
 *    across it would invent data, and drawing it as zero would show a crash
 *    that never happened. The stroke stops and restarts.
 *
 * 2. **Percentages are pinned to 0..100.** An autoscaled CPU graph makes 3%
 *    idle noise look like a spike, which is how dashboards teach people to
 *    ignore them. Temperature autoscales, because it has no natural ceiling.
 */
@Composable
fun StatGraph(
    stat: StatKind,
    series: List<Float?>,
    current: String?,
    modifier: Modifier = Modifier,
) {
    val latest = series.lastOrNull { it != null }
    val line = lineColorFor(stat, latest)
    val hairline = Ink.Hairline
    val empty = Ink.Tertiary

    Column(modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(stat.label, style = MaterialTheme.typography.bodyMedium, color = Ink.Secondary)
            Spacer(Modifier.weight(1f))
            Text(
                current ?: "unknown",
                style = MonoSmall,
                color = if (current == null) Ink.Tertiary else Ink.Primary,
            )
        }

        Spacer(Modifier.height(Space.Sm))

        if (series.count { it != null } < 2) {
            // One point is not a shape. Say so rather than drawing a dot and
            // letting it read as a flat, healthy line.
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(GraphHeight),
                contentAlignment = Alignment.CenterStart,
            ) {
                Text("no history yet", style = MonoSmall, color = empty)
            }
            return@Column
        }

        val (minValue, maxValue) = boundsFor(stat, series)

        Canvas(
            Modifier
                .fillMaxWidth()
                .height(GraphHeight),
        ) {
            val w = size.width
            val h = size.height
            val span = (maxValue - minValue).takeIf { it > 0.01f } ?: 1f

            // Baseline, so an empty stretch still reads as a chart.
            drawLine(
                color = hairline,
                start = Offset(0f, h),
                end = Offset(w, h),
                strokeWidth = 1f,
            )

            val stepX = if (series.size > 1) w / (series.size - 1) else w
            fun yFor(v: Float) = h - ((v - minValue) / span) * h

            val path = Path()
            val fill = Path()
            var drawing = false
            var lastX = 0f

            series.forEachIndexed { index, value ->
                val x = index * stepX
                if (value == null) {
                    // Close off the filled region at the edge of the gap so the
                    // shading does not bridge it either.
                    if (drawing) {
                        fill.lineTo(lastX, h)
                        fill.close()
                    }
                    drawing = false
                    return@forEachIndexed
                }
                val y = yFor(value)
                if (!drawing) {
                    path.moveTo(x, y)
                    fill.moveTo(x, h)
                    fill.lineTo(x, y)
                    drawing = true
                } else {
                    path.lineTo(x, y)
                    fill.lineTo(x, y)
                }
                lastX = x
            }
            if (drawing) {
                fill.lineTo(lastX, h)
                fill.close()
            }

            drawPath(
                path = fill,
                brush = Brush.verticalGradient(
                    listOf(line.copy(alpha = 0.22f), line.copy(alpha = 0.02f)),
                ),
            )
            drawPath(
                path = path,
                color = line,
                style = Stroke(width = 2.5f),
            )

            // The most recent reading, marked. On a series that ends in a gap
            // there is nothing to mark, which is itself informative.
            val lastIndex = series.indexOfLast { it != null }
            if (lastIndex == series.lastIndex) {
                series[lastIndex]?.let { v ->
                    drawCircle(color = line, radius = 3.5f, center = Offset(lastX, yFor(v)))
                }
            }
        }

        Spacer(Modifier.height(Space.Xs))
        Row(Modifier.fillMaxWidth()) {
            Text(axisLabel(minValue, stat), style = MonoSmall, color = Ink.Tertiary)
            Spacer(Modifier.weight(1f))
            Text(axisLabel(maxValue, stat), style = MonoSmall, color = Ink.Tertiary)
        }
    }
}

private val GraphHeight = 56.dp

/**
 * Percentages get a fixed 0..100. Temperature autoscales with headroom,
 * because 40°C and 85°C are both normal for different silicon and a fixed
 * ceiling would flatten every Pi into a line at the bottom.
 */
private fun boundsFor(stat: StatKind, series: List<Float?>): Pair<Float, Float> {
    if (stat != StatKind.TEMPERATURE) return 0f to 100f
    val values = series.filterNotNull()
    val lo = (values.minOrNull() ?: 0f) - 4f
    val hi = (values.maxOrNull() ?: 100f) + 4f
    return lo to hi
}

private fun axisLabel(value: Float, stat: StatKind): String =
    if (stat.unit.isEmpty()) "${value.toInt()}" else "${value.toInt()}${stat.unit}"

/**
 * Colour carries load, not identity.
 *
 * A CPU at 94% should look different from a CPU at 9% without the operator
 * reading the number. Disk is the exception: 80% full is a warning where 80%
 * CPU is just a busy afternoon.
 */
@Composable
private fun lineColorFor(stat: StatKind, latest: Float?): Color {
    val ok = Status.Ok
    val warn = Status.Warn
    val bad = Status.Bad
    val idle = Ink.Tertiary
    if (latest == null) return idle
    return when (stat) {
        StatKind.DISK -> when {
            latest >= 90f -> bad
            latest >= 75f -> warn
            else -> ok
        }
        StatKind.TEMPERATURE -> when {
            latest >= 80f -> bad
            latest >= 70f -> warn
            else -> ok
        }
        else -> when {
            latest >= 90f -> bad
            latest >= 70f -> warn
            else -> ok
        }
    }
}
