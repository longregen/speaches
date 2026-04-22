from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_on_startup(session_dir: Path, max_count: int, max_bytes: int, max_days: int) -> None:
    if not session_dir.exists():
        return
    files = [p for p in session_dir.iterdir() if p.is_file() and p.suffix in {".ndjson", ".raw", ".json"}]
    # Group by session id (filename stem before the first dot)
    sessions: dict[str, list[Path]] = {}
    for p in files:
        sid = p.name.split(".")[0]
        sessions.setdefault(sid, []).append(p)

    now = time.time()
    max_age_s = max_days * 86400 if max_days > 0 else None

    # Age-based removal
    if max_age_s is not None:
        for sid, paths in list(sessions.items()):
            mt = max((p.stat().st_mtime for p in paths), default=0)
            if now - mt > max_age_s:
                for p in paths:
                    _unlink(p)
                sessions.pop(sid, None)

    # Count-based removal: keep newest max_count sessions
    if max_count > 0 and len(sessions) > max_count:
        ordered = sorted(sessions.items(), key=lambda kv: max(p.stat().st_mtime for p in kv[1]), reverse=True)
        for sid, paths in ordered[max_count:]:
            for p in paths:
                _unlink(p)
            sessions.pop(sid, None)

    # Bytes-based removal: newest-first, delete oldest when over cap
    if max_bytes > 0:
        ordered = sorted(sessions.items(), key=lambda kv: max(p.stat().st_mtime for p in kv[1]), reverse=True)
        running = 0
        for _sid, paths in ordered:
            size = sum(p.stat().st_size for p in paths)
            if running + size > max_bytes:
                for p in paths:
                    _unlink(p)
            else:
                running += size


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        logger.exception("Failed to delete inspector artifact %s", path)
