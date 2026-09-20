package com.centron.sentinel.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.unit.dp
import com.centron.sentinel.settings.Site
import com.centron.sentinel.ui.theme.Accent
import com.centron.sentinel.ui.theme.Ink
import com.centron.sentinel.ui.theme.MonoSmall
import com.centron.sentinel.ui.theme.Space

/**
 * Site switcher, centred in the top bar.
 *
 * UniFi's pattern, and it belongs here for the same reason: a site is a whole
 * separate homelab with its own devices and its own policy, and the operator
 * must always be able to see which one they are about to act on. Centring it
 * makes it the title of the screen rather than a setting buried somewhere.
 */
@Composable
fun SiteChip(site: Site?, open: Boolean, onClick: () -> Unit) {
    val spin by animateFloatAsState(
        targetValue = if (open) 180f else 0f,
        animationSpec = tween(200, easing = FastOutSlowInEasing),
        label = "chevron",
    )
    val tint = Ink.Secondary

    Row(
        Modifier
            .clip(RoundedCornerShape(10.dp))
            .background(if (open) Ink.Raised else androidx.compose.ui.graphics.Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = Space.Sm + Space.Xs, vertical = Space.Xs + 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            site?.name ?: "No site",
            style = MaterialTheme.typography.labelLarge,
            color = Ink.Primary,
        )
        Spacer(Modifier.width(Space.Sm))
        Canvas(Modifier.size(9.dp).rotate(spin)) {
            val s = size.minDimension
            val stroke = s * 0.18f
            drawLine(tint, Offset(s * 0.15f, s * 0.35f), Offset(s * 0.50f, s * 0.70f), strokeWidth = stroke)
            drawLine(tint, Offset(s * 0.50f, s * 0.70f), Offset(s * 0.85f, s * 0.35f), strokeWidth = stroke)
        }
    }
}

@Composable
fun SitePanel(
    visible: Boolean,
    sites: List<Site>,
    currentId: String,
    onSelect: (Site) -> Unit,
    onManage: () -> Unit,
) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn(tween(140)) + expandVertically(tween(240, easing = FastOutSlowInEasing)),
        exit = fadeOut(tween(110)) + shrinkVertically(tween(180)),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = Space.Sm, vertical = Space.Sm)
                .clip(RoundedCornerShape(18.dp))
                .background(Ink.Surface)
                .border(Space.Hair, Ink.Hairline, RoundedCornerShape(18.dp))
                .padding(Space.Sm),
        ) {
            Text(
                "SITES",
                style = MaterialTheme.typography.labelSmall,
                color = Ink.Tertiary,
                modifier = Modifier.padding(horizontal = Space.Md, vertical = Space.Sm),
            )

            sites.forEach { site ->
                val selected = site.id == currentId
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(bottom = Space.Xs)
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (selected) Ink.Raised else androidx.compose.ui.graphics.Color.Transparent)
                        .clickable { onSelect(site) }
                        .padding(horizontal = Space.Md, vertical = Space.Md),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        Modifier
                            .size(6.dp)
                            .clip(CircleShape)
                            .background(if (selected) Accent.Clay else Ink.Tertiary),
                    )
                    Spacer(Modifier.width(Space.Md))
                    Column(Modifier.weight(1f)) {
                        Text(
                            site.name,
                            style = MaterialTheme.typography.titleMedium,
                            color = Ink.Primary,
                        )
                        Text(site.engineAddress, style = MonoSmall, color = Ink.Tertiary)
                    }
                }
            }

            Text(
                "Manage sites",
                style = MaterialTheme.typography.labelSmall,
                color = Accent.Clay,
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .clickable(onClick = onManage)
                    .padding(horizontal = Space.Md, vertical = Space.Md),
            )
        }
    }
}
