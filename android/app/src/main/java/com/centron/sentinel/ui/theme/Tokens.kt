package com.centron.sentinel.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * Design tokens.
 *
 * The brief: UniFi crossed with Claude. What that means concretely —
 *
 * UniFi gives us the instrument-panel posture. Dense, technical, status
 * forward. Values are legible at a glance because an operator is scanning, not
 * reading. Nothing decorative competes with a status colour.
 *
 * Claude gives us warmth and air. The greys are warm-shifted rather than the
 * blue-black every dashboard defaults to, type is given room, and the accent is
 * clay rather than the saturated blue or violet that reads as generic SaaS.
 *
 * Rules both palettes follow:
 *
 * 1. Status colours are reserved. Green, amber and red mean connection health
 *    and severity, nowhere else. An accent that competed with them would make
 *    the one thing that matters at 3am harder to spot.
 * 2. One accent, used sparingly. Clay marks the primary path and nothing else.
 * 3. Surfaces step by luminance, not by border. Three greys carry the whole
 *    hierarchy; borders are hairlines for definition, not structure.
 */
@Immutable
data class Palette(
    val base: Color,
    val surface: Color,
    val raised: Color,
    val hairline: Color,
    val textPrimary: Color,
    val textSecondary: Color,
    val textTertiary: Color,
    val clay: Color,
    val clayPressed: Color,
    val claySubtle: Color,
    val ok: Color,
    val warn: Color,
    val bad: Color,
    val idle: Color,
    val okSubtle: Color,
    val warnSubtle: Color,
    val badSubtle: Color,
    val isLight: Boolean,
)

/** Night. The default, because this is read in a dim room next to a rack. */
val DarkPalette = Palette(
    base = Color(0xFF14130F),
    surface = Color(0xFF1C1A15),
    raised = Color(0xFF24211B),
    hairline = Color(0xFF332F26),
    textPrimary = Color(0xFFF2EEE3),
    textSecondary = Color(0xFFA8A093),
    textTertiary = Color(0xFF6E6758),
    clay = Color(0xFFD97757),
    clayPressed = Color(0xFFC2664A),
    claySubtle = Color(0x1FD97757),
    ok = Color(0xFF6E9B5F),
    warn = Color(0xFFD9A441),
    bad = Color(0xFFC7553F),
    idle = Color(0xFF6E6758),
    okSubtle = Color(0x1F6E9B5F),
    warnSubtle = Color(0x1FD9A441),
    badSubtle = Color(0x1FC7553F),
    isLight = false,
)

/**
 * Creme.
 *
 * Not white. A paper-warm base keeps the same temperature as the dark theme so
 * the product does not feel like two different apps, and takes the glare off a
 * screen you might be reading in daylight beside a window.
 *
 * Status colours are darkened rather than reused: the dark theme's muted green
 * fails contrast on a light surface, and status is the one thing that must
 * stay readable.
 */
val CremePalette = Palette(
    base = Color(0xFFF6F2E9),
    surface = Color(0xFFFFFBF2),
    raised = Color(0xFFEDE7DA),
    hairline = Color(0xFFDCD4C3),
    textPrimary = Color(0xFF1E1B15),
    textSecondary = Color(0xFF5C5647),
    textTertiary = Color(0xFF8A8271),
    clay = Color(0xFFB4512F),
    clayPressed = Color(0xFF973F21),
    claySubtle = Color(0x1FB4512F),
    ok = Color(0xFF4A6B3D),
    warn = Color(0xFF8A6215),
    bad = Color(0xFF9E3520),
    idle = Color(0xFF8A8271),
    okSubtle = Color(0x1F4A6B3D),
    warnSubtle = Color(0x1F8A6215),
    badSubtle = Color(0x1F9E3520),
    isLight = true,
)

val LocalPalette = staticCompositionLocalOf { DarkPalette }

/**
 * Token accessors.
 *
 * Composable getters rather than constants so a theme switch recomposes. Where
 * a colour is needed outside composition — inside a Canvas draw lambda, or a
 * plain helper function — read it into a local val first and pass it down.
 */
object Ink {
    val Base: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.base
    val Surface: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.surface
    val Raised: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.raised
    val Hairline: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.hairline
    val Primary: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.textPrimary
    val Secondary: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.textSecondary
    val Tertiary: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.textTertiary
}

object Accent {
    val Clay: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.clay
    val ClayPressed: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.clayPressed
    val ClaySubtle: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.claySubtle
}

object Status {
    val Ok: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.ok
    val Warn: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.warn
    val Bad: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.bad
    val Idle: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.idle
    val OkSubtle: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.okSubtle
    val WarnSubtle: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.warnSubtle
    val BadSubtle: Color @Composable @ReadOnlyComposable get() = LocalPalette.current.badSubtle
}

object Space {
    val Hair = 1.dp
    val Xs = 4.dp
    val Sm = 8.dp
    val Md = 16.dp
    val Lg = 24.dp
    val Xl = 32.dp
    val Xxl = 48.dp

    /** Side gutter. Generous, because this is a tool you read, not a form you
     *  rush. Matches the event list so screens feel continuous. */
    val Gutter = 24.dp
}

object Motion {
    /** Entrance stagger between successive elements. Long enough to read as
     *  sequence, short enough that the screen is settled before a thumb
     *  arrives. */
    const val StaggerMs = 55
    const val EnterMs = 420
    const val PressMs = 90
}
