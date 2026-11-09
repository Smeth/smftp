"""Native widget smoke tests without a remote account."""
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
from smftp.ui.main_window import MainWindow
from smftp.ui.permissions import PermissionsDialog
from smftp.ui.editor import Editor
from smftp.models.entries import Entry


def test_workspace_and_editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    window.remote.set_entries("/www", [Entry("index.html", False, 150), Entry("assets", True)])
    assert window.remote.view.topLevelItemCount() == 2
    window.remote.search.setText("index")
    assert sum(not window.remote.view.topLevelItem(i).isHidden() for i in range(2)) == 1
    window.toggle_theme()
    window.toggle_theme()
    permissions = PermissionsDialog(window)
    permissions.update_boxes("755")
    permissions.update_octal()
    assert permissions.mode() == 0o755
    path = tmp_path / "code.py"
    path.write_text("print('hello')", encoding="utf-8")
    editor = Editor(str(path), "code.py", window)
    editor.text.setPlainText("print('updated')")
    editor.save()
    assert path.read_text(encoding="utf-8") == "print('updated')"
    window.close()
    loop = QEventLoop()
    window.thread.finished.connect(loop.quit)
    QTimer.singleShot(2000, loop.quit)
    loop.exec()
    app.processEvents()
    assert not window.thread.isRunning()


def test_entry_point_exits_after_smoke():
    import subprocess
    import sys
    from pathlib import Path
    result = subprocess.run([sys.executable, "main.py", "--smoke-test"],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
