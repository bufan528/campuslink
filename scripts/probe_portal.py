"""只读页面结构探测工具。

用途：当认证页面结构可能变化时，先只读检查现场页面，再决定是否调整适配器。

铁律：
* **只发 GET**，绝不提交账号密码；
* 不打印 Cookie / Set-Cookie；
* 只访问 auth.endpoints 白名单里的主机（默认是 RFC 5737 示例地址）。

用法::

    python scripts/probe_portal.py
    python scripts/probe_portal.py --host 192.0.2.11 --paths /,/0.htm
    python scripts/probe_portal.py --save .precheck
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from campuslink.auth import endpoints, markers  # noqa: E402
from campuslink.auth.portal import parse_login_page  # noqa: E402

REDIRECTS = {301, 302, 303, 307, 308}
SAFE_HIDDEN_NAMES = {"0MKKey", "DDDDD", "upass", "vlan", "wlan", "wlan_user_ip"}


def probe(host: str, paths: list[str], timeout: float, save_dir: Path | None) -> int:
    import requests

    try:
        endpoints.require_portal(host)
    except Exception as exc:
        print("拒绝探测：", exc)
        print("只允许白名单主机：", ", ".join(sorted(endpoints.ALLOWED_HOSTS)))
        return 2
    session = requests.Session()
    session.trust_env = False
    exit_code = 0
    for path in paths:
        url = endpoints.build_url(host, path)
        print("=" * 70)
        print("GET", url)
        try:
            response = session.get(url, timeout=timeout, allow_redirects=False)
        except Exception as exc:
            print("  结果: 不可达 (" + type(exc).__name__ + ")")
            exit_code = 1
            continue

        print("  HTTP:", response.status_code)
        print("  Content-Type:", response.headers.get("Content-Type", "-"))
        print("  Content-Length:", len(response.content or b""))
        if response.status_code in REDIRECTS:
            location = response.headers.get("Location", "")
            print("  Location:", location)
            try:
                endpoints.require_same_origin(location)
                print("  重定向目标: 白名单内")
            except Exception:
                print("  重定向目标: **白名单外**（本工具不会跟过去）")
            continue
        if response.status_code >= 400:
            exit_code = 1
            continue

        # 用适配器同一套解码与解析逻辑，确保"看到的就是程序看到的"。
        from campuslink.auth.models import HttpResponse

        view = HttpResponse(
            status=response.status_code,
            url=str(response.url),
            headers={k: v for k, v in response.headers.items()},
            body=response.content or b"",
        )
        text, encoding = view.decode()
        print("  实际解码:", encoding)

        page = parse_login_page(text, encoding)
        fingerprint = page.fingerprint
        print("  表单 action:", fingerprint.action)
        print("  表单 method:", fingerprint.method)
        print("  字段集合:", sorted(fingerprint.field_names) or "(空)")
        print("  有账号字段 DDDDD:", fingerprint.has_account_field)
        print("  有密码字段 upass:", fingerprint.has_password_field)
        print("  可识别为登录页:", page.is_recognized)
        if page.hidden_values:
            shown = {
                name: value
                for name, value in page.hidden_values.items()
                if name in SAFE_HIDDEN_NAMES
            }
            print("  已知隐藏字段:", shown or "(无)")
            print("  隐藏字段名(全部):", sorted(page.hidden_values))

        already = markers.looks_like_online_page(text, fingerprint.has_password_field)
        print("  像已登录页:", already)
        outcome, reason = markers.classify_login_body(text)
        print("  正文标记判定:", outcome.value, "|", reason)

        titles = [token for token in ("注销", "自助服务", "Dr.COM", "登录", "密码") if token in text]
        print("  正文出现关键词:", titles or "(无)")

        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            target = save_dir / ("real-" + (path.strip("/") or "root").replace("/", "_") + ".html")
            target.write_text(text, encoding="utf-8")
            print("  已保存原始页面:", target)
    session.close()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读检查校园网认证页面结构（不提交凭据）")
    parser.add_argument("--host", default=endpoints.DEFAULT_HOST)
    parser.add_argument("--paths", default="/0.htm,/,/a30.htm")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--save", type=Path, default=None)
    args = parser.parse_args(argv)
    paths = [item.strip() for item in args.paths.split(",") if item.strip()]
    return probe(args.host, paths, args.timeout, args.save)


if __name__ == "__main__":
    raise SystemExit(main())
