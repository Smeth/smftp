"""Small vector icons drawn with QPainter.

Drawn in code rather than shipped as files: no image plugin is needed in the
packaged build, and the colours follow the light / dark theme.
"""
from PySide6.QtCore import Qt, QPointF, QRectF, QSize
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap, QColor
from PySide6.QtWidgets import QToolButton

PALETTES = {
    # theme: (normal, disabled)
    True: ("#c4d8ff", "#4a5466"),
    False: ("#174ea6", "#b4bccb"),
}


def _pause(p):
    p.drawRoundedRect(QRectF(5, 4, 3.5, 12), 1, 1)
    p.drawRoundedRect(QRectF(11.5, 4, 3.5, 12), 1, 1)


def _play(p):
    path = QPainterPath(QPointF(6, 4))
    path.lineTo(16, 10)
    path.lineTo(6, 16)
    path.closeSubpath()
    p.drawPath(path)


def _pause_play(p):
    p.drawRoundedRect(QRectF(2.5, 4, 3, 12), 1, 1)
    p.drawRoundedRect(QRectF(7, 4, 3, 12), 1, 1)
    path = QPainterPath(QPointF(11.5, 4))
    path.lineTo(18.5, 10)
    path.lineTo(11.5, 16)
    path.closeSubpath()
    p.drawPath(path)


def _cancel(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(3, 3, 14, 14))
    p.drawLine(QPointF(7.5, 7.5), QPointF(12.5, 12.5))
    p.drawLine(QPointF(12.5, 7.5), QPointF(7.5, 12.5))


def _stop(p):
    p.drawRoundedRect(QRectF(5, 5, 10, 10), 1.5, 1.5)


def _retry(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(4, 4, 12, 12), 60 * 16, 290 * 16)
    p.setBrush(p.pen().color())
    head = QPainterPath(QPointF(10.5, 1.5))
    head.lineTo(15.5, 4.5)
    head.lineTo(10.5, 7.5)
    head.closeSubpath()
    p.drawPath(head)


def _trash(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(3.5, 5.5), QPointF(16.5, 5.5))
    p.drawLine(QPointF(8, 5.5), QPointF(8.5, 3))
    p.drawLine(QPointF(8.5, 3), QPointF(11.5, 3))
    p.drawLine(QPointF(11.5, 3), QPointF(12, 5.5))
    path = QPainterPath(QPointF(5, 5.5))
    path.lineTo(6, 17)
    path.lineTo(14, 17)
    path.lineTo(15, 5.5)
    p.drawPath(path)
    p.drawLine(QPointF(8.5, 8.5), QPointF(8.5, 14))
    p.drawLine(QPointF(11.5, 8.5), QPointF(11.5, 14))


def _copy(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7, 7, 10, 10), 1.5, 1.5)
    path = QPainterPath(QPointF(4.5, 13))
    path.lineTo(3.5, 13)
    path.lineTo(3.5, 3.5)
    path.lineTo(13, 3.5)
    path.lineTo(13, 4.5)
    p.drawPath(path)


def _save(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(10, 3), QPointF(10, 12.5))
    head = QPainterPath(QPointF(6, 9))
    head.lineTo(10, 13)
    head.lineTo(14, 9)
    p.drawPath(head)
    tray = QPainterPath(QPointF(3.5, 12.5))
    tray.lineTo(3.5, 16.5)
    tray.lineTo(16.5, 16.5)
    tray.lineTo(16.5, 12.5)
    p.drawPath(tray)


def _eye(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    outline = QPainterPath(QPointF(2.5, 10))
    outline.quadTo(QPointF(10, 2), QPointF(17.5, 10))
    outline.quadTo(QPointF(10, 18), QPointF(2.5, 10))
    p.drawPath(outline)
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(10, 10), 2.4, 2.4)


def _eye_off(p):
    _eye(p)
    p.drawLine(QPointF(4, 16), QPointF(16, 4))


def _folder(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(2.5, 5))
    path.lineTo(7.5, 5)
    path.lineTo(9.5, 7)
    path.lineTo(17.5, 7)
    path.lineTo(17.5, 15.5)
    path.lineTo(2.5, 15.5)
    path.closeSubpath()
    p.drawPath(path)


def _edit(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    body = QPainterPath(QPointF(4, 16))
    body.lineTo(4.5, 12.5)
    body.lineTo(13, 4)
    body.lineTo(16, 7)
    body.lineTo(7.5, 15.5)
    body.closeSubpath()
    p.drawPath(body)
    p.drawLine(QPointF(11, 6), QPointF(14, 9))


SHAPES = {"pause": _pause, "play": _play, "pause_play": _pause_play, "cancel": _cancel,
          "stop": _stop, "retry": _retry, "trash": _trash, "copy": _copy, "save": _save,
          "eye": _eye, "eye_off": _eye_off, "folder": _folder,
          "edit": _edit}


def _pixmap(name, color, scale):
    pixmap = QPixmap(int(20 * scale), int(20 * scale))
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(scale)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QColor(color))
    SHAPES[name](painter)
    painter.end()
    return pixmap


def make_icon(name, dark=True):
    normal, disabled = PALETTES[bool(dark)]
    icon = QIcon()
    for scale in (1, 2):
        icon.addPixmap(_pixmap(name, normal, scale), QIcon.Mode.Normal)
        icon.addPixmap(_pixmap(name, disabled, scale), QIcon.Mode.Disabled)
    return icon


def icon_button(name, tooltip, callback=None, parent=None):
    """Square tool button showing only an icon; the label lives in the tooltip."""
    button = QToolButton(parent)
    button.setObjectName("iconAction")
    button.setProperty("iconName", name)
    button.setIcon(make_icon(name))
    button.setIconSize(QSize(20, 20))
    button.setFixedSize(34, 34)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setAutoRaise(True)
    if callback is not None:
        button.clicked.connect(callback)
    return button


def set_icon(button, name, dark=None):
    button.setProperty("iconName", name)
    if dark is None:
        dark = button.property("iconDark")
        dark = True if dark is None else bool(dark)
    button.setIcon(make_icon(name, dark))


def recolor(root, dark):
    """Redraw every icon button and field action under ``root`` for the current theme."""
    from PySide6.QtGui import QAction
    for widget in root.findChildren(QToolButton) + root.findChildren(QAction):
        name = widget.property("iconName")
        if name:
            widget.setProperty("iconDark", bool(dark))
            widget.setIcon(make_icon(name, dark))


def theme_of(widget):
    """True for the dark theme, read from the main window when there is one."""
    while widget is not None:
        if hasattr(widget, "dark"):
            return bool(widget.dark)
        widget = widget.parent()
    return True
