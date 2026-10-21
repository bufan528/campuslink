# CampusLink · Campus 校园网自动连接助手

一个**本地运行、个人自用**的 Windows 校园网自动认证工具。它把所在单位提供的正常 Web 认证流程封装成桌面程序：
不用每次打开浏览器输密码，并提供系统托盘、掉线自动重连、开机自启等可选功能。

> **合规声明（请先读）**
>
> - 本项目仅用于**本人账号**在本人电脑上完成所在单位提供的正常 Web 认证，**不**绕过认证、**不**破解、**不**修改计费、**不**共享账号、**不**干扰他人会话；
> - 本项目与你就读单位及任何网络服务提供方**无隶属或授权关系**，不包含任何单位内部资料、页面副本、抓包数据、账号数据或接口文档；
> - 认证门户地址、SSID 等本地信息由使用者按所在单位公开说明**自行填写在本地私有文件中**，不进入版本库；
> - 请遵守你所在单位的网络使用规定；使用本工具产生的任何后果由使用者自行承担。
>
> 如果你不接受以上任何一条，请不要使用或分发本项目。

---

## 1. 功能

- **一键登录**：向配置的校园网门户提交单位本来就要求提交的 Web 表单；
- **状态区分**：未连接 / 需要认证 / 登录中 / 认证成功 / 互联网不可用 / 认证失败；
- **独立联网验证**：不把"POST 返回 200"当作登录成功；联网探测优先使用 HTTPS 强证据，避免被 HTTP 探测放行误判；\n- **僵死会话修复**：门户显示在线但外网实际不可用时，自动重连会强制注销并重新认证；
- **系统托盘**：显示状态、手动登录、暂停自动重连、退出；
- **自动重连（默认关闭）**：指数退避 + 失败上限 + 可随时暂停，且开关**即时生效**；
- **开机自启（默认关闭）**：只写当前用户 `HKCU\...\Run`，不装服务、不改计划任务；
- **凭据安全**：密码只存 Windows 凭据管理器（可选），不写明文文件、不进日志；
- **本地诊断日志**：只记录状态、错误类型、时间；账号和密码一律脱敏。

## 2. 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11（x64） |
| Python | 3.10 及以上（源码运行） |
| 网络 | 能访问你所在单位的校园网门户 |
| 权限 | 普通用户即可；无需管理员 |

## 3. 安装（源码运行）

在 PowerShell 中执行：

```powershell
git clone https://github.com/bufan528/campuslink.git
cd campuslink

# 建议使用项目内虚拟环境，避免 Windows 长路径限制
python -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pip install -e .
```

如果你不想安装为包，也可以只对当前 PowerShell 会话设置 `PYTHONPATH`：

```powershell
$env:PYTHONPATH = "src"
```

> `pip install -e .` 之后，`python -m campuslink` 和开机自启的源码模式都能正常找到包。
> 只设置 `PYTHONPATH` 在关闭窗口后失效。

## 4. 本地配置（重要）

仓库**故意不包含**任何真实校园网地址、SSID 或协议细节。第一次使用前必须在本机完成两步：

### 4.1 配置认证门户地址

```powershell
Copy-Item src\campuslink\auth\endpoints_local.example.py `
          src\campuslink\auth\endpoints_local.py
```

打开 `src\campuslink\auth\endpoints_local.py`，按你所在单位**公开**的网络说明填写：

```python
PORTALS = {
    "192.0.2.10": PortalDefinition(     # ← 换成门户的 IPv4 地址
        host="192.0.2.10",              # ← 与字典键一致
        label="我的校园网入口",          # ← 仅用于界面显示
        login_paths=("/0.htm",),        # ← 登录页路径
        logout_path="/F.htm",           # ← 注销路径（不知道可留默认）
        is_default=True,
        evidence_level="local",
        note="本地配置",
    ),
}
```

规则：

- `host` 必须是**字面 IPv4**，只支持 `http://` + 端口 `80`；
- `login_paths` 与 `logout_path` 必须以 `/` 开头；
- 该文件已在 `.gitignore` 中，**不要提交**，也不要把真实地址发到公开仓库；
- 未创建该文件时，程序只会使用 RFC 5737 文档示例地址（`192.0.2.0/24`），不会连接任何真实网络。

