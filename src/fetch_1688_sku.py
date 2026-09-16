"""Standalone CLI. Never prints page bodies, cookie values or exception messages."""
import argparse
import json
import sys
from pathlib import Path

from product_sku.urls import normalize_url


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "参数无效；使用 --help 查看说明。\n")


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description="独立1688详情页SKU采集（无浏览器/MTOP兜底）")
    parser.add_argument("--url", required=True, help="标准1688商品详情链接")
    parser.add_argument("--cookie-file", required=True, type=Path, help="已有Cookie文件；不自动搜索配置")
    parser.add_argument("--output", type=Path, help="结果JSON路径；不指定则写标准输出")
    parser.add_argument("--timeout", type=float, default=20, help="每次请求超时秒数，默认20")
    parser.add_argument("--retries", type=int, default=1, help="网络重试次数，0至2，默认1")
    args = parser.parse_args(argv)
    try:
        normalize_url(args.url)
        if args.output and args.output.resolve() == args.cookie_file.resolve():
            raise ValueError
    except ValueError:
        print("参数无效：请检查商品链接或输出路径。", file=sys.stderr)
        return 2
    try:
        from product_sku.client import ProductSkuClient
        with ProductSkuClient(args.cookie_file, timeout=args.timeout, network_retries=args.retries) as client:
            result = client.fetch(args.url)
    except ImportError:
        print("缺少项目运行依赖。", file=sys.stderr)
        return 2
    except ValueError:
        print("初始化失败：请检查Cookie文件及请求参数。", file=sys.stderr)
        return 2
    except Exception:
        print("采集失败：客户端内部错误（详细信息已隐藏）。", file=sys.stderr)
        return 2
    try:
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
    except (OSError, UnicodeError):
        print("结果写入失败。", file=sys.stderr)
        return 2
    return 0 if result.ok else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
