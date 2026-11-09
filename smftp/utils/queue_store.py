"""Persist queue descriptions only; never serialize credentials or edit copies."""
import json
import os
from pathlib import Path
import tempfile
from smftp.utils.storage import data_dir
from smftp.utils.profiles import profile_for, normalize_profile
from smftp.models.entries import Connection
from smftp.workers.transfer import Transfer


def write_queue(tasks: list[Transfer]) -> None:
    rows = [dict(profile=profile_for(t.connection), local=t.local, remote=t.remote,
                 upload=t.upload, directory=t.directory, conflict=t.conflict,
                 safe_upload=t.safe_upload) for t in tasks]
    path = data_dir() / "queue.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(rows, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def read_queue() -> list[Transfer]:
    path = data_dir() / "queue.json"
    if not path.exists():
        return []
    if path.stat().st_size > 5_000_000:
        raise ValueError("File de transferts trop volumineuse.")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("File de transferts invalide.")
    tasks = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("upload"), bool) or not isinstance(row.get("directory", False), bool):
            raise ValueError("Description de transfert invalide.")
        profile = Connection(**normalize_profile(row["profile"]))
        if not all(isinstance(row.get(k), str) for k in ("local", "remote")):
            raise ValueError("Chemin de transfert invalide.")
        conflict = row.get("conflict", "ask")
        if conflict not in ("ask", "skip", "rename", "overwrite", "newer", "different", "resume"):
            conflict = "ask"
        tasks.append(Transfer(profile, row["local"], row["remote"], bool(row["upload"]),
                     bool(row.get("directory")), True, conflict, bool(row.get("safe_upload", True))))
    return tasks
