from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def extract_video_frame(video_path: Path, dest: Path, *, at_sec: float = 0.2) -> Path:
    """
    Снимает один кадр из видео через ffmpeg (для карточки товара).
    Берём чуть после старта — часто там уже виден товар, а не чёрный кадр.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден в PATH")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{at_sec:.2f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        # fallback: первый кадр без seek
        cmd_fb = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(dest),
        ]
        try:
            subprocess.run(cmd_fb, check=True, capture_output=True, timeout=60)
        except subprocess.CalledProcessError as exc2:
            err = (exc2.stderr or exc.stderr or b"").decode("utf-8", errors="replace")
            raise RuntimeError(f"Не удалось взять кадр из видео: {err[:200]}") from exc2
    if not dest.exists() or dest.stat().st_size < 100:
        raise RuntimeError("Кадр из видео пустой")
    log.info("Extracted video frame %s -> %s", video_path.name, dest.name)
    return dest
