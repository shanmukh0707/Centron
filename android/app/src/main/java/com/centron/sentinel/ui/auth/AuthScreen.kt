package com.centron.sentinel.ui.auth

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.InfiniteRepeatableSpec
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.Motion
import com.centron.sentinel.ui.theme.Space
import com.centron.sentinel.ui.theme.Status
import kotlinx.coroutines.delay

/**
 * Sign in.
 *
 * Layout reasoning, since "clean" is doing a lot of work in the brief:
 *
 * Left-aligned, not centred. A centred stack with a big logo is the default
 * consumer-app shape and reads as generic. This is an operator tool; ranging
 * the type left gives it the posture of an instrument panel and puts the
 * wordmark, headline and buttons on one optical axis a thumb can track.
 *
 * The live dot beside the wordmark is the same component language as the
 * connection bar on the event screen. Before you have signed in, it is idle
 * grey and breathing slowly; it is the app telling you what it is for. That is
 * the one piece of motion that is not purely functional, and it earns its
 * place by being the product's core metaphor rather than decoration.
 *
 * No gradient mesh, no frosted glass, no floating orbs. The surface steps are
 * flat because a security tool that looks like a crypto landing page is not
 * telling the truth about itself.
 */
@Composable
fun AuthScreen(
    state: AuthState,
    onProvider: (AuthProvider) -> Unit,
    onEmail: () -> Unit,
    onSkip: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var mode by remember { mutableStateOf(AuthMode.SignIn) }

    // Staggered entrance. Elements arrive in reading order rather than all at
    // once, which makes a dense screen feel calm instead of dumped.
    var revealed by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        repeat(6) {
            revealed = it + 1
            delay(Motion.StaggerMs.toLong())
        }
    }

    val busy = state is AuthState.Working

    Box(
        modifier
            .fillMaxSize()
            .background(Ink.Base)
            .padding(horizontal = Space.Gutter),
    ) {
        Column(
            Modifier
                .fillMaxSize()
                .padding(top = Space.Xxl, bottom = Space.Lg),
        ) {
            Spacer(Modifier.height(Space.Lg))

            Stagger(revealed, 1) { Wordmark() }

            // Distribute the slack above and below the headline rather than
            // dumping it all in one gap. On a tall display the previous
            // layout left a dead zone between the subhead and the buttons;
            // this keeps the actions in the thumb arc while the type block
            // sits on the upper third.
            Spacer(Modifier.weight(0.7f))

            Stagger(revealed, 2) {
                Text(
                    text = if (mode == AuthMode.SignIn) "Your homelab,\nwatched." else "Set up\nCentron.",
                    style = MaterialTheme.typography.displaySmall,
                    color = Ink.Primary,
                )
            }

            Spacer(Modifier.height(Space.Md))

            Stagger(revealed, 3) {
                Text(
                    text = if (mode == AuthMode.SignIn) {
                        "Sign in to reach your engines."
                    } else {
                        "Create an account, then pair your first engine."
                    },
                    style = MaterialTheme.typography.bodyLarge,
                    color = Ink.Secondary,
                )
            }

            Spacer(Modifier.weight(1f))

            Stagger(revealed, 4) {
                Column(verticalArrangement = Arrangement.spacedBy(Space.Sm)) {
                    AuthProvider.entries.forEach { provider ->
                        ProviderButton(
                            provider = provider,
                            enabled = !busy,
                            loading = (state as? AuthState.Working)?.provider == provider,
                            onClick = { onProvider(provider) },
                        )
                    }
                }
            }

            Spacer(Modifier.height(Space.Lg))

            Stagger(revealed, 5) { HairlineDivider() }

            Spacer(Modifier.height(Space.Lg))

            Stagger(revealed, 6) {
                Column {
                    SecondaryButton(
                        label = "Continue with email",
                        enabled = !busy,
                        onClick = onEmail,
                    )

                    // Error owns vertical space only when present, so the
                    // button stack never jumps on first failure.
                    AnimatedVisibility(
                        visible = state is AuthState.Failed,
                        enter = fadeIn(tween(200)) + expandVertically(tween(220)),
                        exit = fadeOut(tween(120)) + shrinkVertically(tween(160)),
                    ) {
                        ErrorNote((state as? AuthState.Failed)?.message.orEmpty())
                    }

                    Spacer(Modifier.height(Space.Lg))

                    ModeToggle(mode) { mode = it }

                    Spacer(Modifier.height(Space.Md))

                    /*
                     * The architecture's actual entry point.
                     *
                     * CENTRON v2 pairs a phone to an engine with a code; there
                     * is no cloud account. Until that decision is revisited,
                     * this is the path that genuinely works, and the demo must
                     * never be blocked behind a sign-in that cannot complete.
                     * Understated rather than hidden — it is the honest door.
                     */
                    Text(
                        "Skip — connect to an engine directly",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Ink.Tertiary,
                        textAlign = TextAlign.Center,
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable(onClick = onSkip)
                            .padding(vertical = Space.Sm),
                    )
                }
            }
        }
    }
}

private enum class AuthMode { SignIn, SignUp }

// ---------------------------------------------------------------- pieces

@Composable
private fun Wordmark() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        BreathingDot()
        Spacer(Modifier.width(Space.Sm + Space.Xs))
        Text(
            "CENTRON",
            style = MaterialTheme.typography.labelSmall,
            color = Ink.Secondary,
        )
    }
}

