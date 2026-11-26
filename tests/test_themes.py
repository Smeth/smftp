"""Both themes are complete and readable."""
import re
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

THEMES = Path(__file__).resolve().parents[1] / "resources" / "themes"


def contrast(foreground, background):
    def luminance(color):
        values = [int(color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        values = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("name", ["dark", "light"])
def test_theme_styles_every_common_control(name):
    text = (THEMES / f"{name}.qss").read_text(encoding="utf-8")
    for selector in ("QScrollBar:horizontal", "QSpinBox::up-arrow", "QSpinBox::down-arrow", "QMenu::item:selected",
                     "QToolTip", "QProgressBar::chunk"):
        assert selector in text, selector
    for image in re.findall(r"url\(@THEMES@/([^)]+)\)", text):
        assert (THEMES / image).is_file(), image


def test_window_points_theme_images_to_the_installed_folder():
    from smftp.ui.main_window import MainWindow
    from test_audit_known_issues import close_window
    QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        assert "@THEMES@" not in window.styleSheet() and THEMES.as_posix() in window.styleSheet()
    finally:
        close_window(window)


def test_secondary_text_is_readable_on_white():
    text = (THEMES / "light.qss").read_text(encoding="utf-8")
    muted = re.findall(r"QLabel#muted, QLabel#emptyState \{ color: (#[0-9a-f]{6})", text)[-1]
    hint = re.findall(r"QLabel#hint \{ color: (#[0-9a-f]{6})", text)[-1]
    assert contrast(muted, "#ffffff") >= 4.5 and contrast(hint, "#ffffff") >= 4.5


@pytest.mark.parametrize("dark,background", [(True, "#121720"), (False, "#f7f9fc")])
def test_editor_colors_are_readable_on_their_theme(dark, background):
    from smftp.ui.editor import PALETTES
    assert all(contrast(color, background) >= 4.5 for color in PALETTES[dark])


def test_status_colors_follow_the_theme():
    from smftp.ui.scheduler import STATUS_COLORS
    assert all(contrast(color, "#171b23") >= 4.5 for color in STATUS_COLORS[True].values())
    assert all(contrast(color, "#ffffff") >= 4.5 for color in STATUS_COLORS[False].values())
