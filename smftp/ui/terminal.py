"""SSH terminal: a VT/xterm screen (pyte) drawn with Qt, in closable tabs."""
import pyte
from wcwidth import wcwidth
from PySide6.QtCore import Qt, Signal, QEvent, QPoint, QRect, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QKeySequence, QGuiApplication
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QMenu, QToolButton,
                               QStackedWidget, QSizePolicy)

HISTORY = 5000  # Lines kept above the screen.
MARGIN = 8  # Pixels between the text and the edges of the view.
STATE_COLORS = {"connecting": "#d4a72c", "open": "#3fb950", "closed": "#8b949e"}

# Softened xterm palettes. Order: black, red, green, brown (yellow), blue, magenta, cyan, white.
PALETTES = {
    True: {"background": "#0d1117", "foreground": "#d6deeb", "cursor": "#5788f3", "selection": "#264f78",
           "normal": ["#1b2230", "#e5534b", "#57ab5a", "#c69026", "#539bf5", "#b083f0", "#39c5cf", "#c8d1dc"],
           "bright": ["#636e7b", "#ff7b72", "#7ee787", "#e3b341", "#79c0ff", "#d2a8ff", "#56d4dd", "#f0f6fc"]},
    False: {"background": "#ffffff", "foreground": "#1f2328", "cursor": "#346ee4", "selection": "#b6d3ff",
            "normal": ["#24292f", "#cf222e", "#116329", "#7d4e00", "#0550ae", "#8250df", "#1b7c83", "#6e7781"],
            "bright": ["#57606a", "#a40e26", "#1a7f37", "#633c01", "#0969da", "#a475f9", "#3192aa", "#8c959f"]},
}
NAMES = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"]

# Keys without text -> bytes sent to the shell. (normal mode, application cursor mode)
KEYS = {
    Qt.Key.Key_Up: ("\x1b[A", "\x1bOA"), Qt.Key.Key_Down: ("\x1b[B", "\x1bOB"),
    Qt.Key.Key_Right: ("\x1b[C", "\x1bOC"), Qt.Key.Key_Left: ("\x1b[D", "\x1bOD"),
    Qt.Key.Key_Home: ("\x1b[H", "\x1bOH"), Qt.Key.Key_End: ("\x1b[F", "\x1bOF"),
    Qt.Key.Key_Insert: ("\x1b[2~",) * 2, Qt.Key.Key_Delete: ("\x1b[3~",) * 2,
    Qt.Key.Key_PageUp: ("\x1b[5~",) * 2, Qt.Key.Key_PageDown: ("\x1b[6~",) * 2,
    Qt.Key.Key_F1: ("\x1bOP",) * 2, Qt.Key.Key_F2: ("\x1bOQ",) * 2, Qt.Key.Key_F3: ("\x1bOR",) * 2,
    Qt.Key.Key_F4: ("\x1bOS",) * 2, Qt.Key.Key_F5: ("\x1b[15~",) * 2, Qt.Key.Key_F6: ("\x1b[17~",) * 2,
    Qt.Key.Key_F7: ("\x1b[18~",) * 2, Qt.Key.Key_F8: ("\x1b[19~",) * 2, Qt.Key.Key_F9: ("\x1b[20~",) * 2,
    Qt.Key.Key_F10: ("\x1b[21~",) * 2, Qt.Key.Key_F11: ("\x1b[23~",) * 2, Qt.Key.Key_F12: ("\x1b[24~",) * 2,
    Qt.Key.Key_Return: ("\r",) * 2, Qt.Key.Key_Enter: ("\r",) * 2, Qt.Key.Key_Backspace: ("\x7f",) * 2,
    Qt.Key.Key_Tab: ("\t",) * 2, Qt.Key.Key_Backtab: ("\x1b[Z",) * 2, Qt.Key.Key_Escape: ("\x1b",) * 2,
}
APPLICATION_CURSOR = 1 << 5  # DECCKM, as pyte stores private modes.
BRACKETED_PASTE = 2004 << 5
# Window shortcuts that keep working while the terminal has the focus; every other key goes to the shell.
APP_SHORTCUTS = {"Ctrl+Shift+I", "Ctrl+Shift+T", "Ctrl+Shift+W"}