### 4.2 配置 Wi-Fi 名称（可选）

程序默认用 `campus-wlan` 判断是否连着校园无线网。名称不同时，在配置文件里修改：

```json
{
  "wifi_ssid_candidates": ["你的校园网 SSID", "备用 SSID"]
}
```

配置文件路径：

```text
%APPDATA%\CampusLink\config.json
```

也可以只插网线：Wi-Fi 名称不影响认证主流程，最终是否提交由“门户是否可达且页面可识别”决定。

## 5. 使用

### 5.1 先做只读检测（不提交凭据）

```powershell
.venv\Scripts\python.exe -m campuslink --check
```

输出会区分三件事：是否连着目标校园网、门户状态、互联网是否真的可用。

退出码（方便脚本/巡检）：

| 退出码 | 含义 |
|---|---|
| `0` | 已认证 |
| `1` | 需要认证 |
| `2` | 校园网可达但外网不可用 / 认证失败 |
| `3` | 未连接或门户不可达 |
| `4` | 无法判定 |

### 5.2 启动图形界面

```powershell
.venv\Scripts\python.exe -m campuslink
```

首次使用流程：

1. 连接校园网 Wi-Fi 或插网线，确保“自动获取 IP”；
2. 打开程序，输入账号与密码；
3. 点“一键登录”；
4. 看状态：
   - **认证成功**：外网可用；
   - **认证结果未确认**：门户返回了未知标记，程序不会假装成功；
   - **认证失败**：检查账号密码/欠费/门户是否改版；
5. 需要记住密码时勾选“保存到 Windows 凭据管理器”，再登录；
6. 关闭窗口会最小化到托盘，不会退出；退出请用托盘菜单“退出”。

### 5.3 命令行选项

```text
python -m campuslink                 图形界面
python -m campuslink --check         只读检测（不提交凭据）
python -m campuslink --diagnostics   打印配置路径、日志目录、当前设置
python -m campuslink --minimized     启动后直接进托盘
python -m campuslink --config PATH   指定配置文件
python -m campuslink --log-level     日志级别（默认 INFO）
```

### 5.4 自动重连

默认**关闭**。在界面勾选“掉线时自动重连”后：

- 立即启动后台重连线程，不需要重启程序；
- 只在“确实需要认证”时提交凭据；门户不可达/页面不认识时只等待；
- 失败按指数退避重试，达到失败上限后停手；
- 可随时在托盘菜单暂停/恢复；
- 取消勾选会立刻停止后台线程。

> 自动重连会恢复后台应用的数据传输；如果你的校园网按流量计费，请留意费用。

### 5.5 开机自启

默认**关闭**。勾选后会写入当前用户：

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

不需要管理员权限，不创建服务/计划任务。取消勾选会删除该值。

### 5.6 注销与凭据

- “注销”会向配置的注销路径发送请求；只有命中明确成功标记才显示“注销成功”，否则显示“结果未确认”；
- “清除已保存凭据”会删除 Windows 凭据管理器中的条目；
- 程序不会把密码写入 `config.json`、日志或任何明文文件。

### 5.7 日志与诊断

```text
配置：%APPDATA%\CampusLink\config.json
日志：%LOCALAPPDATA%\CampusLink\logs\campuslink.log
```

日志按大小滚动（约 1 MB × 5 份）。账号最多显示前两位 + `***`，密码/表单字段/Cookie 一律脱敏。

