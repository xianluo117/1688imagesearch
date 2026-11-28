"""SKU-only API key configuration and scheduling policy."""
from dataclasses import dataclass, field
import json


@dataclass(frozen=True)
class SkuPolicy:
    priority: int = 0
    weight: int = 1


@dataclass(frozen=True)
class SkuClientKey:
    name: str
    secret: str = field(repr=False)
    policy: SkuPolicy


def parse_sku_keys(raw: str) -> tuple[SkuPolicy, tuple[SkuClientKey, ...]]:
    def policy(entry):
        priority, weight = entry.get("priority", 0), entry.get("weight", 1)
        if type(priority) is not int or priority < 0 or type(weight) is not int or weight < 1:
            raise ValueError
        return SkuPolicy(priority, weight)

    try:
        entries = json.loads(raw)
        if not isinstance(entries, dict):
            raise ValueError
        default = entries.pop("default", {})
        if not isinstance(default, dict) or default.keys() - {"priority", "weight"}:
            raise ValueError
        clients = []
        for name, entry in entries.items():
            if not name.strip() or not isinstance(entry, dict) or entry.keys() - {"key", "priority", "weight"}:
                raise ValueError
            secret = entry.get("key")
            if not isinstance(secret, str) or len(secret) < 24 or not all(33 <= ord(c) <= 126 for c in secret):
                raise ValueError
            clients.append(SkuClientKey(name, secret, policy(entry)))
        return policy(default), tuple(clients)
    except ValueError:
        raise ValueError("SKU_API_KEYS 须为有效的 JSON 配置：优先级为非负整数，权重为正整数，专用 Key 至少 24 位可见 ASCII 字符") from None
