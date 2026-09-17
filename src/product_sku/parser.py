"""Conservative product-scoped pieceWeightScaleInfo parser."""
import re
from typing import Any

from .extraction import (MAX_BYTES, MAX_CANDIDATES, MAX_DEPTH, MAX_NODES,
                         DecodeLimit, Page, decode_string, json_roots)
from .models import Sku, SkuResult, Specification
from .urls import normalize_url
from .specification_images import OptionImages
from .spec_ids import SpecIds

FIELD = "pieceWeightScaleInfo"
IDENTITY_KEYS = ("offerId", "productId", "itemId")
EXCLUDED_BRANCH = re.compile(r"recommend|related|similar|guess|suggest|hotoffer", re.I)


def identifier(value: Any) -> str | None:
    if type(value) is int:
        value = str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,39}", value):
        return value
    return None


def _path(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _verified_model(value: dict, product_id: str) -> Any:
    """Observed page schema only, never infer ownership from arbitrary siblings."""
    global_data = _path(value, "result", "global", "globalData")
    sources = [
        _path(global_data, "parametersMap"),
        _path(global_data, "model", "offerDetail"),
        _path(global_data, "model", "tradeModel"),
    ]
    for source in sources:
        if not isinstance(source, dict) or identifier(source.get("offerId")) != product_id:
            return None
        if any(identifier(source[k]) != product_id for k in IDENTITY_KEYS if k in source):
            return None
    for source in (value, _path(value, "result"), _path(value, "result", "global"), global_data, _path(global_data, "model")):
        if not isinstance(source, dict) or "id" in source or any(identifier(source[k]) != product_id for k in IDENTITY_KEYS if k in source):
            return None
    return global_data["model"]


def _model_array(value: dict, product_id: str) -> Any:
    model = _verified_model(value, product_id)
    description = _path(model, "detailDescription")
    scale = _path(description, "pieceWeightScale")
    for source in (description, scale):
        if not isinstance(source, dict):
            return None
        if "id" in source or any(identifier(source[k]) != product_id for k in IDENTITY_KEYS if k in source):
            return None
    return scale.get(FIELD)


def _seller_candidate(value: dict, product_id: str) -> dict | None:
    if _verified_model(value, product_id) is None:
        return None
    node = value
    for key in ("result", "data", "Root", "fields", "dataJson", "offerBaseInfo"):
        node = _path(node, key)
        if not isinstance(node, dict) or any(identifier(node[k]) != product_id for k in IDENTITY_KEYS if k in node):
            return None
    return node if identifier(node.get("offerId")) == product_id else None


def _set_seller(result: SkuResult, candidates: list[dict]) -> None:
    # Validate each namespace independently. Never use buyer IDs or login names.
    for source, target in (("sellerUserId", "seller_user_id"), ("sellerMemberId", "seller_member_id")):
        values = set()
        invalid = False
        for candidate in candidates:
            raw = candidate.get(source)
            if raw is None:
                continue
            if source == "sellerUserId":
                value = identifier(raw)
            else:
                value = raw if isinstance(raw, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", raw) else None
            if value is None:
                invalid = True
            else:
                values.add(value)
        if len(values) == 1 and not invalid:
            setattr(result, target, next(iter(values)))
        elif invalid or len(values) > 1:
            result.warnings.append(target + "_unverified")


def _arrays(roots: list[Any], product_id: str, models: list[Any] | None = None,
            sellers: list[dict] | None = None) -> tuple[list[Any], bool]:
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
            if sellers is not None:
                seller = _seller_candidate(value, product_id)
                if seller is not None:
                    sellers.append(seller)
                    if len(sellers) > MAX_CANDIDATES:
                        raise DecodeLimit("candidate_limit")
            model = _verified_model(value, product_id)
            if model is not None and models is not None:
                models.append(model)
                if len(models) > MAX_CANDIDATES:
                    raise DecodeLimit("candidate_limit")
            model_array = _model_array(value, product_id)
            if model_array is not None:
                arrays.append(model_array)
                if len(arrays) > MAX_CANDIDATES:
                    raise DecodeLimit("candidate_limit")
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


def _trade_skus(models: list[Any], product_id: str, images: OptionImages | None = None) -> tuple[list[Sku], list[str]]:
    found: dict[str, Sku] = {}
    conflicts: set[str] = set()
    warnings: set[str] = set()
    total = 0
    delimiter = chr(38) + "gt;"
    spec_ids = SpecIds()
    for model in models:
        rows = _path(model, "tradeModel", "skuMap")
        props = _path(model, "offerDetail", "skuProps")
        if rows is None:
            continue
        if not isinstance(rows, list) or not isinstance(props, list) or not 1 <= len(props) <= 32:
            warnings.add("invalid_trade_schema")
            continue
        names, choices = [], []
        for prop in props:
            if not isinstance(prop, dict) or not isinstance(prop.get("prop"), str) or not prop["prop"].strip():
                break
            values = prop.get("value")
            if not isinstance(values, list) or not values or len(values) > MAX_NODES:
                break
            allowed = [v.get("name") if isinstance(v, dict) else None for v in values]
            if any(not isinstance(v, str) or not v.strip() or delimiter in v for v in allowed):
                break
            if len(set(allowed)) != len(allowed) or prop["prop"] in names:
                break
            names.append(prop["prop"])
            choices.append(set(allowed))
        if len(names) != len(props):
            warnings.add("invalid_trade_specifications")
            continue
        for row in rows:
            total += 1
            if total > MAX_NODES:
                raise DecodeLimit("row_limit")
            if not isinstance(row, dict) or not identifier(row.get("skuId")) or not isinstance(row.get("specAttrs"), str):
                warnings.add("invalid_trade_rows")
                continue
            if any(identifier(row[k]) != product_id for k in IDENTITY_KEYS if k in row):
                warnings.add("invalid_trade_rows")
                continue
            parts = row["specAttrs"].split(delimiter)
            if len(parts) != len(names) or any(part not in allowed for part, allowed in zip(parts, choices)):
                warnings.add("unverified_trade_specifications")
                continue
            sku_id = identifier(row["skuId"])
            sku = Sku(sku_id, [Specification(i, f"sku{i}", part, name)
                               for i, (part, name) in enumerate(zip(parts, names), 1)])
            spec_ids.add(sku_id, row.get("specId"))
            if images is not None:
                images.add(sku, props)
            if sku_id in conflicts:
                continue
            if sku_id in found and found[sku_id] != sku:
                del found[sku_id]
                conflicts.add(sku_id)
                warnings.add("conflicting_sku_rows")
            else:
                found[sku_id] = sku
    skus, spec_warnings = spec_ids.finish(list(found.values()))
    warnings.update(spec_warnings)
    return skus, sorted(warnings)


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
        models: list[Any] = []
        sellers: list[dict] = []
        arrays, unscoped = _arrays(roots, product_id, models, sellers)
        images = OptionImages()
        trade_skus, trade_warnings = _trade_skus(models, product_id, images)
        if trade_skus:
            result.skus = trade_skus
            result.specification_images = images.finish(trade_skus)
            result.warnings = trade_warnings + ["sku_completeness_unknown"]
            if malformed_json:
                result.warnings.append("malformed_other_candidate")
            result.status, result.reason = "partial_success", "sku_data_found"
            _set_seller(result, sellers)
            return result
        if trade_warnings:
            result.warnings = trade_warnings
            result.reason = "unverified_trade_data"
            return result
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
        _set_seller(result, sellers)
        result.specification_images = OptionImages().finish(result.skus)
    except (DecodeLimit, RecursionError, ValueError) as exc:
        result.skus = []
        result.specification_images = []
        result.status = "parse_failed"
        result.reason = str(exc) if isinstance(exc, DecodeLimit) else "invalid_structure"
    return result
