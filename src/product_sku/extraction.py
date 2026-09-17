"""Bounded JSON decoding, never JavaScript execution or whole-page unescaping."""
import json
import re
from html.parser import HTMLParser
from typing import Any

from .urls import address_status, normalize_url

MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32
MAX_CANDIDATES = 256
MAX_NODES = 100000


class DecodeLimit(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


class Page(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.meta: dict[str, set[str]] = {}
        self.blocked: str | None = None
        self._script: list[str] | None = None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        if tag == "script":
            self._script = []
        target = attrs.get("action") if tag == "form" else attrs.get("src") if tag in {"script", "iframe"} else None
        if target:
            status = address_status(target)
            if status:
                self.blocked = status
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content"):
                self.meta.setdefault(key.lower(), set()).add(attrs["content"])

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None

    def main_image(self, product_id: str) -> str | None:
        urls = self.meta.get("og:url", set())
        images = self.meta.get("og:image", set())
        if len(urls) != 1 or len(images) != 1:
            return None
        try:
            if normalize_url(next(iter(urls)))[0] != product_id:
                return None
        except ValueError:
            return None
        from urllib.parse import urlsplit
        image = next(iter(images))
        try:
            parts = urlsplit(image)
            if (parts.scheme not in {"https", "http"} or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or re.search(r"[\s\\\x00-\x1f]", image)):
                return None
            _ = parts.port
        except ValueError:
            return None
        return image


def skip_function_body(script: str, start: int) -> int:
    """Skip opaque code, not data. Unknown lexical forms fail closed."""
    depth = 0
    index = start
    while index < len(script):
        char = script[index]
        if char in "\"'`":
            quote = char
            index += 1
            while index < len(script):
                if script[index] == "\\":
                    index += 2
                elif script[index] == quote:
                    index += 1
                    break
                elif quote == "`" and script.startswith("${", index):
                    raise DecodeLimit("unsupported_function_template")
                else:
                    index += 1
            else:
                raise DecodeLimit("invalid_function_wrapper")
            continue
        if script.startswith("//", index):
            end = script.find("\n", index + 2)
            index = len(script) if end < 0 else end + 1
            continue
        if script.startswith("/*", index):
            end = script.find("*/", index + 2)
            if end < 0:
                raise DecodeLimit("invalid_function_wrapper")
            index = end + 2
            continue
        if char == "/":
            # Do not guess regex-versus-division or braces inside regex literals.
            raise DecodeLimit("unsupported_function_token")
        if char == "{":
            depth += 1
            if depth > MAX_DEPTH:
                raise DecodeLimit("structure_limit")
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise DecodeLimit("invalid_function_wrapper")


def raw_data_decode(decoder: json.JSONDecoder, script: str, start: int) -> tuple[Any, int]:
    """JSON plus canonical safe-integer JS property names; no expressions.

    Only a decoder-confirmed property-name position is normalized. The entire
    outer object must then decode successfully, with duplicate-key rejection.
    """
    normalized = script
    inserted = 0
    for _ in range(MAX_CANDIDATES):
        try:
            obj, end = decoder.raw_decode(normalized, start)
            return obj, end - inserted
        except json.JSONDecodeError as exc:
            if exc.msg != "Expecting property name enclosed in double quotes":
                raise
            match = re.match(r"(0|[1-9][0-9]{0,15})\s*:", normalized[exc.pos:])
            if not match or int(match.group(1)) > 9007199254740991:
                raise
            end = exc.pos + len(match.group(1))
            normalized = normalized[:exc.pos] + '"' + normalized[exc.pos:end] + '"' + normalized[end:]
            inserted += 2
    raise DecodeLimit("numeric_key_limit")


FUNCTION = re.compile(r"function\s*(?:[A-Za-z_$][\w$]*\s*)?\(\s*(?:[A-Za-z_$][\w$]*\s*(?:,\s*[A-Za-z_$][\w$]*\s*)*)?\)\s*\{")


def json_roots(page: Page) -> tuple[list[Any], bool]:
    decoder = json.JSONDecoder(object_pairs_hook=unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    roots: list[Any] = []
    attempts = 0
    malformed = False
    for script in page.scripts:
        index = 0
        while index < len(script):
            char = script[index]
            if script.startswith("function", index) and (index == 0 or not re.match(r"[\w$]", script[index - 1])):
                function = FUNCTION.match(script, index)
                if function:
                    index = skip_function_body(script, function.end() - 1)
                    continue
            if script.startswith("//", index):
                end = script.find("\n", index + 2)
                index = len(script) if end < 0 else end + 1
                continue
            if script.startswith("/*", index):
                end = script.find("*/", index + 2)
                index = len(script) if end < 0 else end + 2
                continue
            if char in "'`":
                quote = char
                index += 1
                while index < len(script):
                    if script[index] == "\\":
                        index += 2
                    elif script[index] == quote:
                        index += 1
                        break
                    else:
                        index += 1
                continue
            if char not in '{["':
                index += 1
                continue
            attempts += 1
            if attempts > MAX_CANDIDATES:
                raise DecodeLimit("candidate_limit")
            try:
                obj, end = raw_data_decode(decoder, script, index)
            except DecodeLimit:
                raise
            except (ValueError, RecursionError):
                # Do not salvage inner objects from an invalid outer business object.
                malformed |= "pieceWeightScaleInfo" in script[index:]
                break
            roots.append(obj)
            index = end
    return roots, malformed


def decode_string(value: str) -> Any:
    if value.lstrip().startswith(('{', '[', '"')):
        try:
            return json.loads(value, object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, RecursionError):
            raise DecodeLimit("invalid_nested_json") from None
    return value