## 6. 构建 EXE

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean campuslink.spec
```

或：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

产物：`dist\CampusLink.exe`（单文件、无控制台窗口）。

注意：

- EXE 为窗口子系统程序，PowerShell 直接运行 `--check` 不会等待/显示文本；请用源码方式运行，或 `cmd /c` / `Start-Process -Wait`；
- 未签名，SmartScreen 可能提示“未知发布者”；
- 构建脚本不会修改系统设置、不写注册表、不装服务。

## 7. 测试

```powershell
.venv\Scripts\python.exe -m pytest
```

- 单元测试全部使用虚构凭据与本地 Mock HTTP 服务器，不访问真实校园网、不提交真实密码；
- 包含真实 HTTP（`127.0.0.1` Mock 门户）与 Qt offscreen GUI 测试；
- 未构建 `dist\CampusLink.exe` 时，EXE 相关用例会自动跳过。

## 8. 目录结构

```text
src/campuslink/
  app.py                  入口与装配（CLI/诊断/GUI）
  auth/                   白名单、页面解析、响应标记、认证适配器
    endpoints_local.example.py  本地门户配置模板（复制为 endpoints_local.py 使用）
  network/                SSID 探测、连通性探测、状态推导、退避状态机
  services/               认证编排、自动重连线程与开关
  credentials/store.py    Windows 凭据管理器 / 内存 / 空实现
  config/                 路径与设置（白名单校验、数值边界）
  logging_setup/          滚动日志 + 脱敏
  autostart/registry.py   HKCU Run 开关（后端可注入）
  ui/                     视图模型、Qt 窗口与托盘
tests/                    单元测试 + 本地 Mock 门户 + offscreen GUI 测试
scripts/                  构建脚本、只读探测、打包入口、版本资源
docs/known-limitations.md 已知限制
```

## 9. 隐私与数据

| 项 | 说明 |
|---|---|
| 遥测 | **无**。程序不向任何第三方服务器上报数据 |
| 凭据 | 可选保存到 Windows 凭据管理器；不写明文文件 |
| 日志 | 账号/密码/表单字段/Cookie 双重脱敏；不记录请求正文 |
| 配置 | 只保存非敏感设置；保存前强制做敏感字段检查 |
| 网络目标 | 只与你本地配置的 IPv4 门户通信；不读系统代理、不关闭 TLS 校验、不改证书/防火墙 |
| 账号 | 仅本人账号；不扫描、不收集、不上传任何账号信息 |

## 10. 常见问题

**Q：`--check` 显示门户 unreachable/unrecognized？**
A：先确认已连校园网；再确认 `endpoints_local.py` 里的地址/路径正确；门户改版时请以页面实际结构为准（不要提交真实页面）。

**Q：登录显示“认证结果未确认”？**
A：这是安全兜底。门户返回了程序不认识的响应标记。请记录状态与时间，不要反复重试；可在本地更新 `src/campuslink/auth/markers.py` 中的标记。

**Q：自动重连一直不提交？**
A：检查是否已保存凭据、是否连着校园网、门户是否可达；查看日志中的“未提交凭据”提示。

**Q：关闭窗口后程序还在？**
A：这是最小化到托盘的设计；用托盘菜单“退出”才会真正结束。

**Q：忘记密码/凭据想清除？**
A：界面点“清除已保存凭据”，或在 Windows「凭据管理器 → Windows 凭据」删除 `CampusLink/portal`。

**Q：EXE 被杀毒软件/SmartScreen 拦截？**
A：EXE 未签名，可能被误报。可改用源码运行，或自行构建；不要为此关闭系统安全防护。

**Q：卸载？**
A：删除程序目录；在界面取消开机自启；清除保存的凭据；删除 `%APPDATA%\CampusLink` 与 `%LOCALAPPDATA%\CampusLink`。

## 11. 已知限制

见 [docs/known-limitations.md](docs/known-limitations.md)。要点：

- 登录成功/失败响应标记需要按你单位门户的实际响应校准；
- 注销、备用入口、认证后 Cookie 行为等以实际环境为准；
- 仅支持 Windows；
- 自动重连基于逻辑探测，不监听底层链路事件；
- EXE 未签名。

## 12. 许可与使用边界

- 本仓库**不附带开源许可证**，保留所有权利；仅供个人学习与本人账号自用；
- 不得用于绕过认证、破解、共享账号、商业分发或其他违法违规用途；
- 第三方依赖（`requests`、`PySide6`、`pywin32` 等）遵循其各自许可证；
- 本仓库不包含单位文档、页面副本、抓包数据或接口资料；如你认为某处内容不当，请提交 issue 或联系维护者删除。