class Screen(pyte.HistoryScreen):
    """pyte screen whose answers to the server's queries (cursor position, device attributes) are sent back."""

    def __init__(self, columns, lines, reply):
        super().__init__(columns, lines, history=HISTORY, ratio=0.5)
        self.reply = reply

    def write_process_input(self, data):
        self.reply(data.encode("utf-8"))

    def resize(self, lines=None, columns=None):
        """Fewer lines: keep the cursor on its row, as xterm does.

        pyte drops lines from the top but puts the cursor back on its old row, one line
        below the prompt. Here the lines above leave for the history and the cursor follows.
        """
        lines = lines or self.lines
        if lines < self.lines:
            overflow = max(0, self.cursor.y + 1 - lines)
            rows = [self.buffer[y] for y in range(self.lines)]
            self.history.top.extend(rows[:overflow])
            self.buffer.clear()
            for y, row in enumerate(rows[overflow:overflow + lines]):
                self.buffer[y] = row
            self.cursor.y -= overflow
            self.lines = lines  # The rows are handled: pyte only applies the columns below.
            self.set_margins()
            self.dirty.update(range(lines))
        super().resize(lines, columns)


class TerminalView(QWidget):
    """Draws the screen and turns keys, paste and resizes into bytes for the shell."""
    typed = Signal(bytes)
    resized = Signal(int, int)  # columns, lines

    def __init__(self, dark=True, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 120)
        font = QFont()
        font.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPointSize(10)
        self.set_font(font)
        self.colors = PALETTES[bool(dark)]
        self.screen = Screen(80, 24, self.typed.emit)
        self.stream = pyte.ByteStream(self.screen)
        self.scroll = 0  # Lines scrolled back into the history; 0 = following the output.
        self.selection = None  # ((line, column), (line, column)) in absolute lines.
        self.selecting = False
        self.blink = True
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.toggle_cursor)
        self.timer.start(550)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.setInterval(60)
        self.resize_timer.timeout.connect(self.apply_size)
        # Right click pastes (as in MobaXterm / PuTTY); Shift + right click opens the menu.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    # ----- appearance -----
    def set_font(self, font):
        # Kept apart from QWidget.font(): the window style sheet imposes 'Segoe UI' on every widget.
        self.term_font = QFont(font)
        metrics = QFontMetrics(self.term_font)
        self.cell_width = max(1, metrics.horizontalAdvance("M"))
        self.cell_height = max(1, metrics.height())
        self.ascent = metrics.ascent()

    def set_dark(self, dark):
        self.colors = PALETTES[bool(dark)]
        self.update()

    def color(self, name, foreground, bold=False):
        if name == "default":
            return QColor(self.colors["foreground" if foreground else "background"])
        if name.startswith("bright") and name[6:] in NAMES:
            return QColor(self.colors["bright"][NAMES.index(name[6:])])
        if name in NAMES:
            index = NAMES.index(name)
            return QColor(self.colors["bright" if bold and foreground else "normal"][index])
        if len(name) == 6:
            try:
                return QColor("#" + name)
            except ValueError:
                pass
        return QColor(self.colors["foreground" if foreground else "background"])

    def toggle_cursor(self):
        self.blink = not self.blink
        if self.hasFocus():
            self.update(self.cursor_rect())

    # ----- screen content -----
    def feed(self, data: bytes) -> None:
        self.stream.feed(data)
        if self.scroll:
            self.scroll = min(self.scroll, len(self.screen.history.top))
        self.blink = True
        self.update()

    def write_notice(self, text: str, color="36") -> None:
        """A local message in the terminal (not sent to the server), e.g. session closed."""
        start = "\r\n" if self.screen.cursor.x else ""
        self.feed(f"{start}\x1b[{color}m{text}\x1b[0m\r\n".encode("utf-8"))

    def history_lines(self):
        return list(self.screen.history.top)

    def visible_lines(self):
        """Rows to draw, oldest first: the end of the history when scrolled back, then the screen."""
        history = self.history_lines()
        rows = [self.screen.buffer[y] for y in range(self.screen.lines)]
        if not self.scroll:
            return rows, len(history)
        start = len(history) - self.scroll
        combined = history[start:] + rows
        return combined[:self.screen.lines], start

    def line_text(self, row, start=0, end=None):
        end = self.screen.columns if end is None else end
        return "".join(row[x].data for x in range(start, end)).rstrip()

    def absolute_line(self, absolute):
        history = self.history_lines()
        if absolute < len(history):
            return history[absolute]
        return self.screen.buffer[absolute - len(history)]

    # ----- painting -----
    def paintEvent(self, event):
        painter = QPainter(self)
        background = QColor(self.colors["background"])
        painter.fillRect(self.rect(), background)
        painter.setFont(self.term_font)
        rows, first = self.visible_lines()
        selection = self.ordered_selection()
        width, height = self.cell_width, self.cell_height
        for y, row in enumerate(rows):
            absolute = first + y
            x = 0
            while x < self.screen.columns:
                char = row[x]
                fg = self.color(char.fg, True, char.bold)
                bg = self.color(char.bg, False)
                if char.reverse:
                    fg, bg = bg, fg
                if selection and self.in_selection(selection, absolute, x):
                    bg = QColor(self.colors["selection"])
                text = char.data or " "
                cells = 2 if text != " " and (wcwidth(text[0]) or 1) > 1 else 1
                rect = QRect(MARGIN + x * width, MARGIN + y * height, width * cells, height)
                if bg != background:
                    painter.fillRect(rect, bg)
                if text.strip():
                    font = painter.font()
                    if font.bold() != char.bold or font.italic() != char.italics or font.underline() != char.underscore:
                        font.setBold(char.bold)
                        font.setItalic(char.italics)
                        font.setUnderline(char.underscore)
                        painter.setFont(font)
                    painter.setPen(fg)
                    painter.drawText(MARGIN + x * width, MARGIN + y * height + self.ascent, text)
                x += cells
        cursor = self.screen.cursor
        if not self.scroll and not cursor.hidden:
            rect = self.cursor_rect()
            painter.setPen(QColor(self.colors["cursor"]))
            if self.hasFocus() and self.blink:
                painter.fillRect(rect, QColor(self.colors["cursor"]))
                char = self.screen.buffer[cursor.y][cursor.x]
                if char.data.strip():
                    painter.setPen(background)
                    painter.drawText(rect.x(), rect.y() + self.ascent, char.data)
            elif not self.hasFocus():
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()

    def cursor_rect(self):
        cursor = self.screen.cursor
        return QRect(MARGIN + min(cursor.x, self.screen.columns - 1) * self.cell_width,
                     MARGIN + cursor.y * self.cell_height, self.cell_width, self.cell_height)

    def resizeEvent(self, event):
        # Layout passes go through transient sizes (down to the minimum width) and pyte cuts the
        # lines already shown when the screen shrinks: only the size that remains is applied.
        self.resize_timer.start()
        super().resizeEvent(event)

    def showEvent(self, event):
        self.resize_timer.start()
        super().showEvent(event)

    def apply_size(self):
        if self.isVisible():
            self.fit(self.width(), self.height())

    def fit(self, width, height):
        """Columns and lines for a pixel size; the shell is told through ``resized``."""
        columns = max(2, (width - 2 * MARGIN) // self.cell_width)
        lines = max(2, (height - 2 * MARGIN) // self.cell_height)
        if (columns, lines) != (self.screen.columns, self.screen.lines):
            self.screen.resize(lines, columns)
            self.scroll = min(self.scroll, len(self.screen.history.top))
            self.resized.emit(columns, lines)

    # ----- keyboard -----
    def focusNextPrevChild(self, forward):
        return False  # Tab completes in the shell instead of moving the focus.

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            # Keys go to the shell (Ctrl+C, Ctrl+R, F2…), not to the window's menu shortcuts.
            if self.shortcut_text(event) not in APP_SHORTCUTS:
                event.accept()
                return True
        return super().event(event)

    @staticmethod
    def shortcut_text(event):
        modifiers = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier |
                                         Qt.KeyboardModifier.AltModifier)
        return QKeySequence(event.key() | modifiers.value).toString()

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        key = event.key()
        if ctrl and shift and key == Qt.Key.Key_C:
            self.copy()
            return
        if ctrl and shift and key == Qt.Key.Key_V:
            self.paste()
            return
        if shift and key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            step = self.screen.lines - 1
            self.scroll_by(step if key == Qt.Key.Key_PageUp else -step)
            return
        data = self.key_bytes(event, ctrl, alt)
        if data:
            self.send_input(data)
        else:
            super().keyPressEvent(event)

    def key_bytes(self, event, ctrl, alt):
        key = event.key()
        if key in KEYS:
            sequence = KEYS[key][1 if APPLICATION_CURSOR in self.screen.mode else 0]
            return (("\x1b" if alt else "") + sequence).encode()
        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return (("\x1b" if alt else "") + chr(key - Qt.Key.Key_A + 1)).encode()
        if ctrl and key in (Qt.Key.Key_Space, Qt.Key.Key_At):
            return b"\x00"
        controls = {Qt.Key.Key_BracketLeft: b"\x1b", Qt.Key.Key_Backslash: b"\x1c",
                    Qt.Key.Key_BracketRight: b"\x1d", Qt.Key.Key_Underscore: b"\x1f"}
        if ctrl and key in controls:
            return controls[key]
        text = event.text()
        if text:
            return (("\x1b" if alt and not ctrl else "") + text).encode("utf-8")
        return b""

    def inputMethodEvent(self, event):
        if event.commitString():
            self.send_input(event.commitString().encode("utf-8"))
        event.accept()

    def send_input(self, data: bytes):
        self.scroll = 0
        self.selection = None
        self.blink = True
        self.typed.emit(data)
        self.update()

    # ----- scrolling, selection, clipboard -----
    def scroll_by(self, lines):
        self.scroll = max(0, min(len(self.screen.history.top), self.scroll + lines))
        self.update()

    def wheelEvent(self, event):
        notches = event.angleDelta().y() / 120
        if notches:
            self.scroll_by(int(round(notches * 3)) or (1 if notches > 0 else -1))

    def cell_at(self, point: QPoint):
        rows, first = self.visible_lines()
        y = max(0, min(self.screen.lines - 1, (point.y() - MARGIN) // self.cell_height))
        x = max(0, min(self.screen.columns, round((point.x() - MARGIN) / self.cell_width)))
        return first + y, x

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self.cell_at(event.position().toPoint())
            self.selection = (cell, cell)
            self.selecting = True
            self.update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.paste()
        elif event.button() == Qt.MouseButton.RightButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.context_menu(event.position().toPoint())
            else:
                self.paste()

    def mouseMoveEvent(self, event):
        if self.selecting and self.selection:
            self.selection = (self.selection[0], self.cell_at(event.position().toPoint()))
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selecting = False
            if self.selection and self.selection[0] == self.selection[1]:
                self.selection = None
            self.copy()  # Selecting copies, as in MobaXterm / PuTTY.
            self.update()

    def mouseDoubleClickEvent(self, event):
        line, x = self.cell_at(event.position().toPoint())
        row = self.absolute_line(line)
        text = "".join(row[i].data or " " for i in range(self.screen.columns))
        x = min(x, len(text) - 1)
        if not text[x].strip():
            return
        start, end = x, x
        while start > 0 and text[start - 1].strip():
            start -= 1
        while end < len(text) and text[end].strip():
            end += 1
        self.selection = ((line, start), (line, end))
        self.copy()
        self.update()

    def ordered_selection(self):
        if not self.selection:
            return None
        return tuple(sorted(self.selection))

    @staticmethod
    def in_selection(selection, line, x):
        (start_line, start_x), (end_line, end_x) = selection
        if line < start_line or line > end_line:
            return False
        if start_line == end_line:
            return start_x <= x < end_x
        if line == start_line:
            return x >= start_x
        if line == end_line:
            return x < end_x
        return True

    def selected_text(self):
        selection = self.ordered_selection()
        if not selection:
            return ""
        (start_line, start_x), (end_line, end_x) = selection
        total = len(self.screen.history.top) + self.screen.lines
        parts = []
        for line in range(start_line, min(end_line, total - 1) + 1):
            row = self.absolute_line(line)
            first = start_x if line == start_line else 0
            last = end_x if line == end_line else self.screen.columns
            parts.append(self.line_text(row, first, last))
        return "\n".join(parts)

    def copy(self):
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def paste(self):
        text = QGuiApplication.clipboard().text()
        if not text:
            return
        # An ESC in the clipboard could end bracketed paste mode ("\x1b[201~") and make
        # the shell run the rest as typed commands; other control characters are dropped too.
        text = "".join(c for c in text if c in "\t\n\r" or (ord(c) >= 32 and ord(c) != 127))
        lines = [line for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
        if len(lines) > 1 and not self.confirm_multiline(lines):
            return
        data = text.replace("\r\n", "\r").replace("\n", "\r").encode("utf-8")
        if BRACKETED_PASTE in self.screen.mode:
            data = b"\x1b[200~" + data + b"\x1b[201~"
        self.send_input(data)

    def confirm_multiline(self, lines) -> bool:
        """Several lines may run several commands at once: show them before sending."""
        from PySide6.QtWidgets import QMessageBox
        shown = "\n".join(lines[:8]) + ("\n…" if len(lines) > 8 else "")
        return QMessageBox.question(
            self, self.tr("Coller plusieurs lignes"),
            self.tr("Le texte à coller contient %1 lignes ; le serveur peut les exécuter comme autant de commandes :")
            .replace("%1", str(len(lines))) + "\n\n" + shown + "\n\n" + self.tr("Coller quand même ?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def clear_history(self):
        self.screen.history.top.clear()
        self.scroll = 0
        self.selection = None
        self.update()

    def context_menu(self, point):
        menu = QMenu(self)
        copy = menu.addAction(self.tr("Copier\tCtrl+Maj+C"), self.copy)
        copy.setEnabled(bool(self.selected_text()))
        menu.addAction(self.tr("Coller\tclic droit, Ctrl+Maj+V"), self.paste)
        menu.addSeparator()
        menu.addAction(self.tr("Effacer l’historique de défilement"), self.clear_history)
        menu.exec(self.mapToGlobal(point))


class TerminalSession(QWidget):
    """One tab: a view and the SSH shell feeding it. Enter reconnects once the session has ended."""
    status = Signal(str)
    failed = Signal(object, object)  # ErrorInfo, session
    host_unknown = Signal(object, object)  # UnknownHostError, session
    state_changed = Signal(object)  # session

    def __init__(self, site, connection, dark=True, worker_factory=None, parent=None):
        super().__init__(parent)
        self.site = site
        self.connection = connection
        self.worker_factory = worker_factory
        self.worker = None
        self.state = "idle"  # idle, connecting, open, closed
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = TerminalView(dark, self)
        self.view.typed.connect(self.on_typed)
        self.view.resized.connect(self.on_resized)
        layout.addWidget(self.view)

    @property
    def title(self):
        return self.connection.name or self.connection.host

    @property
    def identity(self):
        return self.connection.identity

    def start(self):
        from smftp.workers.terminal import ShellWorker
        if self.worker is not None and self.worker.isRunning():
            if self.state in ("connecting", "open"):
                return
            # Previous attempt still closing (e.g. just after an unknown-key prompt): let it end.
            self.worker.wait(5000)
        self.set_state("connecting")
        self.view.write_notice(self.tr("Connexion SSH à %s@%s:%s…") % (self.connection.user, self.connection.host,
                                                                         self.connection.port), "90")
        columns, lines = self.view.screen.columns, self.view.screen.lines
        factory = self.worker_factory or ShellWorker
        worker = factory(self.connection, columns, lines)
        worker.status.connect(self.status)
        worker.status.connect(self.show_step)
        worker.opened.connect(lambda: self.set_state("open"))
        worker.output.connect(self.view.feed)
        worker.failed.connect(self.on_failed)
        worker.host_unknown.connect(lambda error: self.host_unknown.emit(error, self))
        worker.closed.connect(self.on_closed)
        self.worker = worker
        worker.start()

    def set_state(self, state):
        self.state = state
        self.state_changed.emit(self)

    def show_step(self, message):
        """Connection steps, in the tab itself: the log is not on screen in the terminal view."""
        if self.state == "connecting":
            text = message.split(" : ", 1)[-1] if message.startswith("Statut") else message
            self.view.write_notice(text[:1].upper() + text[1:], "90")

    def on_typed(self, data):
        if self.state == "open" and self.worker is not None:
            self.worker.send(data)
        elif self.state == "closed" and data in (b"\r", b"\n"):
            self.start()

    def on_resized(self, columns, lines):
        if self.worker is not None:
            self.worker.resize(columns, lines)

    def on_failed(self, info):
        self.view.write_notice(self.tr("Erreur : %s") % info.summary, "31")
        self.failed.emit(info, self)

    def on_closed(self):
        worker = self.sender()
        if worker is not None and worker is not self.worker:
            return  # A previous attempt finishing late.
        if self.state != "stopping":
            self.view.write_notice(self.tr("Session terminée. Appuyez sur Entrée pour vous reconnecter."), "90")
        self.set_state("closed")

    def running(self):
        return self.worker is not None and self.worker.isRunning()

    def stop(self):
        if self.worker is not None:
            self.state = "stopping"
            self.worker.stop()


# Closed sessions whose SSH thread is still ending: kept here, not in the tab area, so that
# neither their deletion nor the area's can happen in the wrong order.
_closing = set()


def retire(session):
    """Detach a closed session from its tabs; it is freed once its thread has ended."""
    for signal in (session.status, session.failed, session.host_unknown, session.state_changed):
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass
    session.hide()
    session.setParent(None)  # A pending deleteLater must not outlive a destroyed parent.
    if session.running():
        _closing.add(session)
        session.worker.finished.connect(lambda: release(session))
    else:
        release(session)


def release(session):
    # No deleteLater: the parentless session belongs to Python, which frees it with its last
    # reference. A deferred Qt deletion could otherwise reach an object Python already freed.
    _closing.discard(session)


def state_icon(state):
    """Small coloured dot: amber while connecting, green when open, grey once ended."""
    from PySide6.QtGui import QIcon, QPixmap
    pixmap = QPixmap(20, 20)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(STATE_COLORS.get(state, STATE_COLORS["closed"])))
    painter.drawEllipse(1, 1, 8, 8)
    painter.end()
    return QIcon(pixmap)


class TerminalArea(QWidget):
    """Centre of the window for SSH profiles: one tab per terminal session."""
    status = Signal(str)
    failed = Signal(object, object)
    host_unknown = Signal(object, object)
    new_session_requested = Signal(object)  # site of the current tab
    emptied = Signal()
    current_changed = Signal(object)  # session or None

    def __init__(self, dark=True, worker_factory=None, parent=None):
        super().__init__(parent)
        self.dark = dark
        self.worker_factory = worker_factory
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.empty = QLabel(self.tr("Aucun terminal ouvert.\n\nDouble-cliquez sur un serveur SSH dans « Mes serveurs », "
                                    "ou faites un clic droit sur un serveur SFTP → « Ouvrir un terminal »."))
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.stack.addWidget(self.empty)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("terminalTabs")
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)  # No base line running under the « + » button.
        self.tabs.currentChanged.connect(self.on_current_changed)
        from smftp.ui.icons import icon_button
        self.new_button = icon_button("plus", self.tr("Nouvelle session sur ce serveur (Ctrl+Maj+T)"),
                                      self.request_new_session)
        self.new_button.setFixedSize(28, 28)
        corner = QWidget()
        corner.setObjectName("tabCorner")
        row = QHBoxLayout(corner)
        row.setContentsMargins(0, 0, 10, 0)
        row.addWidget(self.new_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self.stack.addWidget(self.tabs)

    def sessions(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def current(self):
        return self.tabs.currentWidget()

    def find(self, identity):
        return next((session for session in self.sessions() if session.identity == identity), None)

    def open(self, site, connection):
        session = TerminalSession(site, connection, self.dark, self.worker_factory)
        session.status.connect(lambda message, s=session: self.status.emit(f"[Terminal {s.title}] {message}"))
        session.failed.connect(self.failed)
        session.host_unknown.connect(self.host_unknown)
        session.state_changed.connect(self.update_tab)
        title = session.title
        same = sum(1 for other in self.sessions() if other.title == title)
        index = self.tabs.addTab(session, f"{title} ({same + 1})" if same else title)
        from PySide6.QtCore import QSize
        from smftp.ui.icons import icon_button, make_icon
        close = icon_button("close", self.tr("Fermer la session (Ctrl+Maj+W)"), lambda: self.close_session(session))
        close.setFixedSize(20, 20)
        close.setIconSize(QSize(12, 12))
        close.setIcon(make_icon("close", self.dark))
        self.tabs.tabBar().setTabButton(index, self.tabs.tabBar().ButtonPosition.RightSide, close)
        self.stack.setCurrentWidget(self.tabs)
        self.tabs.setCurrentIndex(index)
        # The tab is not laid out yet: size the screen now so the server starts with the right width.
        # Neither this hidden page nor the new tab is laid out yet; the window's centre is.
        area = (self.parentWidget() or self).size()
        if area.width() > 50 and area.height() > 50:
            session.view.fit(area.width(), area.height() - self.tabs.tabBar().sizeHint().height())
        session.start()
        session.view.setFocus()
        return session

    def show_session(self, session):
        self.tabs.setCurrentWidget(session)
        session.view.setFocus()

    def update_tab(self, session):
        index = self.tabs.indexOf(session)
        if index < 0:
            return
        self.tabs.setTabIcon(index, state_icon(session.state))
        labels = {"connecting": self.tr("connexion…"), "open": self.tr("connecté"), "closed": self.tr("session terminée")}
        self.tabs.setTabToolTip(index, f"{session.connection.user}@{session.connection.host}:{session.connection.port}"
                                       f" — {labels.get(session.state, '')}")
        if session is self.current():
            self.current_changed.emit(session)

    def on_current_changed(self, index):
        session = self.tabs.widget(index) if index >= 0 else None
        if session is not None:
            session.view.setFocus()
        self.current_changed.emit(session)

    def request_new_session(self):
        session = self.current()
        if session is not None:
            self.new_session_requested.emit(session.site)

    def close_session(self, session):
        if session is None:
            return
        session.stop()
        index = self.tabs.indexOf(session)
        # QTabBar deletes a tab's button later (deleteLater) when the tab goes; this button is a
        # Python object, freed by Python. Take it off the tab first so Qt never deletes it twice.
        bar = self.tabs.tabBar()
        button = bar.tabButton(index, bar.ButtonPosition.RightSide)
        if button is not None:
            bar.setTabButton(index, bar.ButtonPosition.RightSide, None)
            try:
                button.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            button.hide()
            button.setParent(None)
        self.tabs.removeTab(index)
        retire(session)
        if not self.tabs.count():
            self.stack.setCurrentWidget(self.empty)
            self.emptied.emit()

    def close_current(self):
        self.close_session(self.current())

    def close_all(self):
        for session in self.sessions():
            self.close_session(session)

    def running(self):
        return any(session.running() for session in self.sessions()) or any(s.running() for s in _closing)

    def set_dark(self, dark):
        self.dark = dark
        for session in self.sessions():
            session.view.set_dark(dark)
