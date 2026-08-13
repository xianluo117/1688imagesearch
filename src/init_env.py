from __future__ import annotations

import argparse
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def build_env_content() -> str:
    cookie_upload_api_key = secrets.token_urlsafe(32)
    search_api_key = secrets.token_urlsafe(32)
    encryption_key = Fernet.generate_key().decode("ascii")
    return f"""# 由 src/init_env.py 自动生成。禁止提交此文件。
COOKIE_UPLOAD_API_KEY={cookie_upload_api_key}
SEARCH_API_KEY={search_api_key}
COOKIE_ENCRYPTION_KEY={encryption_key}

DATABASE_PATH=data/image-search.db
UPLOAD_WORKER_COUNT=2
PRODUCT_WORKER_COUNT=2
MAX_QUEUED_TASKS=100
UPLOAD_TASK_TIMEOUT_SECONDS=240
PRODUCT_TASK_TIMEOUT_SECONDS=180
TASK_RETENTION_SECONDS=86400

DOWNLOAD_CONNECT_TIMEOUT=10
DOWNLOAD_TIMEOUT=30
MAX_IMAGE_BYTES=8388608

SEARCH_HTTP_TIMEOUT=90
SEARCH_NETWORK_RETRIES=1
SEARCH_READY_RETRIES=8
SEARCH_READY_INTERVAL=1.5

API_HOST=127.0.0.1
API_PORT=8000
FORWARDED_ALLOW_IPS=127.0.0.1
LOG_LEVEL=INFO
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 1688 图片搜索 API 的 .env 配置")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help="输出路径，默认项目根目录 .env",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖已有文件并重新生成全部密钥",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    if output.exists() and not args.force:
        print(f"配置文件已存在，未覆盖: {output}")
        print("如需重新生成全部密钥，请使用 --force。")
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_env_content(), encoding="utf-8", newline="\n")
    print(f"配置文件已生成: {output}")
    print("COOKIE_UPLOAD_API_KEY：配置到油猴脚本，用于上传 Cookie。")
    print("SEARCH_API_KEY：配置到查询调用方，用于创建和查询任务。")
    print("COOKIE_ENCRYPTION_KEY：仅由 API 服务使用，禁止泄露或随意更换。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
