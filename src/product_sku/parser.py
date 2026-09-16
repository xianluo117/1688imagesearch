"""Conservative product-scoped pieceWeightScaleInfo parser."""
import re
from typing import Any

from .extraction import (MAX_BYTES, MAX_CANDIDATES, MAX_DEPTH, MAX_NODES,
                         DecodeLimit, Page, decode_string, json_roots)
from .models import Sku, SkuResult, Specification
from .urls import normalize_url

FIELD = "pieceWeightScaleInfo"
IDENTITY_KEYS = ("offerId", "productId", "itemId")
EXCLUDED_BRANCH = re.compile(r"recommend|related|similar|guess|suggest|hotoffer", re.I)


def identifier(value: Any) -> str | None:
    if type(value) is int:
        value = str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,39}", value):
        return value
    return None


def _arrays(roots: list[Any], product_id: str) -> tuple[list[Any], bool]:
    arrays: list[Any] = []
    unscoped = False
    nodes = 0

    def walk(value: Any, owner: str | None, depth: int) -> None:
        nonlocal nodes, unscoped
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise DecodeLimit("structure_limit")
        if isinstance(value, str):
            decoded = decode_string(value)
            if decoded is not value and decoded != value:
                walk(decoded, owner, depth + 1)
        elif isinstance(value, list):
            # Lists may contain unrelated offers even under a current-product root.
            for child in value:
                walk(child, None, depth + 1)
        elif isinstance(value, dict):
            identities = [identifier(value[key]) for key in IDENTITY_KEYS if key in value]
            if identities:
                owner = identities[0] if None not in identities and len(set(identities)) == 1 else "!conflict"
            elif "id" in value:
                # An untyped id marks an unknown entity, not a proven offer identity.
                owner = None
            if FIELD in value:
                if owner == product_id:
                    arrays.append(value[FIELD])
                    if len(arrays) > MAX_CANDIDATES:
                        raise DecodeLimit("candidate_limit")
                elif owner is None or owner == "!conflict":
                    unscoped = True
            for key, child in value.items():
                if key != FIELD and not EXCLUDED_BRANCH.search(key):
                    walk(child, owner, depth + 1)

    for root in roots:
        walk(root, None, 0)
    return arrays, unscoped


def _decode_array(value: Any) -> Any:
    for _ in range(MAX_DEPTH):
        if not isinstance(value, str):
            return value
        decoded = decode_string(value)
        if decoded == value:
            return value
        value = decoded
    raise DecodeLimit("structure_limit")


def _rows(arrays: list[Any]) -> tuple[list[Sku], list[str], bool]:
    found: dict[str, Sku] = {}
    conflicts: set[str] = set()
    warnings: set[str] = set()
    malformed = False
    rows_seen = 0
    for array in arrays:
        if not isinstance(array, list):
            malformed = True
            warnings.add("invalid_field_format")
            continue
        for row in array:
            rows_seen += 1
            if rows_seen > MAX_NODES:
                raise DecodeLimit("row_limit")
            if not isinstance(row, dict) or not identifier(row.get("skuId")):
                warnings.add("invalid_sku_rows")
                continue
            positions = {}
            invalid = False
            for key, value in row.items():
                match = re.fullmatch(r"sku([1-9][0-9]*)", key)
                if not match:
                    continue
                position = int(match.group(1)) if len(match.group(1)) < 3 else 100
                if position > 32 or (value is not None and not isinstance(value, str)):
                    invalid = True
                    break
                positions[position] = value
            if invalid or not positions or not any(isinstance(v, str) and v.strip() for v in positions.values()):
                warnings.add("invalid_sku_rows")
                continue
            sku_id = identifier(row["skuId"])
            sku = Sku(sku_id, [Specification(i, f"sku{i}", positions.get(i))
                               for i in range(1, max(positions) + 1)])
            if sku_id in conflicts:
                continue
            if sku_id in found and found[sku_id] != sku:
                del found[sku_id]
                conflicts.add(sku_id)
                warnings.add("conflicting_sku_rows")
            else:
                found[sku_id] = sku
    return list(found.values()), sorted(warnings), malformed


def parse_detail(text: str, url: str) -> SkuResult:
    product_id, canonical = normalize_url(url)
    result = SkuResult(product_id, canonical, "parse_failed", "invalid_json")
    if len(text.encode("utf-8")) > MAX_BYTES:
        result.reason = "response_too_large"
        return result
    try:
        page = Page(text)
        if page.blocked:
            result.status = page.blocked
            result.reason = "explicit_access_page"
            return result
        roots, malformed_json = json_roots(page)
        arrays, unscoped = _arrays(roots, product_id)
        if not arrays:
            if malformed_json:
                result.reason = "invalid_json"
            elif unscoped:
                result.reason = "unconfirmed_product_context"
            else:
                result.status, result.reason = "source_not_applicable", "field_missing"
            return result
        arrays = [_decode_array(array) for array in arrays]
        result.skus, result.warnings, malformed_field = _rows(arrays)
        if not result.skus:
            if all(isinstance(a, list) and not a for a in arrays):
                result.status, result.reason = "source_not_applicable", "empty_array"
            else:
                result.reason = "invalid_field_format" if malformed_field else "no_valid_skus"
            return result
        if malformed_json:
            result.warnings.append("malformed_other_candidate")
        if unscoped:
            result.warnings.append("unconfirmed_other_candidate")
        result.main_image = page.main_image(product_id)
        if result.main_image is None:
            result.warnings.append("main_image_unavailable")
        result.warnings.extend(["specification_names_unverified", "sku_completeness_unknown"])
        result.status, result.reason = "partial_success", "sku_data_found"
    except (DecodeLimit, RecursionError, ValueError) as exc:
        result.skus = []
        result.status = "parse_failed"
        result.reason = str(exc) if isinstance(exc, DecodeLimit) else "invalid_structure"
    return result
