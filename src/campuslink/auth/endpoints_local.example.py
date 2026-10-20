"""本地私有门户配置示例（复制为 endpoints_local.py 后按需修改）。

注意：
* ``endpoints_local.py`` 已被 .gitignore 忽略，**不要提交**；
* 地址/路径应来自你就读单位公开提供的网络使用说明；
* host 必须是字面 IPv4，只支持 http + 80 端口；
* 本文件仅作模板，默认使用 RFC 5737 文档地址，不会连到真实主机。

复制后把下面的 PORTALS 改成自己的入口即可：

    Copy-Item src\\campuslink\\auth\\endpoints_local.example.py `
              src\\campuslink\\auth\\endpoints_local.py

然后修改 host / login_paths / logout_path / is_default。
"""

from __future__ import annotations

from .endpoints import PortalDefinition

PORTALS: dict[str, PortalDefinition] = {
    "192.0.2.10": PortalDefinition(
        host="192.0.2.10",
        label="我的校园网入口",
        login_paths=("/0.htm",),
        logout_path="/F.htm",
        is_default=True,
        evidence_level="local",
        note="本地配置，请替换为公开的入口地址与路径。",
    ),
}
