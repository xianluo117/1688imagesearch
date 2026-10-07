"""Inspect public upstream JavaScript in memory, never authenticated content."""
import re
from curl_cffi import requests

URL = "https://o.alicdn.com/1688-pc/pc-dynamic-sc/index.js"


def main():
    response = requests.get(URL, timeout=20)
    text = response.text
    print("asset_status", response.status_code, "bytes", len(response.content))
    for match in list(re.finditer(r"Price25|mainPrice|priceAmount|skuMapOriginal|formatPrice|currency|¥|￥|\\\\u5143|\\\\uffe5", text))[:30]:
        print(text[max(0, match.start() - 500):match.end() + 900])
    for match in list(re.finditer(r"main-price|mainPrice|sku-selection|skuSelection|cbu-pc-od|upkg", text))[-25:]:
        print("registry", text[max(0, match.start()-200):match.end()+400])
    print("bundles", re.findall(r"https?[^\s\"']+\.js", text)[:20])


if __name__ == "__main__":
    main()
