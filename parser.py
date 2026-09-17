"""Parse SKU arrays embedded in page HTML; never execute page source."""
import json
import re


def extract_skus(source):
    decoder = json.JSONDecoder()
    # Handle both plain JSON and JSON embedded in quoted strings.
    layers = [source]
    for _ in range(3):
        previous = layers[-1]
        decoded = previous.replace('\\"', '"').replace('\\\\', '\\')
        if decoded == previous:
            break
        layers.append(decoded)
    for text in layers:
        for match in re.finditer(r'"pieceWeightScaleInfo"\s*:\s*', text):
            try:
                rows, _ = decoder.raw_decode(text[match.end():])
            except ValueError:
                continue
            if not isinstance(rows, list):
                continue
            result = {}
            for row in rows:
                if not isinstance(row, dict) or row.get('skuId') is None:
                    continue
                fields = sorted((k for k in row if re.fullmatch(r'sku\d+', k)), key=lambda k: int(k[3:]))
                attributes = [{'source_field': k, 'value': str(row[k])} for k in fields if row[k] not in (None, '')]
                if attributes:
                    sku_id = str(row['skuId'])
                    result[sku_id] = {'sku_id': sku_id, 'attributes': attributes}
            if result:
                return list(result.values())
    return []
