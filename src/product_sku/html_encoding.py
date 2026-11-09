"""有界、严格的详情文本解码；不探测未声明的传统编码。"""
import re
from html.parser import HTMLParser

from .extraction import MAX_BYTES


META_SCAN_BYTES = 8192
HEADER_SCAN_CHARS = 4096
_CHARSET = re.compile(
    r'''(?:^|;)\s*([^=;\s]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^;\s]*))\s*(?=;|$)''',
    re.IGNORECASE,
)
_ENCODINGS = {"utf-8": "utf-8", "utf8": "utf-8",
              "gb18030": "gb18030", "gbk": "gb18030", "gb2312": "gb18030"}
_BINARY_MAGIC = (b"\x1f\x8b", b"PK\x03\x04", b"BZh", b"\xfd7zXZ\x00",
                 b"\x28\xb5\x2f\xfd", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"%PDF-")


def _charsets(value: str) -> list[str]:
    return [next(group for group in match.groups()[1:] if group is not None).strip().lower()
            for match in _CHARSET.finditer(value) if match.group(1).lower() == "charset"]


class _MetaCharsets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.labels = []

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        values = dict(attrs)
        self.labels.extend((value or "").strip().lower()
                           for name, value in attrs if name == "charset")
        if (values.get("http-equiv") or "").strip().lower() == "content-type":
            self.labels.extend(_charsets(values.get("content") or ""))


def decode_html(body: bytes, content_type: str = "") -> str:
    """优先保留既有UTF-8行为，失败时仅按明确白名单声明解码。"""
    if len(body) > MAX_BYTES:
        raise ValueError("response_too_large")
    # zlib有两字节头；Brotli/raw deflate没有可靠magic，不能仅凭头推断或解压。
    zlib_header = (len(body) >= 2 and body[0] & 15 == 8 and body[0] >> 4 <= 7
                   and int.from_bytes(body[:2], "big") % 31 == 0)
    if (body.startswith(_BINARY_MAGIC) or zlib_header
            or any(byte < 32 and byte not in (9, 10, 13) for byte in body)
            or b"\x7f" in body):
        raise ValueError("unsupported_encoding")
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        if body.startswith(b"\xef\xbb\xbf"):
            raise ValueError("invalid_encoding") from None
    if len(content_type) > HEADER_SCAN_CHARS:
        raise ValueError("unsupported_encoding")
    labels = _charsets(content_type)
    if not labels:
        parser = _MetaCharsets()
        # latin-1仅用于一对一保留字节以读取ASCII声明，不作为正文解码结果。
        parser.feed(body[:META_SCAN_BYTES].decode("latin-1"))
        parser.close()
        labels = parser.labels
    encodings = {_ENCODINGS.get(label) for label in labels}
    if not labels or None in encodings or len(encodings) != 1:
        raise ValueError("unsupported_encoding")
    try:
        return body.decode(encodings.pop())
    except UnicodeDecodeError:
        raise ValueError("invalid_encoding") from None
