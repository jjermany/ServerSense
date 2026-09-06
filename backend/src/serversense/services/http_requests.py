import zlib
from typing import Any

import httpx

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class BoundedDecoder:
    """Limit wire bytes and expansion before allocating decoded response data."""

    def __init__(self, encoding: str, limit: int) -> None:
        self.encoding = encoding.strip().lower()
        if self.encoding not in {"", "identity", "gzip", "deflate"}:
            raise ValueError("External service returned an unsupported content encoding")
        self.limit = limit
        self.received = 0
        self.decoded = 0
        self._decoder = (
            zlib.decompressobj(31 if self.encoding == "gzip" else zlib.MAX_WBITS)
            if self.encoding in {"gzip", "deflate"}
            else None
        )

    def feed(self, chunk: bytes) -> bytes:
        self.received += len(chunk)
        if self.received > self.limit:
            raise ValueError("External service response exceeds the size limit")
        if self._decoder is None:
            return chunk
        output = bytearray()
        try:
            while chunk:
                if self._decoder.eof:
                    if self.encoding != "gzip":
                        raise ValueError("External service returned trailing compressed data")
                    self._decoder = zlib.decompressobj(31)
                decoded = self._decoder.decompress(chunk, self.limit - self.decoded + 1)
                self.decoded += len(decoded)
                if self.decoded > self.limit:
                    raise ValueError("External service response exceeds the size limit")
                output.extend(decoded)
                chunk = self._decoder.unused_data
        except zlib.error as exc:
            raise ValueError("External service returned invalid compressed data") from exc
        return bytes(output)

    def finish(self) -> None:
        if self._decoder is not None and not self._decoder.eof:
            raise ValueError("External service returned incomplete compressed data")


def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    kwargs["follow_redirects"] = False
    request_headers = httpx.Headers(kwargs.get("headers") or {})
    request_headers["Accept-Encoding"] = "gzip, deflate"
    kwargs["headers"] = request_headers
    with httpx.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        decoder = BoundedDecoder(response.headers.get("content-encoding", ""), MAX_RESPONSE_BYTES)
        body = bytearray()
        for chunk in response.iter_raw():
            body.extend(decoder.feed(chunk))
        decoder.finish()
        # We have already decompressed the payload; do not decode it a
        # second time when constructing the bounded, reusable response.
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        }
        return httpx.Response(
            response.status_code, headers=headers, content=bytes(body), request=response.request
        )


def get(url: str, **kwargs: Any) -> httpx.Response:
    return _request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> httpx.Response:
    return _request("POST", url, **kwargs)
