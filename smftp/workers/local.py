"""Run potentially recursive local filesystem operations outside the GUI."""
from pathlib import Path
import os
import shutil
import tempfile
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
                overwrite = len(self.arguments) > 2 and self.arguments[2]
                if target.exists() or target.is_symlink():
                    if not (overwrite and self.replaceable(source, target)):
                        raise FileExistsError(f"La destination existe déjà : {target}")
                    self.replace_file(source, target, keep_source=False)
                    return
                if source.is_dir() and source.resolve() in target.resolve().parents:
                    raise ValueError("Un dossier ne peut pas être déplacé dans lui-même.")
                shutil.move(str(source), str(target))
            elif self.operation == "copy":
                target = Path(self.arguments[1])
                overwrite = len(self.arguments) > 2 and self.arguments[2]
                if source.is_symlink():
                    raise ValueError("Les liens symboliques ne sont pas copiés.")
                if target.exists() or target.is_symlink():
                    if not (overwrite and self.replaceable(source, target)):
                        raise FileExistsError(f"La destination existe déjà : {target}")
                    self.replace_file(source, target, keep_source=True)
                    return
                if source.is_dir():
                    if source.resolve() in target.resolve().parents:
                        raise ValueError("Un dossier ne peut pas être copié dans lui-même.")
                    shutil.copytree(source, target, symlinks=True)
                else:
                    shutil.copy2(source, target)
        except Exception as error:
            detail = f" ({error})" if str(error) else ""
            self.failed.emit("L’opération locale a échoué. Vérifiez les permissions et le nom de destination." + detail)

    @staticmethod
    def replaceable(source: Path, target: Path) -> bool:
        # Only a regular file replaces a regular file; folders are never merged or replaced.
        return source.is_file() and not source.is_symlink() and target.is_file() and not target.is_symlink()

    @staticmethod
    def replace_file(source: Path, target: Path, keep_source: bool) -> None:
        """Write a complete copy beside the target, then swap it in atomically."""
        if source.resolve() == target.resolve():
            return
        handle, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".smftp-tmp", dir=target.parent)
        os.close(handle)
        temporary = Path(name)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        if not keep_source:
            source.unlink()
