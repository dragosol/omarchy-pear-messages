"""Pictures for the conversation view, fetched from BlueBubbles only when they're needed.

Syncing stores messages, never files. When a message with a photo or video comes into view,
the window asks for its *preview*:

  photo  -> resized on the Mac (BlueBubbles' ?width=), re-encoded here as a small JPEG
  video  -> a thumbnail frame. BlueBubbles can't make one or send part of a file, so:
            if the video's index (moov) is at the front, the start of the stream goes into
            ffmpeg and reading stops at the first frame; if it's at the end, the video is
            downloaded to the temporary folder, one frame taken, and the video deleted -
            up to AUTO_VIDEO bytes; bigger ones wait for you to ask
  other  -> nothing (a file chip is enough)

The original file is fetched only when you open or preview it, and always to the temporary
folder. Previews of the newest photos in a conversation are kept; the rest are temporary.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import urllib.parse
import urllib.request

AUTO_VIDEO = 30 * 1024 * 1024
PREVIEW_WIDTH = 600


class MediaError(Exception):
    pass


def _open(client, guid: str, width: int = 0):
    path = f"/attachment/{urllib.parse.quote(guid, safe='')}/download"
    if width:
        path += f"?width={width}&quality=good"
    q = urllib.parse.urlencode({"password": client.password})
    req = urllib.request.Request(f"{client.url}/api/v1{path}{'&' if '?' in path else '?'}{q}",
                                 headers={"User-Agent": "PearMessages/1.0"})
    try:
        return urllib.request.urlopen(req, timeout=120)
    except Exception as e:
        raise MediaError(f"Can't fetch it from the Mac: {getattr(e, 'reason', e)}") from None


def _to_jpeg(src: str, dst: str, width: int = 0) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    vf = ["-vf", f"scale='min({width},iw)':-2"] if width else []
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, *vf, "-frames:v", "1", "-q:v", "4", dst],
                       capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0


def photo_preview(client, guid: str, dst: str) -> str:
    """A small JPEG of the photo. Returns the path written."""
    with _open(client, guid, PREVIEW_WIDTH) as r:
        data = r.read()
    fd, raw = tempfile.mkstemp(dir=os.path.dirname(dst))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        if _to_jpeg(raw, dst + ".part.jpg"):
            os.replace(dst + ".part.jpg", dst)
        else:                       # no ffmpeg: keep what the Mac sent
            os.replace(raw, dst)
    finally:
        for p in (raw, dst + ".part.jpg"):
            if os.path.exists(p):
                os.unlink(p)
    return dst


def _moov_first(head: bytes) -> bool | None:
    """Is the MP4/MOV index before the media data? None if the start doesn't say."""
    off = 0
    while off + 8 <= len(head):
        size, typ = struct.unpack(">I4s", head[off:off + 8])
        if typ == b"moov":
            return True
        if typ == b"mdat":
            return False
        if size == 1 and off + 16 <= len(head):
            size = struct.unpack(">Q", head[off + 8:off + 16])[0]
        if size < 8:
            return None
        off += size
    return None


def video_thumb(client, guid: str, size: int, dst: str, scratch: str, allow_big: bool = False) -> str:
    """A JPEG frame from the start of the video."""
    if not shutil.which("ffmpeg"):
        raise MediaError("Video thumbnails need ffmpeg")
    r = _open(client, guid)
    try:
        head = r.read(64 * 1024)
        first = _moov_first(head)
        if first:
            # stream: ffmpeg stops once it has a frame, and so do we
            p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-i", "pipe:0", "-frames:v", "1",
                                  "-vf", f"scale='min({PREVIEW_WIDTH},iw)':-2", "-q:v", "4", dst + ".part.jpg"],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                p.stdin.write(head)
                while p.poll() is None:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    p.stdin.write(chunk)
            except (BrokenPipeError, OSError):
                pass                       # ffmpeg had its frame and closed the pipe
            try:
                p.stdin.close()
            except OSError:
                pass
            p.wait(timeout=30)
        else:
            if size > AUTO_VIDEO and not allow_big:
                raise MediaError("big")
            # the index is at the end: the whole file is needed for one frame
            fd, tmp = tempfile.mkstemp(dir=scratch, suffix=".video")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(head)
                    shutil.copyfileobj(r, f, 1 << 20)
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", tmp, "-frames:v", "1",
                                "-vf", f"scale='min({PREVIEW_WIDTH},iw)':-2", "-q:v", "4", dst + ".part.jpg"],
                               capture_output=True, timeout=60)
            finally:
                os.unlink(tmp)
    finally:
        r.close()
    if not os.path.exists(dst + ".part.jpg") or os.path.getsize(dst + ".part.jpg") == 0:
        raise MediaError("Couldn't read a frame from the video")
    os.replace(dst + ".part.jpg", dst)
    return dst


def original(client, guid: str, dst: str) -> str:
    with _open(client, guid) as r, open(dst + ".part", "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(dst + ".part", dst)
    return dst
