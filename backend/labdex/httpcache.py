"""HTTP caching.

API: every successful JSON GET gets a weak ETag (hash of the body, so it already
covers the caller's permissions) and "private, no-cache": the browser always asks,
and an unchanged answer comes back as an empty 304. The frontend's query cache sends
If-None-Match itself.

Static files: Vite puts a content hash in every file under assets/, so those are
cached for a year as immutable; index.html is always revalidated. A precompressed
.br or .gz next to a file is served when the browser accepts it.
"""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from flask import Response, request, send_file

IMMUTABLE = "public, max-age=31536000, immutable"


def api_etag(resp: Response) -> Response:
    if request.method != "GET" or resp.status_code != 200 or resp.is_streamed:
        return resp
    if not (resp.mimetype or "").endswith("json") or not request.path.startswith("/api/"):
        return resp
    data = resp.get_data()
    resp.set_etag(hashlib.blake2b(data, digest_size=12).hexdigest(), weak=True)
    resp.headers["Cache-Control"] = "private, no-cache"
    resp.vary.add("Cookie")
    resp.vary.add("Authorization")
    if request.if_none_match and request.if_none_match.contains_weak(resp.get_etag()[0]):
        resp.status_code = 304
        resp.set_data(b"")
        resp.headers.pop("Content-Length", None)
    return resp


def static_file(root: Path, path: str) -> Response:
    target = root / path
    accept = request.headers.get("Accept-Encoding", "")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    resp = None
    for enc, ext in (("br", ".br"), ("gzip", ".gz")):
        alt = target.with_name(target.name + ext)
        if enc in accept and alt.is_file():
            resp = send_file(alt, mimetype=mime, conditional=True, etag=True)
            resp.headers["Content-Encoding"] = enc
            break
    if resp is None:
        resp = send_file(target, mimetype=mime, conditional=True, etag=True)
    resp.vary.add("Accept-Encoding")
    if path.startswith("assets/"):
        resp.headers["Cache-Control"] = IMMUTABLE
    else:
        resp.headers["Cache-Control"] = "no-cache"
    return resp
