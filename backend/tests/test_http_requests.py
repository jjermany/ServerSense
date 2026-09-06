import gzip
import tracemalloc
import zlib

import httpx
import pytest

from serversense.services import http_requests


def test_compression_bomb_is_rejected_before_full_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compressed = gzip.compress(b"x" * (8 * 1024 * 1024))
    monkeypatch.setattr(http_requests, "MAX_RESPONSE_BYTES", 64 * 1024)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            stream=httpx.ByteStream(compressed),
            headers={"Content-Encoding": "gzip"},
        )
    )
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(httpx, "stream", client.stream)
        tracemalloc.start()
        try:
            with pytest.raises(ValueError, match="size limit"):
                http_requests.get("https://service.test", timeout=5)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    # An after-decompression length check would first allocate the entire 8 MiB.
    assert peak < 1024 * 1024


@pytest.mark.parametrize(
    "encoding,body",
    [
        ("identity", b'{"ok": true}'),
        ("gzip", gzip.compress(b'{"ok": true}')),
        ("gzip", gzip.compress(b'{"ok":') + gzip.compress(b" true}")),
        ("deflate", zlib.compress(b'{"ok": true}')),
    ],
)
def test_decoder_handles_arbitrary_chunk_boundaries(encoding: str, body: bytes) -> None:
    decoder = http_requests.BoundedDecoder(encoding, 1024)
    decoded = b"".join(decoder.feed(bytes([value])) for value in body)
    decoder.finish()
    assert decoded == b'{"ok": true}'


def test_decoder_rejects_truncated_and_unsupported_encodings() -> None:
    decoder = http_requests.BoundedDecoder("gzip", 1024)
    decoder.feed(gzip.compress(b"example")[:-1])
    with pytest.raises(ValueError, match="incomplete"):
        decoder.finish()
    with pytest.raises(ValueError, match="unsupported"):
        http_requests.BoundedDecoder("gzip, gzip", 1024)


def test_external_response_limit_counts_decompressed_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    compressed = gzip.compress(b"x" * (http_requests.MAX_RESPONSE_BYTES + 1))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            stream=httpx.ByteStream(compressed),
            headers={"Content-Encoding": "gzip"},
        )
    )
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(httpx, "stream", client.stream)
        with pytest.raises(ValueError, match="size limit"):
            http_requests.get("https://service.test", timeout=5)


def test_external_response_is_decoded_once_and_redirects_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://other.test"})
        return httpx.Response(
            200,
            stream=httpx.ByteStream(gzip.compress(b'{"ok": true}')),
            headers={"Content-Encoding": "gzip"},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(httpx, "stream", client.stream)
        assert http_requests.get("https://service.test", timeout=5).json() == {"ok": True}
        with pytest.raises(httpx.HTTPStatusError):
            http_requests.get("https://service.test/redirect", timeout=5)
    assert urls == ["https://service.test", "https://service.test/redirect"]
