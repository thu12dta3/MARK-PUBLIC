package br.com.bannaman.markmobile

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.size
import kotlin.math.cos
import kotlin.math.sin

internal enum class MarkGlyphKind { Refresh, Settings, Info, Assistant, Calendar, Call, Ar, Camera, Add, Attach }

/** Small crisp outline icons drawn as vectors, so their appearance doesn't depend on emoji fonts. */
@Composable
internal fun MarkGlyph(
    kind: MarkGlyphKind,
    modifier: Modifier = Modifier.size(20.dp),
    tint: Color = Color(0xFFBDF5F6),
) {
    Canvas(modifier = modifier) {
        scale(size.width / 24f, size.height / 24f, pivot = Offset.Zero) {
            val line = Stroke(width = 1.8f, cap = StrokeCap.Round, join = StrokeJoin.Round)
            fun path(block: Path.() -> Unit) = Path().apply(block)
            fun outline(shape: Path) = drawPath(shape, tint, style = line)

            when (kind) {
                MarkGlyphKind.Call -> {
                    val handset = path {
                        moveTo(6.62f, 10.79f)
                        cubicTo(8.06f, 13.62f, 10.38f, 15.94f, 13.21f, 17.38f)
                        lineTo(15.41f, 15.18f)
                        cubicTo(15.68f, 14.91f, 16.08f, 14.82f, 16.43f, 14.94f)
                        cubicTo(17.55f, 15.31f, 18.75f, 15.5f, 20f, 15.5f)
                        cubicTo(20.55f, 15.5f, 21f, 15.95f, 21f, 16.5f)
                        lineTo(21f, 20f)
                        cubicTo(21f, 20.55f, 20.55f, 21f, 20f, 21f)
                        cubicTo(10.61f, 21f, 3f, 13.39f, 3f, 4f)
                        cubicTo(3f, 3.45f, 3.45f, 3f, 4f, 3f)
                        lineTo(7.5f, 3f)
                        cubicTo(8.05f, 3f, 8.5f, 3.45f, 8.5f, 4f)
                        cubicTo(8.5f, 5.25f, 8.7f, 6.45f, 9.07f, 7.57f)
                        cubicTo(9.18f, 7.92f, 9.1f, 8.32f, 8.82f, 8.59f)
                        close()
                    }
                    drawPath(handset, tint)
                }
                MarkGlyphKind.Camera -> {
                    drawRoundRect(tint, topLeft = Offset(3.5f, 7f), size = androidx.compose.ui.geometry.Size(17f, 13f),
                        cornerRadius = androidx.compose.ui.geometry.CornerRadius(2.2f), style = line)
                    outline(path {
                        moveTo(8f, 7f); lineTo(9.7f, 4.8f); lineTo(14.3f, 4.8f); lineTo(16f, 7f)
                    })
                    drawCircle(tint, radius = 3.1f, center = Offset(12f, 13.5f), style = line)
                    drawCircle(tint, radius = 0.8f, center = Offset(12f, 13.5f))
                }
                MarkGlyphKind.Ar -> outline(path {
                    moveTo(12f, 3f); lineTo(20.5f, 7.7f); lineTo(20.5f, 16.3f)
                    lineTo(12f, 21f); lineTo(3.5f, 16.3f); lineTo(3.5f, 7.7f); close()
                    moveTo(3.8f, 7.9f); lineTo(12f, 12.5f); lineTo(20.2f, 7.9f)
                    moveTo(12f, 12.5f); lineTo(12f, 20.5f)
                })
                MarkGlyphKind.Add -> outline(path {
                    moveTo(12f, 5f); lineTo(12f, 19f); moveTo(5f, 12f); lineTo(19f, 12f)
                })
                MarkGlyphKind.Attach -> outline(path {
                    moveTo(8f, 12.5f); lineTo(14.7f, 5.8f)
                    cubicTo(16.3f, 4.2f, 18.8f, 4.2f, 20.4f, 5.8f)
                    cubicTo(22f, 7.4f, 22f, 9.9f, 20.4f, 11.5f)
                    lineTo(11.2f, 20.7f)
                    cubicTo(8.8f, 23.1f, 4.9f, 23.1f, 2.5f, 20.7f)
                    cubicTo(0.1f, 18.3f, 0.1f, 14.4f, 2.5f, 12f)
                    lineTo(11.7f, 2.8f)
                    cubicTo(13.3f, 1.2f, 15.8f, 1.2f, 17.4f, 2.8f)
                })
                MarkGlyphKind.Refresh -> {
                    drawArc(tint, 35f, 285f, false, topLeft = Offset(3f, 3f),
                        size = androidx.compose.ui.geometry.Size(18f, 18f), style = line)
                    outline(path { moveTo(20f, 4f); lineTo(20f, 9f); lineTo(15f, 9f) })
                }
                MarkGlyphKind.Settings -> {
                    val cog = path {
                        for (i in 0 until 24) {
                            val angle = (i * 15.0 - 90.0) * Math.PI / 180.0
                            val radius = if (i % 3 == 0) 10.3f else 8.7f
                            val x = 12f + cos(angle).toFloat() * radius
                            val y = 12f + sin(angle).toFloat() * radius
                            if (i == 0) moveTo(x, y) else lineTo(x, y)
                        }
                        close()
                    }
                    outline(cog)
                    drawCircle(tint, radius = 3.1f, center = Offset(12f, 12f), style = line)
                }
                MarkGlyphKind.Info -> {
                    drawCircle(tint, radius = 9f, center = Offset(12f, 12f), style = line)
                    drawCircle(tint, radius = 0.9f, center = Offset(12f, 7.5f))
                    drawLine(tint, Offset(12f, 11f), Offset(12f, 16.5f), strokeWidth = 1.8f, cap = StrokeCap.Round)
                }
                MarkGlyphKind.Assistant -> {
                    outline(path {
                        moveTo(12f, 2.5f); lineTo(14.1f, 9.9f); lineTo(21.5f, 12f)
                        lineTo(14.1f, 14.1f); lineTo(12f, 21.5f); lineTo(9.9f, 14.1f)
                        lineTo(2.5f, 12f); lineTo(9.9f, 9.9f); close()
                    })
                }
                MarkGlyphKind.Calendar -> {
                    outline(path {
                        moveTo(5f, 4f); lineTo(19f, 4f); cubicTo(20.1f, 4f, 21f, 4.9f, 21f, 6f)
                        lineTo(21f, 19f); cubicTo(21f, 20.1f, 20.1f, 21f, 19f, 21f)
                        lineTo(5f, 21f); cubicTo(3.9f, 21f, 3f, 20.1f, 3f, 19f)
                        lineTo(3f, 6f); cubicTo(3f, 4.9f, 3.9f, 4f, 5f, 4f); close()
                        moveTo(8f, 2.5f); lineTo(8f, 6f); moveTo(16f, 2.5f); lineTo(16f, 6f)
                        moveTo(3.5f, 9f); lineTo(20.5f, 9f)
                        moveTo(8f, 12.5f); lineTo(8f, 12.6f); moveTo(12f, 12.5f); lineTo(12f, 12.6f)
                        moveTo(16f, 12.5f); lineTo(16f, 12.6f); moveTo(8f, 16.5f); lineTo(8f, 16.6f)
                        moveTo(12f, 16.5f); lineTo(12f, 16.6f); moveTo(16f, 16.5f); lineTo(16f, 16.6f)
                    })
                }
            }
        }
    }
}
