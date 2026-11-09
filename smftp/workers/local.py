"""Run potentially recursive local filesystem operations outside the GUI."""
from pathlib import Path
import shutil
from PySide6.QtCore import QThread, Signal


class LocalOperation(QThread):
    failed = Signal(str)

    def __init__(self, operation, arguments, parent=None):
        super().__init__(parent)
        self.operation, self.arguments = operation, arguments

    def run(self):
        try:
            source = Path(self.arguments[0])
            if self.operation == "mkdir":
                source.mkdir()
            elif self.operation == "remove":
                if source.is_dir() and not source.is_symlink():
                    shutil.rmtree(source)
                else:
                    source.unlink()
            elif self.operation in ("rename", "move"):
                target = Path(self.arguments[1])
                if target.exists():
                    raise FileExistsError
                if source.is_dir() and source.resolve() in target.resolve().parents:
                    raise ValueError
                shutil.move(str(source), str(target))
            elif self.operation == "copy":
                target = Path(self.arguments[1])
                if target.exists() or source.is_symlink():
                    raise FileExistsError
                if source.is_dir():
                    if source.resolve() in target.resolve().parents:
                        raise ValueError
                    shutil.copytree(source, target, symlinks=True)
                else:
                    shutil.copy2(source, target)
        except Exception:
            self.failed.emit("L’opération locale a échoué. Vérifiez les permissions et le nom de destination.")