/**
 * Slow pulse, idle grey. Deliberately off-tempo from a heartbeat — it should
 * read as "listening", not as an alert. Once connected, the event screen's bar
 * takes over this role in green.
 */
@Composable
private fun BreathingDot() {
    val transition = rememberInfiniteTransition(label = "breath")
    val glow by transition.animateFloat(
        initialValue = 0.28f,
        targetValue = 1f,
        animationSpec = InfiniteRepeatableSpec(
            animation = tween(2200, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "glow",
    )

    Box(contentAlignment = Alignment.Center) {
        Box(
            Modifier
                .size(16.dp)
                .alpha(glow * 0.22f)
                .clip(CircleShape)
                .background(Status.Idle),
        )
        Box(
            Modifier
                .size(6.dp)
                .alpha(0.45f + glow * 0.55f)
                .clip(CircleShape)
                .background(Status.Idle),
        )
    }
}

@Composable
private fun ProviderButton(
    provider: AuthProvider,
    enabled: Boolean,
    loading: Boolean,
    onClick: () -> Unit,
) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()

    // Spring rather than tween on press: it settles with a little weight,
    // which reads as a physical control instead of a fading rectangle.
    val scale by animateFloatAsState(
        targetValue = if (pressed) 0.985f else 1f,
        animationSpec = spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = Spring.StiffnessHigh),
        label = "press",
    )

    val tint = if (enabled) Ink.Primary else Ink.Tertiary

    Row(
        Modifier
            .fillMaxWidth()
            .scale(scale)
            .clip(RoundedCornerShape(14.dp))
            .background(if (pressed) Ink.Raised else Ink.Surface)
            .border(Space.Hair, Ink.Hairline, RoundedCornerShape(14.dp))
            .clickable(
                interactionSource = interaction,
                indication = null,
                enabled = enabled,
                onClick = onClick,
            )
            .padding(horizontal = Space.Md, vertical = 15.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        when (provider) {
            AuthProvider.GOOGLE -> GoogleMark(tint)
            AuthProvider.MICROSOFT -> MicrosoftMark(tint)
            AuthProvider.GITHUB -> GithubMark(tint)
        }
        Spacer(Modifier.width(Space.Md))
        Text(
            "Continue with ${provider.label}",
            style = MaterialTheme.typography.labelLarge,
            color = tint,
        )
        Spacer(Modifier.weight(1f))
        if (loading) InlineSpinner()
    }
}

@Composable
private fun SecondaryButton(label: String, enabled: Boolean, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()

    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(if (pressed) Accent.ClayPressed else Accent.Clay)
            .clickable(
                interactionSource = interaction,
                indication = null,
                enabled = enabled,
                onClick = onClick,
            )
            .padding(vertical = 15.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelLarge,
            color = Ink.Base,
        )
    }
}

@Composable
private fun InlineSpinner() {
    val transition = rememberInfiniteTransition(label = "spin")
    val a by transition.animateFloat(
        initialValue = 0.2f,
        targetValue = 1f,
        animationSpec = InfiniteRepeatableSpec(
            animation = tween(700, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulse",
    )
    Box(
        Modifier
            .size(7.dp)
            .alpha(a)
            .clip(CircleShape)
            .background(Accent.Clay),
    )
}

@Composable
private fun HairlineDivider() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f).height(Space.Hair).background(Ink.Hairline))
        Text(
            "OR",
            style = MaterialTheme.typography.labelSmall,
            color = Ink.Tertiary,
            modifier = Modifier.padding(horizontal = Space.Md),
        )
        Box(Modifier.weight(1f).height(Space.Hair).background(Ink.Hairline))
    }
}

@Composable
private fun ErrorNote(message: String) {
    Row(
        Modifier
            .padding(top = Space.Md)
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(Status.BadSubtle)
            .border(Space.Hair, Status.Bad.copy(alpha = 0.35f), RoundedCornerShape(10.dp))
            .padding(Space.Md),
    ) {
        Box(
            Modifier
                .padding(top = 6.dp)
                .size(5.dp)
                .clip(CircleShape)
                .background(Status.Bad),
        )
        Spacer(Modifier.width(Space.Sm + Space.Xs))
        Text(
            message,
            style = MaterialTheme.typography.bodyMedium,
            color = Ink.Secondary,
        )
    }
}

@Composable
private fun ModeToggle(mode: AuthMode, onChange: (AuthMode) -> Unit) {
    val target = if (mode == AuthMode.SignIn) AuthMode.SignUp else AuthMode.SignIn
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.Center,
    ) {
        Text(
            if (mode == AuthMode.SignIn) "New here? " else "Already set up? ",
            style = MaterialTheme.typography.bodyMedium,
            color = Ink.Tertiary,
        )
        Text(
            if (mode == AuthMode.SignIn) "Create an account" else "Sign in",
            style = MaterialTheme.typography.bodyMedium,
            color = Accent.Clay,
            textAlign = TextAlign.Center,
            modifier = Modifier.clickable { onChange(target) },
        )
    }
}

/** Reveals its content once [revealed] reaches [index], fading and rising. */
@Composable
private fun Stagger(revealed: Int, index: Int, content: @Composable () -> Unit) {
    val visible = revealed >= index
    val progress by animateFloatAsState(
        targetValue = if (visible) 1f else 0f,
        animationSpec = tween(Motion.EnterMs, easing = FastOutSlowInEasing),
        label = "stagger$index",
    )
    Box(
        Modifier
            .alpha(progress)
            .padding(top = ((1f - progress) * 10f).dp),
    ) {
        content()
    }
}
