"""Pictures for the conversation view, fetched from BlueBubbles only when they're needed.

Syncing stores messages, never files. When a message with a photo or video comes into view,
the window asks for its *preview*:

  photo  -> resized on the Mac (BlueBubbles' ?width=), re-encoded here as a small JPEG, turned
            upright by the original's EXIF orientation when the resized copy lost it
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

from . import bluebubbles

AUTO_VIDEO = 30 * 1024 * 1024
PREVIEW_WIDTH = 600
# A 600 px preview is tens of KB. Anything far past that is not a preview, and it would be held
# in memory whole, so reading stops here.
MAX_PREVIEW = 16 * 1024 * 1024
# How much of the original is read to find its orientation: the EXIF block sits at the start.
ORIENTATION_HEAD = 128 * 1024

# EXIF orientation -> the ffmpeg filter that turns the stored pixels upright (checked against
# ffmpeg's own autorotate for all eight).
_ORIENT_FILTER = {2: "hflip", 3: "hflip,vflip", 4: "vflip", 5: "transpose=0",
                  6: "transpose=1", 7: "transpose=3", 8: "transpose=2"}


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
        return bluebubbles.urlopen(req, timeout=120)
    except Exception as e:
        raise MediaError(f"Can't fetch it from the Mac: {getattr(e, 'reason', e)}") from None


def _read_capped(r, limit: int) -> bytes:
    data = r.read(limit + 1)
    if len(data) > limit:
        raise MediaError("The Mac sent far more than a preview")
    return data


def _exif_orientation(tiff: bytes) -> int:
    """The Orientation tag (0x0112) of IFD0 in a TIFF-structured EXIF block, 1 if absent."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return 1
    e = "<" if tiff[:2] == b"II" else ">"
    ifd = struct.unpack(e + "I", tiff[4:8])[0]
    if ifd + 2 > len(tiff):
        return 1
    for i in range(struct.unpack(e + "H", tiff[ifd:ifd + 2])[0]):
        off = ifd + 2 + 12 * i
        if off + 12 > len(tiff):
            break
        tag, typ = struct.unpack(e + "HH", tiff[off:off + 4])
        if tag == 0x0112 and typ == 3:              # SHORT, stored in the value field
            v = struct.unpack(e + "H", tiff[off + 8:off + 10])[0]
            return v if 1 <= v <= 8 else 1
    return 1


def image_info(head: bytes) -> tuple[int, int, int]:
    """(orientation, width, height) of a JPEG or PNG from its first bytes, as stored, before
    any rotation. Orientation is 1 when there is no tag; sizes are 0 when the start doesn't say."""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        w = h = 0
        orient = 1
        off = 8
        while off + 8 <= len(head):
            n, typ = struct.unpack(">I4s", head[off:off + 8])
            body = head[off + 8:off + 8 + n]
            if typ == b"IHDR" and len(body) >= 8:
                w, h = struct.unpack(">II", body[:8])
            elif typ == b"eXIf":
                orient = _exif_orientation(body)
            elif typ in (b"IDAT", b"IEND"):
                break
            off += 12 + n
        return orient, w, h
    if head[:2] != b"\xff\xd8":
        return 1, 0, 0
    orient, w, h = 1, 0, 0
    off = 2
    while off + 4 <= len(head):
        if head[off] != 0xFF:
            break
        marker = head[off + 1]
        if marker == 0xFF:                          # fill byte
            off += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            off += 2
            continue
        n = struct.unpack(">H", head[off + 2:off + 4])[0]
        body = head[off + 4:off + 2 + n]
        if marker == 0xE1 and body[:6] == b"Exif\0\0" and orient == 1:
            orient = _exif_orientation(body[6:])
        elif marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) \
                and len(body) >= 5:
            h, w = struct.unpack(">HH", body[1:5])
            break                                   # the frame header comes after EXIF
        elif marker == 0xDA:                        # image data, nothing more to learn
            break
        off += 2 + n
    return orient, w, h


def _upright_filter(preview: bytes, original_head: bytes) -> str:
    """The rotation the preview still needs, "" if none.

    BlueBubbles (1.9.12 at least) resizes a photo into a PNG of the stored pixels with no
    orientation tag, so a portrait iPhone photo came out sideways. The original has the tag.
    It is applied only when the preview has no tag of its own (ffmpeg honours one) and, for the
    quarter turns, only when the preview is still the shape of the stored pixels - a server that
    already turned its copy upright is not turned again.
    """
    p_orient, pw, ph = image_info(preview)
    if p_orient != 1:
        return ""
    o_orient, ow, oh = image_info(original_head)
    vf = _ORIENT_FILTER.get(o_orient, "")
    if vf and o_orient >= 5 and pw and ph and ow and oh and (pw > ph) != (ow > oh):
        return ""                                   # already turned
    return vf


def _to_jpeg(src: str, dst: str, width: int = 0, rotate: str = "") -> bool:
    if not shutil.which("ffmpeg"):
        return False
    filters = [f for f in (rotate, f"scale='min({width},iw)':-2" if width else "") if f]
    vf = ["-vf", ",".join(filters)] if filters else []
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, *vf, "-frames:v", "1", "-q:v", "4", dst],
                       capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0


def _private(path: str) -> str:
    """Make a finished cache file readable only by you, and hand the path back.

    Everything here lands through os.replace, which keeps the mode of the file being moved.
    tempfile.mkstemp gives 0600, but ffmpeg and a plain open() both write 0644 under the usual
    umask, so a photo or video out of your messages ended up world-readable in the cache. The
    directory above it is private, which is the real protection; this is the layer under it.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path

def photo_preview(client, guid: str, dst: str) -> str:
    """A small JPEG of the photo. Returns the path written."""
    with _open(client, guid, PREVIEW_WIDTH) as r:
        data = _read_capped(r, MAX_PREVIEW)
    rotate = ""
    if image_info(data)[0] == 1:                    # no tag of its own: ask the original
        try:
            with _open(client, guid) as r:
                rotate = _upright_filter(data, r.read(ORIENTATION_HEAD))
        except (MediaError, OSError):
            pass                                    # a sideways photo beats no photo
    fd, raw = tempfile.mkstemp(dir=os.path.dirname(dst))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        if _to_jpeg(raw, dst + ".part.jpg", rotate=rotate):
            os.replace(dst + ".part.jpg", dst)   # ffmpeg wrote this one 0644
        else:                       # no ffmpeg: keep what the Mac sent
            os.replace(raw, dst)
        _private(dst)
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
                limit = max(size, AUTO_VIDEO) if not allow_big else 0
                with os.fdopen(fd, "wb") as f:
                    f.write(head)
                    got = len(head)
                    while chunk := r.read(1 << 20):
                        got += len(chunk)
                        if limit and got > limit:   # more than the Mac said the video was
                            raise MediaError("big")
                        f.write(chunk)
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
    return _private(dst)


def original(client, guid: str, dst: str) -> str:
    fd = os.open(dst + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with _open(client, guid) as r, os.fdopen(fd, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(dst + ".part", dst)
    return _private(dst)
