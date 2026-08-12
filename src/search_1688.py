from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from image_search import ImageSearchClient, SearchOptions
from image_search.errors import (
    AuthenticationError,
    ProtocolError,
    RateLimitError,
    RiskControlError,
    TokenError,
)

EXIT_PROTOCOL = 2
EXIT_AUTH = 3
EXIT_TOKEN = 4
EXIT_RISK = 5
EXIT_RATE_LIMIT = 6


def json_object(value: str) -> dict[str, Any]:
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"不是有效 JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise argparse.ArgumentTypeError("必须提供 JSON 对象")
    return result


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("不能小于 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="1688 图片搜索纯 HTTP 协议客户端")
    parser.add_argument("--cookie-file", required=True, help="油猴导出的 Cookie JSON 或 Cookie 字符串文件")
    parser.add_argument("--image", required=True, help="本地 JPEG、PNG、GIF 或 WebP 图片")
    parser.add_argument("--output", default="outputs/image-search-result.json", help="结果 JSON 路径")
    parser.add_argument("--page-size", type=positive_int, default=60, help="首屏请求数量，默认 60")
    parser.add_argument("--limit", type=positive_int, default=3, help="输出头部商品数量，默认 3")
    parser.add_argument("--ready-retries", type=int, default=5, help="上传后等待首屏就绪的重试次数")
    parser.add_argument("--interval", type=non_negative_float, default=1.0, help="首屏未就绪时的重试间隔秒数")
    parser.add_argument("--timeout", type=non_negative_float, default=30.0, help="单次 HTTP 超时秒数")
    parser.add_argument("--network-retries", type=int, default=2, help="网络错误重试次数")
    parser.add_argument("--max-image-mb", type=non_negative_float, default=8.0, help="图片体积上限 MiB")
    parser.add_argument("--exclude-ads", action="store_true", help="排除广告商品")
    parser.add_argument("--include-raw-item", action="store_true", help="在结果中保留商品原始节点")
    parser.add_argument("--raw-response-dir", help="可选：保存每次 MTOP 原始响应的目录")
    parser.add_argument(
        "--extra-params",
        type=json_object,
        default={},
        help='附加或覆盖图搜 params，例如 \'{"sortType":"default"}\'',
    )
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    return parser


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def secure_output_permissions(path: Path) -> None:
    if os.name == "posix":
        path.chmod(0o600)


def run(args: argparse.Namespace) -> int:
    options = SearchOptions(
        page_size=args.page_size,
        result_limit=args.limit,
        ready_retries=max(0, args.ready_retries),
        request_interval=args.interval,
        exclude_ads=args.exclude_ads,
        include_raw_item=args.include_raw_item,
        max_image_bytes=int(args.max_image_mb * 1024 * 1024),
        raw_response_dir=Path(args.raw_response_dir) if args.raw_response_dir else None,
        extra_params=args.extra_params,
    )
    client = ImageSearchClient(
        args.cookie_file,
        options=options,
        timeout=args.timeout,
        network_retries=max(0, args.network_retries),
    )
    result = client.search(args.image)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result.to_dict(include_raw=args.include_raw_item), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    secure_output_permissions(output)
    logging.getLogger(__name__).info(
        "完成: image_id=%s pages=%d products=%d stop=%s output=%s",
        result.upload.image_id,
        result.pages_requested,
        len(result.products),
        result.stop_reason,
        output,
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)
    try:
        return run(args)
    except AuthenticationError as exc:
        logging.error("登录态无效，请重新导出 Cookie: %s", exc)
        return EXIT_AUTH
    except TokenError as exc:
        logging.error("MTOP Token 无效，请重新导出 Cookie: %s", exc)
        return EXIT_TOKEN
    except RiskControlError as exc:
        logging.error("请求触发验证码或风控，请在本机完成验证后重新导出 Cookie: %s", exc)
        return EXIT_RISK
    except RateLimitError as exc:
        logging.error("请求被限流，请降低频率后重试: %s", exc)
        return EXIT_RATE_LIMIT
    except ProtocolError as exc:
        logging.error("协议执行失败: %s", exc)
        return EXIT_PROTOCOL
    except KeyboardInterrupt:
        logging.warning("用户中止")
        return 130


if __name__ == "__main__":
    sys.exit(main())
