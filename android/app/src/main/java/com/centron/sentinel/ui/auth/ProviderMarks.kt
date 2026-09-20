package com.centron.sentinel.ui.auth

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp

/**
 * Provider marks, drawn monochrome.
 *
 * Two reasons they are monochrome rather than full-colour brand logos:
 *
 * 1. Design. Three saturated multi-colour logos stacked vertically fight each
 *    other and fight the status palette, which is the only colour in this app
 *    allowed to mean something. Monochrome at uniform optical weight makes the
 *    three options read as one considered set rather than a pile of badges.
 *
 * 2. Licensing. Google's Sign-In branding guidelines are prescriptive about
 *    button construction and require their supplied asset; GitHub and Microsoft
 *    both publish brand kits with their own rules.
 *
 * BEFORE SHIPPING: replace these with each provider's official asset and follow
 * their button spec. These are honest stand-ins, sized and weighted so the swap
 * is a drop-in and the layout will not move.
 */

@Composable
fun GoogleMark(tint: Color, modifier: Modifier = Modifier) {
    Canvas(modifier.then(Modifier.size(MarkSize))) {
        val s = size.minDimension
        val stroke = s * 0.16f
        val inset = stroke / 2f
        // Open ring, gap on the right, with the crossbar into the centre.
        drawArc(
            color = tint,
            startAngle = -20f,
            sweepAngle = 300f,
            useCenter = false,
            topLeft = Offset(inset, inset),
            size = Size(s - stroke, s - stroke),
            style = Stroke(width = stroke),
        )
        drawRect(
            color = tint,
            topLeft = Offset(s * 0.52f, s * 0.42f),
            size = Size(s * 0.36f, stroke),
        )
    }
}

@Composable
fun MicrosoftMark(tint: Color, modifier: Modifier = Modifier) {
    Canvas(modifier.then(Modifier.size(MarkSize))) {
        val s = size.minDimension
        val cell = s * 0.44f
        val gap = s * 0.12f
        listOf(
            Offset(0f, 0f),
            Offset(cell + gap, 0f),
            Offset(0f, cell + gap),
            Offset(cell + gap, cell + gap),
        ).forEach { drawRect(color = tint, topLeft = it, size = Size(cell, cell)) }
    }
}

@Composable
fun GithubMark(tint: Color, modifier: Modifier = Modifier) {
    Canvas(modifier.then(Modifier.size(MarkSize))) {
        val s = size.minDimension
        // Head, ears, and the tail that make the silhouette read at 20dp.
        val head = Path().apply {
            addOval(Rect(Offset(s * 0.10f, s * 0.20f), Size(s * 0.80f, s * 0.70f)))
        }
        drawPath(head, tint)

        val earL = Path().apply {
            moveTo(s * 0.20f, s * 0.34f)
            lineTo(s * 0.26f, s * 0.08f)
            lineTo(s * 0.46f, s * 0.24f)
            close()
        }
        val earR = Path().apply {
            moveTo(s * 0.80f, s * 0.34f)
            lineTo(s * 0.74f, s * 0.08f)
            lineTo(s * 0.54f, s * 0.24f)
            close()
        }
        drawPath(earL, tint)
        drawPath(earR, tint)

        val tail = Path().apply {
            moveTo(s * 0.44f, s * 0.86f)
            lineTo(s * 0.44f, s * 1.00f)
            lineTo(s * 0.56f, s * 1.00f)
            lineTo(s * 0.56f, s * 0.86f)
            close()
        }
        drawPath(tail, tint)
    }
}

private val MarkSize = 20.dp
