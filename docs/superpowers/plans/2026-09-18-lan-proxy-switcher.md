# 局域网代理自动发现与网卡切换工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做出一个 Windows 桌面小工具 `LANProxySwitcher.exe`，能独占式切换物理网卡、用纯 TCP Connect 扫描局域网的 7890/1080 端口、自动写入当前用户的 Windows 系统代理，并持续监控、失效后自动重扫。

**Architecture:** 8 个模块分两层——`scanner.py` / `config.py` 是纯逻辑（无 Windows 依赖），`network.py` / `proxy.py` 是薄薄的 Windows 适配层（PowerShell 子进程 + winreg），`controller.py` 是唯一的状态机，串行消费一个 `inbox` 队列因而全程无锁，`gui.py` 只在 Tkinter 主线程里通过 `root.after` 泵 `ui_queue`。所有跨模块依赖走 `typing.Protocol`，测试用 Fake 注入。

**Tech Stack:** Python 3.14.3、标准库 only（`tkinter` / `winreg` / `ctypes` / `socket` / `subprocess` / `concurrent.futures`）、Windows PowerShell 5.1、pytest（仅开发）、PyInstaller（仅打包）。

**Spec:** `docs/superpowers/specs/2026-09-18-lan-proxy-switcher-design.md`

## Global Constraints

以下是全局要求，**每个任务的验收条件都隐含包含本节**：

- **代理判定唯一依据是 TCP Connect 能否建立**。禁止发送 HTTP GET、禁止发送 CONNECT、禁止请求任何 URL、禁止验证状态码 / 互联网可达性 / DNS / HTTPS / 公网 IP。监控阶段同样只做 TCP Connect。
- **运行时零第三方依赖**。`pytest`、`pyinstaller` 只装在开发环境，不得进入打包产物，不得被 `lan_proxy_switcher/` 下任何模块 import。
- **不做 TUN，不安装任何网络驱动**。
- **只写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`**，不碰 HKLM。
- **只扫描本机已启用网卡对应的私有网段**（`10/8`、`172.16/12`、`192.168/16`）。不扫描公网，不接受用户输入的任意网段。
- **工作线程中不得出现任何 `tkinter` 调用**。GUI 控件只在 Tkinter 主线程的 `_pump` 里更新。
- **worker 线程中任何异常都不得静默消失**，一律转成事件回投 `inbox` 并记日志。
- 端口默认 `[7890, 1080]`，优先级 `7890 > 1080`，同端口比 TCP 建连耗时。
- 配置文件 JSON 键名用 camelCase（`preferPort`、`scanTimeout`…），Python 侧字段用 snake_case。
- 所有面向用户的日志与 GUI 文案用中文。

**每个任务结束时 `python -m pytest` 全绿才算完成**，不得只跑本任务的测试。

---

### Task 1: 项目骨架与打包风险前置

规格第 15 节要求：在写任何功能代码之前先跑通一次 PyInstaller 打包。开发机是 Python 3.14.3，属较新版本，必须先确认 PyInstaller 支持它，而不是等功能写完才发现打不出来。

**Files:**
- Create: `lan_proxy_switcher/__init__.py`
- Create: `main.py`
- Create: `requirements-dev.txt`
- Create: `pyproject.toml`
- Create: `.gitignore`
- Test: `tests/test_skeleton.py`

**Interfaces:**
- Consumes: 无
- Produces: 包 `lan_proxy_switcher`（版本号 `__version__: str`）；入口 `main.main() -> None`；已验证可用的打包命令

- [ ] **Step 1: 建虚拟环境并装开发依赖**

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install pytest pyinstaller
.venv/Scripts/python.exe -m pip freeze | grep -Ei 'pytest|pyinstaller'
```

把上一条命令输出的两行版本号写进 `requirements-dev.txt`（连同注释）：

```
# 仅开发/打包使用，不得进入运行时依赖
pytest==<填实际版本>
pyinstaller==<填实际版本>
```

如果 `pip install pyinstaller` 在 Python 3.14 上失败，**立即停下来报告**，不要绕过——这正是本任务要前置暴露的风险。

- [ ] **Step 2: 写骨架文件**

`.gitignore`：

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
build/
dist/
*.spec
config.json
```

`pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

`lan_proxy_switcher/__init__.py`：

```python
"""局域网代理自动发现与网卡切换工具。"""

__version__ = "0.1.0"
```

`main.py`：

```python
"""LANProxySwitcher 入口。"""

from __future__ import annotations

import tkinter as tk

from lan_proxy_switcher import __version__


def main() -> None:
    root = tk.Tk()
    root.title(f"LANProxySwitcher {__version__}")
    root.geometry("900x700")
    tk.Label(root, text="骨架窗口：打包验证用").pack(expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 写测试并确认它先失败**

`tests/test_skeleton.py`：

```python
import lan_proxy_switcher


def test_package_exposes_version():
    assert lan_proxy_switcher.__version__ == "0.1.0"


def test_runtime_package_has_no_third_party_imports():
    """运行时模块只许用标准库。"""
    import pathlib
    import re

    banned = re.compile(r"^\s*(import|from)\s+(pytest|PyInstaller|pyinstaller)\b", re.M)
    for path in pathlib.Path("lan_proxy_switcher").rglob("*.py"):
        assert not banned.search(path.read_text(encoding="utf-8")), path
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_skeleton.py -v`
Expected: 先 FAIL（`ModuleNotFoundError: lan_proxy_switcher`），补齐 Step 2 的文件后 PASS。

- [ ] **Step 4: 跑通一次完整打包**

```bash
.venv/Scripts/python.exe -m PyInstaller --onefile --windowed --uac-admin --name LANProxySwitcher main.py
ls -l dist/LANProxySwitcher.exe
```

Expected: 生成 `dist/LANProxySwitcher.exe`。

- [ ] **Step 5: 手动验证 exe 能起来**

双击 `dist/LANProxySwitcher.exe`：应弹出 UAC 提示（`--uac-admin` 的预期行为），同意后出现 900×700 的骨架窗口。关闭窗口，进程应干净退出。

**若 exe 启动即闪退或报 Tk 相关错误，停下来报告**——这是打包路线的否决性问题，后续任务全部依赖它。

- [ ] **Step 6: Commit**

```bash
git add .gitignore pyproject.toml requirements-dev.txt main.py lan_proxy_switcher/__init__.py tests/test_skeleton.py
git commit -m "chore: 项目骨架并验证 PyInstaller 在 Python 3.14 上可打包"
```

---

### Task 2: config.py 配置加载

**Files:**
- Create: `lan_proxy_switcher/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `Config` 冻结数据类，字段：`ports: tuple[int, ...]`、`prefer_port: int`、`scan_timeout_ms: int`、`scan_concurrency: int`、`monitor_interval_s: int`、`proxy_check_failures: int`、`auto_scan: bool`、`auto_set_proxy: bool`、`disable_proxy_when_unavailable: bool`、`restore_proxy_on_exit: bool`
  - `config_path() -> pathlib.Path`
  - `load(path: Path, log: Callable[[str], None] = ...) -> Config`
  - `save(path: Path, cfg: Config) -> None`
  - 后续所有任务都通过这些 snake_case 字段名读配置

- [ ] **Step 1: 写失败的测试**

`tests/test_config.py`：

```python
import json

from lan_proxy_switcher import config


def test_missing_file_writes_defaults(tmp_path):
    path = tmp_path / "config.json"
    cfg = config.load(path)

    assert cfg.ports == (7890, 1080)
    assert cfg.prefer_port == 7890
    assert cfg.scan_timeout_ms == 500
    assert cfg.scan_concurrency == 100
    assert cfg.monitor_interval_s == 30
    assert cfg.proxy_check_failures == 3
    assert cfg.auto_scan is True
    assert cfg.auto_set_proxy is True
    assert cfg.disable_proxy_when_unavailable is False
    assert cfg.restore_proxy_on_exit is False

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["preferPort"] == 7890
    assert written["ports"] == [7890, 1080]
    assert written["restoreProxyOnExit"] is False


def test_partial_file_fills_missing_fields(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"monitorInterval": 10}), encoding="utf-8")

    cfg = config.load(path)

    assert cfg.monitor_interval_s == 10
    assert cfg.scan_timeout_ms == 500


def test_invalid_value_falls_back_and_logs(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"scanConcurrency": 0, "autoScan": "yes"}), encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg.scan_concurrency == 100
    assert cfg.auto_scan is True
    assert any("scanConcurrency" in line for line in lines)
    assert any("autoScan" in line for line in lines)


def test_unparsable_file_uses_all_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ 这不是 json", encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg == config.Config()
    assert any("无法解析" in line for line in lines)


def test_prefer_port_outside_ports_falls_back_to_first(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ports": [1080], "preferPort": 7890}), encoding="utf-8")
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg.ports == (1080,)
    assert cfg.prefer_port == 1080
    assert any("preferPort" in line for line in lines)


def test_bool_is_not_accepted_as_int(tmp_path):
    """True 在 Python 里是 int 的子类，必须显式挡掉。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"scanTimeout": True}), encoding="utf-8")

    cfg = config.load(path, lambda _line: None)

    assert cfg.scan_timeout_ms == 500
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: FAIL，`ModuleNotFoundError: lan_proxy_switcher.config`

- [ ] **Step 3: 写实现**

`lan_proxy_switcher/config.py`：

```python
"""JSON 配置的读取、校验与写回。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Config:
    ports: tuple[int, ...] = (7890, 1080)
    prefer_port: int = 7890
    scan_timeout_ms: int = 500
    scan_concurrency: int = 100
    monitor_interval_s: int = 30
    proxy_check_failures: int = 3
    auto_scan: bool = True
    auto_set_proxy: bool = True
    disable_proxy_when_unavailable: bool = False
    restore_proxy_on_exit: bool = False


# JSON 键（camelCase，规格第 12 节）↔ Config 字段（snake_case）
JSON_KEYS: dict[str, str] = {
    "ports": "ports",
    "preferPort": "prefer_port",
    "scanTimeout": "scan_timeout_ms",
    "scanConcurrency": "scan_concurrency",
    "monitorInterval": "monitor_interval_s",
    "proxyCheckFailures": "proxy_check_failures",
    "autoScan": "auto_scan",
    "autoSetProxy": "auto_set_proxy",
    "disableProxyWhenUnavailable": "disable_proxy_when_unavailable",
    "restoreProxyOnExit": "restore_proxy_on_exit",
}


def _is_plain_int(value: object) -> bool:
    # bool 是 int 的子类，必须显式排除
    return isinstance(value, int) and not isinstance(value, bool)


def _ports(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("必须是非空数组")
    result = []
    for item in value:
        if not _is_plain_int(item) or not 1 <= item <= 65535:
            raise ValueError(f"非法端口 {item!r}")
        result.append(item)
    return tuple(result)


def _int_range(low: int, high: int) -> Callable[[object], int]:
    def check(value: object) -> int:
        if not _is_plain_int(value) or not low <= value <= high:
            raise ValueError(f"必须是 {low}~{high} 之间的整数，得到 {value!r}")
        return int(value)

    return check


def _flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"必须是 true 或 false，得到 {value!r}")
    return value


VALIDATORS: dict[str, Callable[[object], object]] = {
    "ports": _ports,
    "prefer_port": _int_range(1, 65535),
    "scan_timeout_ms": _int_range(1, 60000),
    "scan_concurrency": _int_range(1, 1000),
    "monitor_interval_s": _int_range(1, 3600),
    "proxy_check_failures": _int_range(1, 100),
    "auto_scan": _flag,
    "auto_set_proxy": _flag,
    "disable_proxy_when_unavailable": _flag,
    "restore_proxy_on_exit": _flag,
}


def config_path() -> Path:
    """冻结运行时用 exe 同级目录；--onefile 会解压到临时目录，不能用 __file__。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.json"
    return Path(__file__).resolve().parent.parent / "config.json"


def to_json_dict(cfg: Config) -> dict[str, object]:
    out: dict[str, object] = {}
    for json_key, field_name in JSON_KEYS.items():
        value = getattr(cfg, field_name)
        out[json_key] = list(value) if isinstance(value, tuple) else value
    return out


def save(path: Path, cfg: Config) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_json_dict(cfg), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load(path: Path, log: Callable[[str], None] = lambda _line: None) -> Config:
    if not path.exists():
        cfg = Config()
        save(path, cfg)
        log(f"配置文件不存在，已写入默认配置：{path}")
        return cfg

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("顶层必须是 JSON 对象")
    except (OSError, ValueError) as exc:
        log(f"配置文件无法解析（{exc}），全部使用默认值")
        return Config()

    values: dict[str, object] = {}
    for json_key, field_name in JSON_KEYS.items():
        if json_key not in raw:
            continue
        try:
            values[field_name] = VALIDATORS[field_name](raw[json_key])
        except ValueError as exc:
            log(f"配置项 {json_key} 非法：{exc}，使用默认值")

    cfg = Config(**values)  # type: ignore[arg-type]
    if cfg.prefer_port not in cfg.ports:
        log(f"preferPort={cfg.prefer_port} 不在 ports={list(cfg.ports)} 中，改用 {cfg.ports[0]}")
        cfg = replace(cfg, prefer_port=cfg.ports[0])
    return cfg
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add lan_proxy_switcher/config.py tests/test_config.py
git commit -m "feat(config): JSON 配置加载、逐字段校验与默认值回退"
```

---

### Task 3: scanner.py 纯逻辑——目标枚举与代理选择

本任务只写纯函数，不碰 socket。规格第 7.2 / 7.4 节的安全闸门与选择策略全部落在这里，是整个项目测试密度最高的一块。

**Files:**
- Create: `lan_proxy_switcher/scanner.py`
- Test: `tests/test_scanner_logic.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `ScanHit` 冻结数据类：`ip: str`、`port: int`、`latency_ms: float`
  - `ScanRefused(Exception)`
  - `gateway_targets(gateway: str | None, ports: Sequence[int]) -> list[tuple[str, int]]`
  - `enumerate_targets(ipv4: str, prefix_length: int, ports: Sequence[int], max_hosts_prefix: int = 24) -> list[tuple[str, int]]`
  - `select_best(hits: Sequence[ScanHit], prefer_port: int, ports: Sequence[int]) -> ScanHit | None`

> **相对规格第 6 节签名的调整**：规格把 `gateway` 写进了 `enumerate_targets` 的参数。这里拆成 `gateway_targets` + `enumerate_targets` 两个函数，因为网关快路径与全网段枚举是两个独立阶段（规格第 7.3 节），拆开后各自都是可单独测试的纯函数。

- [ ] **Step 1: 写失败的测试**

`tests/test_scanner_logic.py`：

```python
import pytest

from lan_proxy_switcher.scanner import (
    ScanHit,
    ScanRefused,
    enumerate_targets,
    gateway_targets,
    select_best,
)


# ---------- 安全闸门（规格第 7.2 节第 1 条）----------

@pytest.mark.parametrize("ipv4", ["8.8.8.8", "1.1.1.1", "203.0.113.5"])
def test_refuses_public_addresses(ipv4):
    with pytest.raises(ScanRefused):
        enumerate_targets(ipv4, 24, (7890,))


def test_refuses_apipa():
    with pytest.raises(ScanRefused):
        enumerate_targets("169.254.25.234", 16, (7890,))


def test_refuses_loopback():
    with pytest.raises(ScanRefused):
        enumerate_targets("127.0.0.1", 8, (7890,))


@pytest.mark.parametrize(
    "ipv4,prefix",
    [("10.0.0.2", 24), ("172.20.10.7", 28), ("192.168.10.10", 24)],
)
def test_accepts_private_addresses(ipv4, prefix):
    assert enumerate_targets(ipv4, prefix, (7890,))


# ---------- 网段计算（规格第 7.2 节第 2、3 条）----------

def test_iphone_28_uses_real_mask():
    """iPhone 共享网络实测是 /28，不能按 /24 扫。"""
    targets = enumerate_targets("172.20.10.7", 28, (7890, 1080))

    ips = sorted({ip for ip, _ in targets}, key=lambda s: int(s.rsplit(".", 1)[1]))
    assert ips == [f"172.20.10.{i}" for i in range(1, 15) if i != 7]
    assert len(targets) == 13 * 2


def test_prefix_16_is_clamped_to_24():
    targets = enumerate_targets("10.1.2.3", 16, (7890,))

    assert len(targets) == 253  # 254 个主机地址减去本机
    assert all(ip.startswith("10.1.2.") for ip, _ in targets)


def test_own_ip_is_excluded():
    targets = enumerate_targets("192.168.10.10", 24, (7890,))

    assert ("192.168.10.10", 7890) not in targets


def test_network_and_broadcast_excluded():
    targets = enumerate_targets("192.168.10.10", 24, (7890,))
    ips = {ip for ip, _ in targets}

    assert "192.168.10.0" not in ips
    assert "192.168.10.255" not in ips


def test_prefix_32_yields_no_targets():
    assert enumerate_targets("192.168.1.5", 32, (7890,)) == []


def test_prefix_30_yields_only_the_peer():
    targets = enumerate_targets("192.168.1.5", 30, (7890, 1080))

    assert targets == [("192.168.1.6", 7890), ("192.168.1.6", 1080)]


def test_ports_are_expanded_per_ip_in_order():
    targets = enumerate_targets("192.168.1.5", 30, (7890, 1080))

    assert [port for _, port in targets] == [7890, 1080]


# ---------- 网关快路径（规格第 7.3 节阶段一）----------

def test_gateway_targets_expands_ports():
    assert gateway_targets("172.20.10.1", (7890, 1080)) == [
        ("172.20.10.1", 7890),
        ("172.20.10.1", 1080),
    ]


def test_gateway_targets_empty_when_no_gateway():
    assert gateway_targets(None, (7890, 1080)) == []


def test_gateway_targets_refuses_public_gateway():
    with pytest.raises(ScanRefused):
        gateway_targets("8.8.8.8", (7890,))


# ---------- 选择策略（规格第 7.4 节）----------

def test_prefer_port_wins_over_lower_latency_on_other_port():
    hits = [ScanHit("10.0.0.30", 1080, 5.0), ScanHit("10.0.0.20", 7890, 35.0)]

    assert select_best(hits, 7890, (7890, 1080)) == ScanHit("10.0.0.20", 7890, 35.0)


def test_lowest_latency_wins_within_the_same_port():
    hits = [ScanHit("10.0.0.30", 7890, 35.0), ScanHit("10.0.0.20", 7890, 10.0)]

    assert select_best(hits, 7890, (7890, 1080)).ip == "10.0.0.20"


def test_falls_back_to_next_port_when_prefer_port_absent():
    hits = [ScanHit("10.0.0.30", 1080, 15.0)]

    assert select_best(hits, 7890, (7890, 1080)).port == 1080


def test_select_best_returns_none_for_no_hits():
    assert select_best([], 7890, (7890, 1080)) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scanner_logic.py -v`
Expected: FAIL，`ModuleNotFoundError: lan_proxy_switcher.scanner`

- [ ] **Step 3: 写实现**

`lan_proxy_switcher/scanner.py`（本任务只写这部分，Task 4 会往同一文件追加扫描执行部分）：

```python
"""局域网扫描：目标枚举、并发 TCP Connect、结果选择。

本模块不含任何 Windows 相关代码，可在任意平台单测。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Sequence

PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class ScanRefused(Exception):
    """目标不在允许扫描的私有网段内（规格第 20 节安全边界）。"""


@dataclass(frozen=True)
class ScanHit:
    ip: str
    port: int
    latency_ms: float


def _require_private(ipv4: str) -> ipaddress.IPv4Address:
    try:
        address = ipaddress.IPv4Address(ipv4)
    except ipaddress.AddressValueError as exc:
        raise ScanRefused(f"{ipv4} 不是合法的 IPv4 地址") from exc
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise ScanRefused(f"{ipv4} 不在私有网段（10/8、172.16/12、192.168/16）内，拒绝扫描")
    return address


def gateway_targets(gateway: str | None, ports: Sequence[int]) -> list[tuple[str, int]]:
    if not gateway:
        return []
    _require_private(gateway)
    return [(gateway, port) for port in ports]


def enumerate_targets(
    ipv4: str,
    prefix_length: int,
    ports: Sequence[int],
    max_hosts_prefix: int = 24,
) -> list[tuple[str, int]]:
    """展开待扫描的 (ip, port)。

    掩码 >= /24 用真实掩码（iPhone 热点是 /28）；< /24 收窄到本机所在的 /24。
    剔除网络地址、广播地址与本机自身 IP。
    """
    address = _require_private(ipv4)
    prefix = max(prefix_length, max_hosts_prefix)
    network = ipaddress.ip_network(f"{ipv4}/{prefix}", strict=False)
    return [
        (str(host), port)
        for host in network.hosts()
        if host != address
        for port in ports
    ]


def select_best(
    hits: Sequence[ScanHit], prefer_port: int, ports: Sequence[int]
) -> ScanHit | None:
    """7890 > 1080，同端口取 TCP 建连耗时最低者。"""
    order = [prefer_port] + [port for port in ports if port != prefer_port]
    for port in order:
        candidates = [hit for hit in hits if hit.port == port]
        if candidates:
            return min(candidates, key=lambda hit: hit.latency_ms)
    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

> 注：`test_prefix_30_yields_only_the_peer` 记录了对规格第 7.2 节那句「/31、/32 直接产出空目标集」的细化——`/32` 确实为空，但 `/30`、`/31` 还有可用邻居，按 `hosts()` 自然展开即可，无需特判。

- [ ] **Step 5: Commit**

```bash
git add lan_proxy_switcher/scanner.py tests/test_scanner_logic.py
git commit -m "feat(scanner): 目标枚举、私有网段闸门与代理选择策略"
```

---

### Task 4: scanner.py 扫描执行与两阶段发现

**Files:**
- Modify: `lan_proxy_switcher/scanner.py`（追加，不改动 Task 3 已有内容）
- Test: `tests/test_scanner_scan.py`

**Interfaces:**
- Consumes: Task 3 的 `ScanHit`、`ScanRefused`、`gateway_targets`、`enumerate_targets`
- Produces:
  - `probe(ip: str, port: int, timeout_s: float) -> tuple[ScanHit | None, int | None]`——返回 (命中, errno)
  - `ScanReport` 冻结数据类：`hits: tuple[ScanHit, ...]`、`attempted: int`、`completed: int`、`errors: dict[int, int]`、`cancelled: bool`，方法 `all_unreachable() -> bool`
  - `scan(targets, timeout_ms: int, concurrency: int, cancel: threading.Event, on_hit: Callable[[ScanHit], None] | None = None) -> ScanReport`
  - `DiscoveryResult` 冻结数据类：`hits: tuple[ScanHit, ...]`、`phase: str`（`"gateway"` / `"subnet"` / `"refused"` / `"unreachable"` / `"cancelled"`）、`message: str`
  - `discover(adapter, ports, scan_fn: Callable[[list[tuple[str, int]]], ScanReport], cancel, max_hosts_prefix: int = 24) -> DiscoveryResult`
  - `discover` 只读取 adapter 的 `name` / `ipv4` / `prefix_length` / `gateway` 四个属性，因此测试可以用任意有这四个属性的对象，不必等 Task 5

- [ ] **Step 1: 写失败的测试**

`tests/test_scanner_scan.py`：

```python
import socket
import threading
from dataclasses import dataclass

import pytest

from lan_proxy_switcher.scanner import (
    DiscoveryResult,
    ScanHit,
    ScanReport,
    discover,
    scan,
)


@pytest.fixture
def open_port():
    """开一个真实监听 socket，返回其端口；测试结束自动关闭。"""
    opened = []

    def _open():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        opened.append(sock)
        return sock.getsockname()[1]

    yield _open
    for sock in opened:
        sock.close()


def closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------- scan 对真实 socket 的行为 ----------

def test_scan_finds_a_listening_port(open_port):
    port = open_port()

    report = scan([("127.0.0.1", port)], 500, 10, threading.Event())

    assert [(hit.ip, hit.port) for hit in report.hits] == [("127.0.0.1", port)]
    assert report.hits[0].latency_ms >= 0.0
    assert report.completed == 1


def test_scan_misses_a_closed_port():
    report = scan([("127.0.0.1", closed_port())], 500, 10, threading.Event())

    assert report.hits == ()
    assert report.completed == 1
    assert sum(report.errors.values()) == 1


def test_scan_calls_on_hit_for_each_hit(open_port):
    ports = [open_port(), open_port()]
    seen = []

    scan(
        [("127.0.0.1", p) for p in ports],
        500,
        10,
        threading.Event(),
        on_hit=seen.append,
    )

    assert sorted(hit.port for hit in seen) == sorted(ports)


def test_scan_sorts_hits_by_port_then_latency(open_port):
    ports = sorted([open_port(), open_port()])

    report = scan([("127.0.0.1", p) for p in ports], 500, 10, threading.Event())

    assert [hit.port for hit in report.hits] == ports


def test_scan_of_empty_target_list_is_a_noop():
    report = scan([], 500, 10, threading.Event())

    assert report == ScanReport((), 0, 0, {}, False)


def test_scan_reports_cancellation():
    cancel = threading.Event()
    cancel.set()

    report = scan([("127.0.0.1", closed_port())], 500, 10, cancel)

    assert report.cancelled is True


def test_scan_of_unroutable_address_yields_no_hit():
    """192.0.2.0/24 是 RFC5737 保留的 TEST-NET-1，保证不可路由。"""
    report = scan([("192.0.2.1", 7890)], 200, 10, threading.Event())

    assert report.hits == ()


# ---------- 两阶段发现 ----------

@dataclass(frozen=True)
class FakeAdapter:
    name: str
    ipv4: str | None
    prefix_length: int | None
    gateway: str | None


def recording_scan_fn(hits_by_call):
    """按调用次序依次返回预设结果，并记录每次收到的目标。"""
    calls = []

    def scan_fn(targets):
        calls.append(list(targets))
        hits = tuple(hits_by_call[len(calls) - 1])
        return ScanReport(hits, len(targets), len(targets), {}, False)

    return scan_fn, calls


def test_gateway_hit_short_circuits_full_scan():
    adapter = FakeAdapter("Ethernet 10", "172.20.10.7", 28, "172.20.10.1")
    hit = ScanHit("172.20.10.1", 7890, 12.0)
    scan_fn, calls = recording_scan_fn([[hit]])

    result = discover(adapter, (7890, 1080), scan_fn, threading.Event())

    assert result.phase == "gateway"
    assert result.hits == (hit,)
    assert len(calls) == 1
    assert calls[0] == [("172.20.10.1", 7890), ("172.20.10.1", 1080)]


def test_falls_through_to_subnet_scan_when_gateway_is_silent():
    adapter = FakeAdapter("Ethernet 10", "172.20.10.7", 28, "172.20.10.1")
    hit = ScanHit("172.20.10.3", 7890, 20.0)
    scan_fn, calls = recording_scan_fn([[], [hit]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "subnet"
    assert result.hits == (hit,)
    assert len(calls) == 2
    assert ("172.20.10.3", 7890) in calls[1]


def test_scans_subnet_directly_when_there_is_no_gateway():
    """场景 B：对端没有默认网关，只能靠全网段扫描发现。"""
    adapter = FakeAdapter("Ethernet", "192.168.10.10", 24, None)
    hit = ScanHit("192.168.10.20", 7890, 8.0)
    scan_fn, calls = recording_scan_fn([[hit]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "subnet"
    assert result.hits == (hit,)
    assert len(calls) == 1


def test_adapter_without_ipv4_is_refused():
    adapter = FakeAdapter("Wi-Fi", None, None, None)
    scan_fn, calls = recording_scan_fn([[]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "refused"
    assert calls == []
    assert "Wi-Fi" in result.message


def test_public_ipv4_is_refused_with_reason():
    adapter = FakeAdapter("Ethernet", "8.8.8.8", 24, None)
    scan_fn, calls = recording_scan_fn([[]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "refused"
    assert calls == []
    assert "私有网段" in result.message


def test_all_unreachable_is_reported_as_adapter_down():
    import errno

    adapter = FakeAdapter("Ethernet", "192.168.10.10", 24, None)

    def scan_fn(targets):
        return ScanReport((), len(targets), len(targets), {errno.ENETUNREACH: len(targets)}, False)

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert result.phase == "unreachable"
    assert "掉线" in result.message


def test_cancel_between_phases_stops_discovery():
    adapter = FakeAdapter("Ethernet 10", "172.20.10.7", 28, "172.20.10.1")
    cancel = threading.Event()

    def scan_fn(targets):
        cancel.set()
        return ScanReport((), len(targets), len(targets), {}, True)

    result = discover(adapter, (7890,), scan_fn, cancel)

    assert result.phase == "cancelled"


def test_discovery_result_is_returned_even_with_zero_hits():
    adapter = FakeAdapter("Ethernet", "192.168.10.10", 24, None)
    scan_fn, _ = recording_scan_fn([[]])

    result = discover(adapter, (7890,), scan_fn, threading.Event())

    assert isinstance(result, DiscoveryResult)
    assert result.hits == ()
    assert result.phase == "subnet"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scanner_scan.py -v`
Expected: FAIL，`ImportError: cannot import name 'scan'`

- [ ] **Step 3: 写实现（追加到 `lan_proxy_switcher/scanner.py` 末尾）**

文件顶部的 import 段补上：

```python
import errno
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Protocol
```

追加到文件末尾：

```python
# 整段网络不可达时的 errno，用于区分「没扫到代理」与「网卡刚掉线」
UNREACHABLE_ERRNOS = frozenset({errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN})


@dataclass(frozen=True)
class ScanReport:
    hits: tuple[ScanHit, ...]
    attempted: int
    completed: int
    errors: dict[int, int]
    cancelled: bool

    def all_unreachable(self) -> bool:
        if self.completed == 0 or self.hits:
            return False
        unreachable = sum(
            count for code, count in self.errors.items() if code in UNREACHABLE_ERRNOS
        )
        return unreachable == self.completed


class _AdapterLike(Protocol):
    name: str
    ipv4: str | None
    prefix_length: int | None
    gateway: str | None


@dataclass(frozen=True)
class DiscoveryResult:
    hits: tuple[ScanHit, ...]
    phase: str
    message: str


def probe(ip: str, port: int, timeout_s: float) -> tuple[ScanHit | None, int | None]:
    """只做 TCP Connect。连上即算命中，任何错误都算未命中。"""
    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect((ip, port))
    except socket.timeout:
        return None, errno.ETIMEDOUT
    except OSError as exc:
        return None, exc.errno
    finally:
        sock.close()
    return ScanHit(ip, port, (time.perf_counter() - started) * 1000.0), None


def scan(
    targets: Sequence[tuple[str, int]],
    timeout_ms: int,
    concurrency: int,
    cancel: threading.Event,
    on_hit: Callable[[ScanHit], None] | None = None,
) -> ScanReport:
    targets = list(targets)
    if not targets:
        return ScanReport((), 0, 0, {}, cancel.is_set())

    timeout_s = timeout_ms / 1000.0
    hits: list[ScanHit] = []
    errors: dict[int, int] = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=min(concurrency, len(targets))) as pool:
        futures = [pool.submit(probe, ip, port, timeout_s) for ip, port in targets]
        for future in as_completed(futures):
            if cancel.is_set():
                for pending in futures:
                    pending.cancel()
                break
            hit, code = future.result()
            completed += 1
            if hit is not None:
                hits.append(hit)
                if on_hit is not None:
                    on_hit(hit)
            elif code is not None:
                errors[code] = errors.get(code, 0) + 1

    hits.sort(key=lambda hit: (hit.port, hit.latency_ms))
    return ScanReport(tuple(hits), len(targets), completed, errors, cancel.is_set())


def discover(
    adapter: _AdapterLike,
    ports: Sequence[int],
    scan_fn: Callable[[list[tuple[str, int]]], ScanReport],
    cancel: threading.Event,
    max_hosts_prefix: int = 24,
) -> DiscoveryResult:
    """规格第 7.3 节的两阶段发现：先探网关，不中再扫全网段。"""
    if adapter.ipv4 is None or adapter.prefix_length is None:
        return DiscoveryResult((), "refused", f"{adapter.name} 无可用 IPv4，跳过扫描")

    try:
        first_stage = gateway_targets(adapter.gateway, ports)
    except ScanRefused:
        first_stage = []  # 网关不在私有段，跳过快路径，让全网段闸门给出统一说明

    if first_stage:
        report = scan_fn(first_stage)
        if report.hits:
            return DiscoveryResult(
                report.hits,
                "gateway",
                f"网关 {adapter.gateway} 命中 {len(report.hits)} 个端口，跳过全网段扫描",
            )

    if cancel.is_set():
        return DiscoveryResult((), "cancelled", "扫描已取消")

    try:
        targets = enumerate_targets(
            adapter.ipv4, adapter.prefix_length, ports, max_hosts_prefix
        )
    except ScanRefused as exc:
        return DiscoveryResult((), "refused", str(exc))

    if not targets:
        return DiscoveryResult(
            (),
            "refused",
            f"{adapter.ipv4}/{adapter.prefix_length} 没有可扫描的邻居地址",
        )

    report = scan_fn(targets)
    if report.cancelled:
        return DiscoveryResult((), "cancelled", "扫描已取消")
    if report.all_unreachable():
        return DiscoveryResult((), "unreachable", "全部目标网络不可达，网卡可能刚刚掉线")
    return DiscoveryResult(
        report.hits, "subnet", f"扫描 {len(targets)} 个目标，命中 {len(report.hits)} 个"
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: 量一下真实 /24 扫描耗时（规格第 7.3 节声称最坏约 3 秒）**

```bash
.venv/Scripts/python.exe -c "
import threading, time
from lan_proxy_switcher.scanner import enumerate_targets, scan
targets = enumerate_targets('192.168.10.10', 24, (7890, 1080))
start = time.perf_counter()
report = scan(targets, 500, 100, threading.Event())
print(len(targets), 'targets', round(time.perf_counter() - start, 2), 's', len(report.hits), 'hits')
"
```

Expected: `506 targets` 且耗时在 10 秒以内。若明显超过，把实测值记进 commit message，后续调 `scanConcurrency` 默认值时有依据。

- [ ] **Step 6: Commit**

```bash
git add lan_proxy_switcher/scanner.py tests/test_scanner_scan.py
git commit -m "feat(scanner): 并发 TCP Connect 扫描与网关优先的两阶段发现"
```

---

### Task 5: network.py 网卡查询与解析

**Files:**
- Create: `lan_proxy_switcher/network.py`
- Create: `tests/fixtures/adapters_snapshot.json`
- Test: `tests/test_network_parse.py`
- Test: `tests/test_network_live.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `AdapterKind`（`WIRED` / `WIRELESS`）、`AdapterStatus`（`UP` / `DISCONNECTED` / `DISABLED`）
  - `Adapter` 冻结数据类：`name: str`、`index: int`、`description: str`、`kind: AdapterKind`、`status: AdapterStatus`、`ipv4: str | None`、`prefix_length: int | None`、`gateway: str | None`、`metric: int | None`
  - `NetworkError(Exception)`
  - `NetworkService(Protocol)`：`list_adapters() -> list[Adapter]`、`set_adapter_enabled(index: int, enabled: bool) -> None`
  - `run_powershell(script: str, timeout: float = 30.0) -> str`
  - `QUERY_SCRIPT: str`
  - `parse_snapshot(payload: dict) -> list[Adapter]`
  - `select_active(adapters: Sequence[Adapter]) -> Adapter | None`
  - `current_identity() -> str`
  - `PowerShellNetworkService`（本任务只实现 `list_adapters`，`set_adapter_enabled` 留给 Task 6）

- [ ] **Step 1: 建 fixture**

`tests/fixtures/adapters_snapshot.json`——取自开发机实测（规格第 4 节），保留 3 张真实物理网卡，另补 3 张确定为虚拟的网卡用于验证过滤：

```json
{
  "adapters": [
    {
      "Name": "Ethernet 10",
      "InterfaceIndex": 32,
      "InterfaceDescription": "Apple Mobile Device Ethernet #8",
      "Status": "Up",
      "MediaType": "802.3",
      "PhysicalMediaType": "Unspecified",
      "HardwareInterface": true,
      "Virtual": false
    },
    {
      "Name": "Ethernet",
      "InterfaceIndex": 25,
      "InterfaceDescription": "Realtek PCIe GbE Family Controller",
      "Status": "Up",
      "MediaType": "802.3",
      "PhysicalMediaType": "802.3",
      "HardwareInterface": true,
      "Virtual": false
    },
    {
      "Name": "Wi-Fi",
      "InterfaceIndex": 23,
      "InterfaceDescription": "RZ616 Wi-Fi 6E 160MHz",
      "Status": "Disconnected",
      "MediaType": "Native 802.11",
      "PhysicalMediaType": "Native 802.11",
      "HardwareInterface": true,
      "Virtual": false
    },
    {
      "Name": "vEthernet (Default Switch)",
      "InterfaceIndex": 40,
      "InterfaceDescription": "Hyper-V Virtual Ethernet Adapter",
      "Status": "Up",
      "MediaType": "802.3",
      "PhysicalMediaType": "Unspecified",
      "HardwareInterface": false,
      "Virtual": true
    },
    {
      "Name": "VMware Network Adapter VMnet8",
      "InterfaceIndex": 33,
      "InterfaceDescription": "VMware Virtual Ethernet Adapter for VMnet8",
      "Status": "Up",
      "MediaType": "802.3",
      "PhysicalMediaType": "802.3",
      "HardwareInterface": false,
      "Virtual": true
    },
    {
      "Name": "Tailscale",
      "InterfaceIndex": 8,
      "InterfaceDescription": "Tailscale Tunnel",
      "Status": "Up",
      "MediaType": "IP",
      "PhysicalMediaType": "Unspecified",
      "HardwareInterface": false,
      "Virtual": true
    }
  ],
  "addresses": [
    {"InterfaceIndex": 32, "IPAddress": "172.20.10.7", "PrefixLength": 28},
    {"InterfaceIndex": 25, "IPAddress": "10.0.0.2", "PrefixLength": 24},
    {"InterfaceIndex": 23, "IPAddress": "169.254.25.234", "PrefixLength": 16},
    {"InterfaceIndex": 40, "IPAddress": "172.22.112.1", "PrefixLength": 20},
    {"InterfaceIndex": 33, "IPAddress": "192.168.137.1", "PrefixLength": 24},
    {"InterfaceIndex": 8, "IPAddress": "100.111.31.82", "PrefixLength": 32}
  ],
  "routes": [
    {"InterfaceIndex": 32, "NextHop": "172.20.10.1", "RouteMetric": 0, "InterfaceMetric": 25}
  ]
}
```

- [ ] **Step 2: 写失败的测试**

`tests/test_network_parse.py`：

```python
import json
import pathlib

import pytest

from lan_proxy_switcher.network import (
    Adapter,
    AdapterKind,
    AdapterStatus,
    parse_snapshot,
    select_active,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "adapters_snapshot.json"


@pytest.fixture
def snapshot():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def adapters(snapshot):
    return parse_snapshot(snapshot)


def by_name(adapters, name):
    return next(adapter for adapter in adapters if adapter.name == name)


# ---------- 过滤（规格第 4 节规则 1）----------

def test_only_physical_adapters_survive(adapters):
    assert sorted(adapter.name for adapter in adapters) == [
        "Ethernet",
        "Ethernet 10",
        "Wi-Fi",
    ]


def test_virtual_adapters_are_dropped(adapters):
    names = {adapter.name for adapter in adapters}
    assert "Tailscale" not in names
    assert "VMware Network Adapter VMnet8" not in names
    assert "vEthernet (Default Switch)" not in names


# ---------- 类型判定（规格第 10 节：不依据中文名称）----------

def test_wifi_is_wireless(adapters):
    assert by_name(adapters, "Wi-Fi").kind is AdapterKind.WIRELESS


def test_realtek_is_wired(adapters):
    assert by_name(adapters, "Ethernet").kind is AdapterKind.WIRED


def test_iphone_usb_tether_counts_as_wired(adapters):
    """Apple Mobile Device Ethernet 的 PhysicalMediaType 是 Unspecified。"""
    assert by_name(adapters, "Ethernet 10").kind is AdapterKind.WIRED


# ---------- 状态映射 ----------

def test_status_mapping(adapters):
    assert by_name(adapters, "Ethernet 10").status is AdapterStatus.UP
    assert by_name(adapters, "Wi-Fi").status is AdapterStatus.DISCONNECTED


def test_disabled_status_is_recognised(snapshot):
    snapshot["adapters"][2]["Status"] = "Disabled"

    assert by_name(parse_snapshot(snapshot), "Wi-Fi").status is AdapterStatus.DISABLED


def test_unknown_status_degrades_to_disconnected(snapshot):
    snapshot["adapters"][2]["Status"] = "Not Present"

    assert by_name(parse_snapshot(snapshot), "Wi-Fi").status is AdapterStatus.DISCONNECTED


# ---------- 地址（规格第 4 节规则 2、3）----------

def test_apipa_address_is_treated_as_no_ipv4(adapters):
    wifi = by_name(adapters, "Wi-Fi")

    assert wifi.ipv4 is None
    assert wifi.prefix_length is None


def test_iphone_prefix_is_28_not_24(adapters):
    iphone = by_name(adapters, "Ethernet 10")

    assert iphone.ipv4 == "172.20.10.7"
    assert iphone.prefix_length == 28


def test_adapter_without_any_address_has_none(snapshot):
    snapshot["addresses"] = [a for a in snapshot["addresses"] if a["InterfaceIndex"] != 25]

    assert by_name(parse_snapshot(snapshot), "Ethernet").ipv4 is None


# ---------- 网关 ----------

def test_gateway_and_metric_come_from_default_route(adapters):
    iphone = by_name(adapters, "Ethernet 10")

    assert iphone.gateway == "172.20.10.1"
    assert iphone.metric == 25


def test_adapter_without_default_route_has_no_gateway(adapters):
    """场景 B 的对端就是这样：没有默认网关。"""
    assert by_name(adapters, "Ethernet").gateway is None


def test_onlink_next_hop_is_not_a_gateway(snapshot):
    snapshot["routes"] = [
        {"InterfaceIndex": 25, "NextHop": "0.0.0.0", "RouteMetric": 0, "InterfaceMetric": 5}
    ]

    assert by_name(parse_snapshot(snapshot), "Ethernet").gateway is None


def test_lowest_interface_metric_wins_when_several_routes(snapshot):
    snapshot["routes"].append(
        {"InterfaceIndex": 32, "NextHop": "172.20.10.1", "RouteMetric": 0, "InterfaceMetric": 5}
    )

    assert by_name(parse_snapshot(snapshot), "Ethernet 10").metric == 5


# ---------- 活动网卡选择（规格第 7.1 节）----------

def test_active_adapter_is_the_one_with_a_gateway(adapters):
    assert select_active(adapters).name == "Ethernet 10"


def test_active_falls_back_to_first_usable_ipv4_when_no_gateway(snapshot):
    snapshot["routes"] = []

    active = select_active(parse_snapshot(snapshot))

    assert active.name == "Ethernet 10"
    assert active.gateway is None


def test_active_prefers_lowest_metric_among_gateways(snapshot):
    snapshot["routes"] = [
        {"InterfaceIndex": 32, "NextHop": "172.20.10.1", "RouteMetric": 0, "InterfaceMetric": 25},
        {"InterfaceIndex": 25, "NextHop": "10.0.0.1", "RouteMetric": 0, "InterfaceMetric": 5},
    ]

    assert select_active(parse_snapshot(snapshot)).name == "Ethernet"


def test_active_is_none_when_nothing_has_an_address(snapshot):
    snapshot["addresses"] = []

    assert select_active(parse_snapshot(snapshot)) is None


def test_adapter_is_hashable_and_comparable():
    """monitor 用快照相等性做去抖动，Adapter 必须可比较。"""
    one = Adapter("Wi-Fi", 23, "d", AdapterKind.WIRELESS, AdapterStatus.UP, None, None, None, None)
    two = Adapter("Wi-Fi", 23, "d", AdapterKind.WIRELESS, AdapterStatus.UP, None, None, None, None)

    assert one == two
    assert len({one, two}) == 1
```

`tests/test_network_live.py`——真跑 PowerShell，只读无副作用：

```python
import sys

import pytest

from lan_proxy_switcher.network import (
    Adapter,
    AdapterKind,
    AdapterStatus,
    PowerShellNetworkService,
    current_identity,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows")


def test_list_adapters_returns_self_consistent_data():
    adapters = PowerShellNetworkService().list_adapters()

    assert adapters, "本机至少应有一张物理网卡"
    for adapter in adapters:
        assert isinstance(adapter, Adapter)
        assert adapter.index > 0
        assert adapter.name
        assert adapter.description
        assert isinstance(adapter.kind, AdapterKind)
        assert isinstance(adapter.status, AdapterStatus)
        if adapter.ipv4 is not None:
            assert not adapter.ipv4.startswith("169.254.")
            assert adapter.prefix_length is not None


def test_current_identity_reports_a_user_and_sid():
    identity = current_identity()

    assert "S-1-" in identity
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_network_parse.py tests/test_network_live.py -v`
Expected: FAIL，`ModuleNotFoundError: lan_proxy_switcher.network`

- [ ] **Step 4: 写实现**

`lan_proxy_switcher/network.py`（本任务写到 `list_adapters` 为止）：

```python
"""网卡查询与切换：PowerShell 子进程 + JSON。

选 PowerShell 而非 ctypes 直调 Win32 的决定性理由：被禁用的网卡不会出现在
GetAdaptersAddresses 的结果里，而 GUI 必须显示「已禁用」状态。
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

CREATE_NO_WINDOW = 0x08000000

# PowerShell 5.1 默认按控制台代码页输出，中文网卡名会变乱码
_UTF8_PREFIX = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "

# @() 强制成数组：ConvertTo-Json 对单个对象不会输出数组
QUERY_SCRIPT = (
    "$adapters = Get-NetAdapter | Select-Object Name,InterfaceIndex,"
    "InterfaceDescription,Status,MediaType,PhysicalMediaType,HardwareInterface,Virtual; "
    "$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
    "Select-Object InterfaceIndex,IPAddress,PrefixLength; "
    "$routes = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
    "Select-Object InterfaceIndex,NextHop,RouteMetric,InterfaceMetric; "
    "[pscustomobject]@{adapters=@($adapters); addresses=@($addresses); routes=@($routes)} | "
    "ConvertTo-Json -Depth 4 -Compress"
)

IDENTITY_SCRIPT = (
    "$id = [Security.Principal.WindowsIdentity]::GetCurrent(); "
    "\"$($id.Name) $($id.User.Value)\""
)

APIPA = ipaddress.ip_network("169.254.0.0/16")


class NetworkError(Exception):
    """PowerShell 调用失败或输出无法解析。"""


class AdapterKind(Enum):
    WIRED = "有线"
    WIRELESS = "无线"


class AdapterStatus(Enum):
    UP = "已连接"
    DISCONNECTED = "未连接"
    DISABLED = "已禁用"


@dataclass(frozen=True)
class Adapter:
    name: str
    index: int
    description: str
    kind: AdapterKind
    status: AdapterStatus
    ipv4: str | None
    prefix_length: int | None
    gateway: str | None
    metric: int | None


class NetworkService(Protocol):
    def list_adapters(self) -> list[Adapter]: ...

    def set_adapter_enabled(self, index: int, enabled: bool) -> None: ...


def run_powershell(script: str, timeout: float = 30.0) -> str:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _UTF8_PREFIX + script],
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkError(f"PowerShell 调用失败：{exc}") from exc

    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise NetworkError(f"PowerShell 退出码 {proc.returncode}：{stderr[-400:]}")
    return stdout


def _kind(row: dict) -> AdapterKind:
    media = f"{row.get('PhysicalMediaType') or ''} {row.get('MediaType') or ''}"
    return AdapterKind.WIRELESS if "802.11" in media else AdapterKind.WIRED


def _status(row: dict) -> AdapterStatus:
    raw = (row.get("Status") or "").strip().lower()
    if raw == "up":
        return AdapterStatus.UP
    if raw == "disabled":
        return AdapterStatus.DISABLED
    return AdapterStatus.DISCONNECTED


def _usable_address(rows: Sequence[dict]) -> tuple[str | None, int | None]:
    for row in rows:
        raw = row.get("IPAddress")
        if not raw:
            continue
        try:
            address = ipaddress.IPv4Address(raw)
        except ipaddress.AddressValueError:
            continue
        if address in APIPA or address.is_loopback:
            continue
        return str(address), int(row["PrefixLength"])
    return None, None


def _default_route(rows: Sequence[dict]) -> tuple[str | None, int | None]:
    best: tuple[str, int] | None = None
    for row in rows:
        next_hop = row.get("NextHop")
        if not next_hop or next_hop == "0.0.0.0":
            continue  # 直连路由，不是网关
        metric = int(row.get("InterfaceMetric") or 0)
        if best is None or metric < best[1]:
            best = (next_hop, metric)
    return best if best is not None else (None, None)


def parse_snapshot(payload: dict) -> list[Adapter]:
    """把 QUERY_SCRIPT 的 JSON 输出解析成 Adapter 列表，只保留物理网卡。"""
    addresses: dict[int, list[dict]] = {}
    for row in payload.get("addresses") or []:
        addresses.setdefault(int(row["InterfaceIndex"]), []).append(row)

    routes: dict[int, list[dict]] = {}
    for row in payload.get("routes") or []:
        routes.setdefault(int(row["InterfaceIndex"]), []).append(row)

    adapters: list[Adapter] = []
    for row in payload.get("adapters") or []:
        if not row.get("HardwareInterface") or row.get("Virtual"):
            continue
        index = int(row["InterfaceIndex"])
        ipv4, prefix_length = _usable_address(addresses.get(index, []))
        gateway, metric = _default_route(routes.get(index, []))
        adapters.append(
            Adapter(
                name=row["Name"],
                index=index,
                description=row.get("InterfaceDescription") or "",
                kind=_kind(row),
                status=_status(row),
                ipv4=ipv4,
                prefix_length=prefix_length,
                gateway=gateway,
                metric=metric,
            )
        )
    return adapters


def select_active(adapters: Sequence[Adapter]) -> Adapter | None:
    """① 有网关且 InterfaceMetric 最小者；② 否则第一张有可用 IPv4 的。"""
    with_gateway = [a for a in adapters if a.gateway and a.ipv4]
    if with_gateway:
        return min(with_gateway, key=lambda a: (a.metric if a.metric is not None else 9999))
    with_address = [a for a in adapters if a.ipv4]
    return with_address[0] if with_address else None


def current_identity() -> str:
    """当前进程的账户名与 SID，用于确认代理写进了哪个用户的 HKCU。"""
    return run_powershell(IDENTITY_SCRIPT).strip()


class PowerShellNetworkService:
    def list_adapters(self) -> list[Adapter]:
        raw = run_powershell(QUERY_SCRIPT)
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise NetworkError(f"网卡查询输出无法解析为 JSON：{raw[:200]!r}") from exc
        if not isinstance(payload, dict):
            raise NetworkError(f"网卡查询输出结构异常：{raw[:200]!r}")
        return parse_snapshot(payload)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

如果 `test_list_adapters_returns_self_consistent_data` 失败，先单独跑一次查询看真实输出：

```bash
.venv/Scripts/python.exe -c "
from lan_proxy_switcher.network import QUERY_SCRIPT, run_powershell
print(run_powershell(QUERY_SCRIPT)[:2000])
"
```

若真实输出的字段名与 fixture 不符（例如 `HardwareInterface` 没被 `Select-Object` 带出来），**以真实输出为准修正 `QUERY_SCRIPT` 和 fixture**，并在 commit message 里记下差异。

- [ ] **Step 6: Commit**

```bash
git add lan_proxy_switcher/network.py tests/fixtures/adapters_snapshot.json tests/test_network_parse.py tests/test_network_live.py
git commit -m "feat(network): PowerShell 网卡查询、物理网卡过滤与活动网卡选择"
```

---

### Task 6: network.py 网卡启用/禁用

单独成一个任务，因为它是整个项目唯一有破坏性副作用、且无法自动化测试的写操作，风险画像与只读查询完全不同。

**Files:**
- Modify: `lan_proxy_switcher/network.py`（追加）
- Test: `tests/test_network_switch.py`
- Create: `MANUAL-VERIFICATION.md`

**Interfaces:**
- Consumes: Task 5 的 `run_powershell`、`NetworkError`
- Produces:
  - `switch_script(index: int, enabled: bool) -> str`——纯函数，生成 PowerShell 命令串
  - `PowerShellNetworkService.set_adapter_enabled(index: int, enabled: bool) -> None`
  - 至此 `PowerShellNetworkService` 完整实现 `NetworkService` 协议

- [ ] **Step 1: 写失败的测试**

`tests/test_network_switch.py`：

```python
import pytest

from lan_proxy_switcher import network


def test_enable_script_targets_the_interface_index():
    script = network.switch_script(23, True)

    assert "Enable-NetAdapter" in script
    assert "-InterfaceIndex 23" in script


def test_disable_script_targets_the_interface_index():
    script = network.switch_script(25, False)

    assert "Disable-NetAdapter" in script
    assert "-InterfaceIndex 25" in script


def test_script_never_prompts():
    """-NonInteractive 的子进程里任何确认提示都会挂死。"""
    for enabled in (True, False):
        script = network.switch_script(1, enabled)
        assert "-Confirm:$false" in script


def test_script_stops_on_error():
    """默认 non-terminating error 会让退出码保持 0，失败就被吞掉了。"""
    assert "-ErrorAction Stop" in network.switch_script(1, True)


def test_index_must_be_an_integer():
    """防注入：index 直接拼进命令串，必须是整数。"""
    with pytest.raises((TypeError, ValueError)):
        network.switch_script("23; Remove-Item C:\\", True)


def test_set_adapter_enabled_invokes_the_script(monkeypatch):
    seen = []
    monkeypatch.setattr(network, "run_powershell", lambda script, **kw: seen.append(script) or "")

    network.PowerShellNetworkService().set_adapter_enabled(23, True)

    assert len(seen) == 1
    assert "Enable-NetAdapter" in seen[0]
    assert "-InterfaceIndex 23" in seen[0]


def test_set_adapter_enabled_propagates_failure(monkeypatch):
    def boom(script, **kw):
        raise network.NetworkError("拒绝访问")

    monkeypatch.setattr(network, "run_powershell", boom)

    with pytest.raises(network.NetworkError):
        network.PowerShellNetworkService().set_adapter_enabled(23, False)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_network_switch.py -v`
Expected: FAIL，`AttributeError: module 'lan_proxy_switcher.network' has no attribute 'switch_script'`

- [ ] **Step 3: 写实现（追加到 `lan_proxy_switcher/network.py`）**

```python
def switch_script(index: int, enabled: bool) -> str:
    """启用/禁用网卡。index 强制转 int，杜绝命令注入。"""
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError(f"InterfaceIndex 必须是整数，得到 {index!r}")
    verb = "Enable-NetAdapter" if enabled else "Disable-NetAdapter"
    return f"{verb} -InterfaceIndex {int(index)} -Confirm:$false -ErrorAction Stop"
```

并给 `PowerShellNetworkService` 补上方法：

```python
    def set_adapter_enabled(self, index: int, enabled: bool) -> None:
        run_powershell(switch_script(index, enabled), timeout=60.0)
```

注意：方法体里调用的是模块级的 `run_powershell`，测试通过 `monkeypatch.setattr(network, "run_powershell", ...)` 替换它，因此**不要**把它改成 `self._run` 之类的实例属性。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: 真机手动验证一次**

以**管理员身份**开一个 PowerShell，跑：

```bash
.venv/Scripts/python.exe -c "
from lan_proxy_switcher.network import PowerShellNetworkService
service = PowerShellNetworkService()
for adapter in service.list_adapters():
    print(adapter.index, adapter.name, adapter.status)
"
```

挑一张**当前没在用**的网卡（例如未连接的 Wi-Fi），记下它的 index，然后：

```bash
.venv/Scripts/python.exe -c "
from lan_proxy_switcher.network import PowerShellNetworkService
service = PowerShellNetworkService()
service.set_adapter_enabled(<index>, False)
print([ (a.name, a.status) for a in service.list_adapters() ])
"
```

Expected: 该网卡状态变成 `AdapterStatus.DISABLED`。随后用 `True` 再启用回去，状态应回到 `DISCONNECTED` 或 `UP`。

**不要拿当前正在提供网络的那张网卡做这个验证**，会把自己的网断掉。

- [ ] **Step 6: 建手动验证清单文件**

`MANUAL-VERIFICATION.md`：

```markdown
# 手动验证清单

自动化测试覆盖不到的部分（真实网卡启停、真实系统代理、真实 GUI）逐条验证。
每次改动 network.py / proxy.py / gui.py / controller.py 后重跑本清单。

## 1. 网卡启用/禁用（Task 6）

- [ ] 列出网卡，各字段与「设置 → 网络和 Internet」显示一致
- [ ] 禁用一张空闲网卡，状态变为「已禁用」
- [ ] 重新启用，状态回到「未连接」或「已连接」
- [ ] 以**非管理员**身份执行禁用，程序给出清晰的失败日志而非崩溃

（后续任务会往本文件追加第 2 节及以后的条目。）
```

- [ ] **Step 7: Commit**

```bash
git add lan_proxy_switcher/network.py tests/test_network_switch.py MANUAL-VERIFICATION.md
git commit -m "feat(network): 网卡启用/禁用与手动验证清单"
```

---

### Task 7: proxy.py 系统代理读写

**Files:**
- Create: `lan_proxy_switcher/proxy.py`
- Test: `tests/test_proxy.py`
- Modify: `MANUAL-VERIFICATION.md`（追加第 2 节）

**Interfaces:**
- Consumes: 无
- Produces:
  - `ProxyState` 冻结数据类：`enable: bool`、`server: str | None`、`override: str | None`、`auto_config_url: str | None`
  - `ProxyError(Exception)`
  - `ProxyService(Protocol)`：`read()`、`apply(server)`、`disable()`、`restore(state)`
  - `INTERNET_SETTINGS_SUBKEY: str`
  - `notify_settings_changed() -> None`
  - `RegistryProxyService(subkey: str = INTERNET_SETTINGS_SUBKEY, notify: Callable[[], None] = notify_settings_changed)`

- [ ] **Step 1: 写失败的测试**

`tests/test_proxy.py`——**注册表子键注入成测试专用键，永不触碰真实代理设置**：

```python
import sys
import winreg

import pytest

from lan_proxy_switcher.proxy import (
    INTERNET_SETTINGS_SUBKEY,
    ProxyError,
    ProxyState,
    RegistryProxyService,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows 注册表")

TEST_SUBKEY = r"Software\LANProxySwitcher\TestSettings"


@pytest.fixture
def service():
    calls = []
    svc = RegistryProxyService(subkey=TEST_SUBKEY, notify=lambda: calls.append("notify"))
    svc.notify_calls = calls
    yield svc
    for key in (TEST_SUBKEY, r"Software\LANProxySwitcher"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


def raw_value(name):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_SUBKEY) as key:
        return winreg.QueryValueEx(key, name)[0]


def test_default_subkey_is_the_real_internet_settings():
    """这条常量一旦被改错，整个程序会写错地方。"""
    assert INTERNET_SETTINGS_SUBKEY == (
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    )


def test_tests_never_use_the_real_subkey():
    assert TEST_SUBKEY != INTERNET_SETTINGS_SUBKEY


def test_read_of_missing_key_returns_empty_state(service):
    assert service.read() == ProxyState(False, None, None, None)


def test_apply_writes_enable_and_server(service):
    service.apply("172.20.10.1:7890")

    assert raw_value("ProxyEnable") == 1
    assert raw_value("ProxyServer") == "172.20.10.1:7890"
    assert service.read() == ProxyState(True, "172.20.10.1:7890", None, None)


def test_apply_notifies_windows(service):
    service.apply("172.20.10.1:7890")

    assert service.notify_calls == ["notify"]


def test_apply_removes_auto_config_url(service):
    """PAC 存在时 Windows 优先走 PAC，ProxyServer 会静默失效。"""
    service.restore(ProxyState(False, None, None, "http://example.local/pac"))
    assert service.read().auto_config_url == "http://example.local/pac"

    service.apply("10.0.0.20:7890")

    assert service.read().auto_config_url is None


def test_apply_keeps_existing_override(service):
    service.restore(ProxyState(False, None, "<local>;10.*", None))

    service.apply("10.0.0.20:7890")

    assert service.read().override == "<local>;10.*"


def test_disable_keeps_server_value(service):
    service.apply("10.0.0.20:7890")

    service.disable()

    state = service.read()
    assert state.enable is False
    assert state.server == "10.0.0.20:7890"


def test_restore_puts_every_field_back(service):
    service.apply("10.0.0.20:7890")
    snapshot = ProxyState(False, "192.168.1.1:8080", "<local>", "http://old/pac")

    service.restore(snapshot)

    assert service.read() == snapshot


def test_restore_deletes_fields_that_were_absent(service):
    service.restore(ProxyState(True, "1.2.3.4:80", "<local>", "http://pac"))

    service.restore(ProxyState(False, None, None, None))

    assert service.read() == ProxyState(False, None, None, None)


@pytest.mark.parametrize(
    "server",
    ["", "no-colon", "10.0.0.1:", ":7890", "10.0.0.1:0", "10.0.0.1:70000", "10.0.0.1:abc"],
)
def test_apply_rejects_malformed_server(service, server):
    with pytest.raises(ProxyError):
        service.apply(server)


def test_apply_raises_when_readback_disagrees(service, monkeypatch):
    """写后必须读回校验，不许只记『设置成功』。"""
    monkeypatch.setattr(service, "read", lambda: ProxyState(False, None, None, None))

    with pytest.raises(ProxyError):
        service.apply("10.0.0.20:7890")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_proxy.py -v`
Expected: FAIL，`ModuleNotFoundError: lan_proxy_switcher.proxy`

- [ ] **Step 3: 写实现**

`lan_proxy_switcher/proxy.py`：

```python
"""Windows 系统代理（当前用户）的读取、设置与恢复。

只操作 HKCU 下的 Internet Settings，不碰 HKLM，不做 TUN，不装驱动。
"""

from __future__ import annotations

import ctypes
import winreg
from dataclasses import dataclass
from typing import Callable, Protocol

INTERNET_SETTINGS_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

INTERNET_OPTION_REFRESH = 37
INTERNET_OPTION_SETTINGS_CHANGED = 39


class ProxyError(Exception):
    """代理设置写入失败或校验不通过。"""


@dataclass(frozen=True)
class ProxyState:
    enable: bool
    server: str | None
    override: str | None
    auto_config_url: str | None


class ProxyService(Protocol):
    def read(self) -> ProxyState: ...

    def apply(self, server: str) -> None: ...

    def disable(self) -> None: ...

    def restore(self, state: ProxyState) -> None: ...


def notify_settings_changed() -> None:
    """告诉已经在运行的程序重新读取代理配置，否则要过几分钟才生效。"""
    wininet = ctypes.WinDLL("wininet.dll")
    wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
    wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)


def validate_server(server: str) -> str:
    host, sep, port = str(server).rpartition(":")
    if not sep or not host or not port:
        raise ProxyError(f"代理地址格式必须是 IP:Port，得到 {server!r}")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ProxyError(f"代理端口非法：{server!r}")
    return server


class RegistryProxyService:
    def __init__(
        self,
        subkey: str = INTERNET_SETTINGS_SUBKEY,
        notify: Callable[[], None] = notify_settings_changed,
    ) -> None:
        self._subkey = subkey
        self._notify = notify

    def _open_for_write(self):
        return winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, self._subkey, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
        )

    @staticmethod
    def _query(key, name):
        try:
            return winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None

    @staticmethod
    def _delete(key, name):
        try:
            winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def read(self) -> ProxyState:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._subkey, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return ProxyState(False, None, None, None)
        with key:
            enable = self._query(key, "ProxyEnable")
            return ProxyState(
                enable=bool(enable),
                server=self._query(key, "ProxyServer"),
                override=self._query(key, "ProxyOverride"),
                auto_config_url=self._query(key, "AutoConfigURL"),
            )

    def apply(self, server: str) -> None:
        validate_server(server)
        with self._open_for_write() as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
            # PAC 优先级高于 ProxyServer，留着它我们设的代理不会生效
            self._delete(key, "AutoConfigURL")
        self._notify()

        state = self.read()
        if not state.enable or state.server != server or state.auto_config_url is not None:
            raise ProxyError(f"代理写入后校验失败，注册表当前为 {state}")

    def disable(self) -> None:
        with self._open_for_write() as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        self._notify()

        if self.read().enable:
            raise ProxyError("代理关闭后校验失败，ProxyEnable 仍为 1")

    def restore(self, state: ProxyState) -> None:
        with self._open_for_write() as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, int(state.enable))
            for name, value in (
                ("ProxyServer", state.server),
                ("ProxyOverride", state.override),
                ("AutoConfigURL", state.auto_config_url),
            ):
                if value is None:
                    self._delete(key, name)
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        self._notify()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: 确认真实代理设置没被动过**

```bash
.venv/Scripts/python.exe -c "
from lan_proxy_switcher.proxy import RegistryProxyService
print(RegistryProxyService().read())
"
```

跑测试前后各跑一次，两次输出必须完全一致。若不一致说明测试污染了真实键，**立刻停下来修**。

- [ ] **Step 6: 追加手动验证清单第 2 节**

在 `MANUAL-VERIFICATION.md` 末尾追加：

```markdown
## 2. 系统代理（Task 7）

- [ ] `apply("127.0.0.1:7890")` 后，「设置 → 网络和 Internet → 代理」显示手动代理已开启且地址正确
- [ ] 浏览器新开标签页立即走代理（无需重启浏览器），验证 InternetSetOption 通知生效
- [ ] `disable()` 后设置页显示已关闭，但地址栏仍保留原地址
- [ ] 事先手动设一个 PAC 脚本地址，`apply()` 后 PAC 被清空且手动代理生效
- [ ] 程序日志首行的账户名/SID 与当前登录用户一致
```

- [ ] **Step 7: Commit**

```bash
git add lan_proxy_switcher/proxy.py tests/test_proxy.py MANUAL-VERIFICATION.md
git commit -m "feat(proxy): HKCU 系统代理读写、PAC 清理与写后校验"
```

---

### Task 8: monitor.py 代理探测与网络变化检测

**Files:**
- Create: `lan_proxy_switcher/monitor.py`
- Test: `tests/test_monitor.py`

**Interfaces:**
- Consumes: Task 5 的 `Adapter`；Task 4 的 `probe` 签名 `(ip, port, timeout_s) -> tuple[ScanHit | None, int | None]`
- Produces:
  - `NetworkChanged` 冻结数据类：`adapters: tuple[Adapter, ...]`
  - `ProxyOk` 冻结数据类：`server: str`、`latency_ms: float`
  - `ProxyLost` 冻结数据类：`server: str`、`failures: int`
  - `MonitorError` 冻结数据类：`message: str`
  - `MonitorState(*, list_adapters, probe, monitor_interval_s, scan_timeout_ms, proxy_check_failures, network_poll_interval_s=5.0)`，方法 `set_proxy(server: str | None) -> None`、`tick(now: float) -> list[object]`
  - `MonitorThread(state, inbox, clock=time.monotonic, tick_interval=1.0)`，方法 `start()`、`stop()`

> **相对规格第 9.1 节的调整**：规格把去抖动放在控制器。这里改放进 `MonitorState`——去抖动的输入就是相邻两次轮询的快照，放在采样点旁边只需一个地方保存状态，且能用确定性的 `tick(now)` 单测，不必起线程。控制器因此只会收到已经稳定的 `NetworkChanged`。

- [ ] **Step 1: 写失败的测试**

`tests/test_monitor.py`：

```python
import queue
import time

from lan_proxy_switcher.monitor import (
    MonitorError,
    MonitorState,
    MonitorThread,
    NetworkChanged,
    ProxyLost,
    ProxyOk,
)
from lan_proxy_switcher.network import Adapter, AdapterKind, AdapterStatus
from lan_proxy_switcher.scanner import ScanHit


def adapter(name="Wi-Fi", ipv4=None, status=AdapterStatus.DISCONNECTED):
    return Adapter(name, 23, "desc", AdapterKind.WIRELESS, status, ipv4, 24 if ipv4 else None, None, None)


def hit_probe(latency=12.0):
    return lambda ip, port, timeout_s: (ScanHit(ip, port, latency), None)


def fail_probe(ip, port, timeout_s):
    return None, 10061


def build(list_adapters=lambda: [], probe=fail_probe, **kwargs):
    options = dict(
        monitor_interval_s=30,
        scan_timeout_ms=500,
        proxy_check_failures=3,
        network_poll_interval_s=5.0,
    )
    options.update(kwargs)
    return MonitorState(list_adapters=list_adapters, probe=probe, **options)


# ---------- 网络变化去抖动 ----------

def test_first_poll_only_establishes_a_baseline():
    state = build(list_adapters=lambda: [adapter()])

    assert state.tick(0.0) == []


def test_change_is_reported_after_two_identical_polls():
    state = build(list_adapters=lambda: [adapter()])

    state.tick(0.0)
    events = state.tick(5.0)

    assert events == [NetworkChanged((adapter(),))]


def test_stable_network_is_reported_only_once():
    state = build(list_adapters=lambda: [adapter()])

    state.tick(0.0)
    state.tick(5.0)

    assert state.tick(10.0) == []


def test_mid_flight_change_is_not_reported_until_it_settles():
    """Wi-Fi 重连过程中会连抛好几次变化，不能每次都触发重扫。"""
    current = [adapter()]
    state = build(list_adapters=lambda: list(current))
    state.tick(0.0)
    state.tick(5.0)  # 基线已上报

    current[:] = [adapter(ipv4="192.168.1.5", status=AdapterStatus.UP)]
    assert state.tick(10.0) == []  # 变了，但还不稳定

    events = state.tick(15.0)
    assert events == [NetworkChanged((adapter(ipv4="192.168.1.5", status=AdapterStatus.UP),))]


def test_network_is_not_polled_more_often_than_the_interval():
    calls = []
    state = build(list_adapters=lambda: calls.append(1) or [adapter()])

    state.tick(0.0)
    state.tick(1.0)
    state.tick(2.0)

    assert len(calls) == 1


def test_adapter_query_failure_becomes_an_error_event():
    def boom():
        raise RuntimeError("PowerShell 挂了")

    state = build(list_adapters=boom)

    events = state.tick(0.0)

    assert len(events) == 1
    assert isinstance(events[0], MonitorError)
    assert "PowerShell 挂了" in events[0].message


# ---------- 代理探测 ----------

def test_no_proxy_means_no_probing():
    calls = []
    state = build(probe=lambda *args: calls.append(args) or (None, 10061))

    state.tick(0.0)
    state.tick(30.0)
    state.tick(60.0)

    assert calls == []


def test_successful_check_emits_proxy_ok():
    state = build(probe=hit_probe(12.5))
    state.set_proxy("172.20.10.1:7890")

    state.tick(0.0)
    events = state.tick(30.0)

    assert events == [ProxyOk("172.20.10.1:7890", 12.5)]


def test_probe_receives_ip_port_and_timeout_in_seconds():
    calls = []

    def recording_probe(ip, port, timeout_s):
        calls.append((ip, port, timeout_s))
        return ScanHit(ip, port, 1.0), None

    state = build(probe=recording_probe, scan_timeout_ms=500)
    state.set_proxy("172.20.10.1:7890")
    state.tick(0.0)
    state.tick(30.0)

    assert calls == [("172.20.10.1", 7890, 0.5)]


def test_proxy_lost_only_after_the_configured_number_of_failures():
    state = build(probe=fail_probe, proxy_check_failures=3)
    state.set_proxy("10.0.0.20:7890")
    state.tick(0.0)

    assert state.tick(30.0) == []
    assert state.tick(60.0) == []
    assert state.tick(90.0) == [ProxyLost("10.0.0.20:7890", 3)]


def test_a_success_resets_the_failure_counter():
    outcomes = [(None, 10061), (None, 10061), (ScanHit("10.0.0.20", 7890, 9.0), None), (None, 10061)]
    state = build(probe=lambda *args: outcomes.pop(0), proxy_check_failures=3)
    state.set_proxy("10.0.0.20:7890")
    state.tick(0.0)

    state.tick(30.0)
    state.tick(60.0)
    state.tick(90.0)

    assert state.tick(120.0) == []  # 计数已清零，这才是第 1 次失败


def test_changing_the_proxy_resets_the_failure_counter():
    state = build(probe=fail_probe, proxy_check_failures=2)
    state.set_proxy("10.0.0.20:7890")
    state.tick(0.0)
    state.tick(30.0)

    state.set_proxy("10.0.0.30:7890")
    state.tick(60.0)

    assert state.tick(90.0) == []


# ---------- 线程外壳 ----------

def test_thread_pumps_events_into_the_inbox_and_stops():
    inbox = queue.Queue()
    state = build(list_adapters=lambda: [adapter()], network_poll_interval_s=0.0)
    thread = MonitorThread(state, inbox, tick_interval=0.01)

    thread.start()
    event = inbox.get(timeout=2.0)
    thread.stop()

    assert isinstance(event, NetworkChanged)
    assert not thread.is_alive()


def test_thread_survives_a_tick_exception():
    inbox = queue.Queue()

    class Exploding:
        def tick(self, now):
            raise RuntimeError("tick 炸了")

    thread = MonitorThread(Exploding(), inbox, tick_interval=0.01)
    thread.start()
    event = inbox.get(timeout=2.0)
    thread.stop()

    assert isinstance(event, MonitorError)
    assert "tick 炸了" in event.message
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_monitor.py -v`
Expected: FAIL，`ModuleNotFoundError: lan_proxy_switcher.monitor`

- [ ] **Step 3: 写实现**

`lan_proxy_switcher/monitor.py`：

```python
"""后台监控：代理 TCP 健康探测 + 网络变化检测。

两种节奏跑在同一个线程里：网络快照每 5 秒一次，代理探测每 monitorInterval 一次。
仍然只做 TCP Connect，禁止通过代理访问任何 URL。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from .network import Adapter
from .scanner import ScanHit

ProbeFn = Callable[[str, int, float], "tuple[ScanHit | None, int | None]"]


@dataclass(frozen=True)
class NetworkChanged:
    adapters: tuple[Adapter, ...]


@dataclass(frozen=True)
class ProxyOk:
    server: str
    latency_ms: float


@dataclass(frozen=True)
class ProxyLost:
    server: str
    failures: int


@dataclass(frozen=True)
class MonitorError:
    message: str


class MonitorState:
    def __init__(
        self,
        *,
        list_adapters: Callable[[], Sequence[Adapter]],
        probe: ProbeFn,
        monitor_interval_s: int,
        scan_timeout_ms: int,
        proxy_check_failures: int,
        network_poll_interval_s: float = 5.0,
    ) -> None:
        self._list_adapters = list_adapters
        self._probe = probe
        self._monitor_interval_s = monitor_interval_s
        self._scan_timeout_ms = scan_timeout_ms
        self._proxy_check_failures = proxy_check_failures
        self._network_poll_interval_s = network_poll_interval_s

        self._previous_poll: tuple[Adapter, ...] | None = None
        self._reported: tuple[Adapter, ...] | None = None
        self._next_network_poll: float | None = None
        self._next_proxy_check: float | None = None
        self._server: str | None = None
        self._failures = 0

    def set_proxy(self, server: str | None) -> None:
        self._server = server
        self._failures = 0
        self._next_proxy_check = None

    def tick(self, now: float) -> list[object]:
        events: list[object] = []

        if self._next_network_poll is None or now >= self._next_network_poll:
            events.extend(self._poll_network())
            self._next_network_poll = now + self._network_poll_interval_s

        if self._server is not None:
            if self._next_proxy_check is None:
                self._next_proxy_check = now + self._monitor_interval_s
            elif now >= self._next_proxy_check:
                events.extend(self._check_proxy())
                self._next_proxy_check = now + self._monitor_interval_s

        return events

    def _poll_network(self) -> list[object]:
        try:
            current = tuple(self._list_adapters())
        except Exception as exc:  # 查询失败不能让监控线程死掉
            return [MonitorError(f"网卡查询失败：{exc}")]

        events: list[object] = []
        # 连续两次快照一致才认为网络已稳定，避免 Wi-Fi 重连途中反复触发重扫
        if (
            self._previous_poll is not None
            and current == self._previous_poll
            and current != self._reported
        ):
            self._reported = current
            events.append(NetworkChanged(current))
        self._previous_poll = current
        return events

    def _check_proxy(self) -> list[object]:
        assert self._server is not None
        host, _, port = self._server.rpartition(":")
        try:
            hit, _code = self._probe(host, int(port), self._scan_timeout_ms / 1000.0)
        except Exception as exc:
            return [MonitorError(f"代理探测失败：{exc}")]

        if hit is not None:
            self._failures = 0
            return [ProxyOk(self._server, hit.latency_ms)]

        self._failures += 1
        if self._failures >= self._proxy_check_failures:
            failures = self._failures
            self._failures = 0
            return [ProxyLost(self._server, failures)]
        return []


class MonitorThread:
    """MonitorState 的线程外壳。1 秒粒度，退出最多 1 秒收尾。"""

    def __init__(
        self,
        state,
        inbox: "queue.Queue[object]",
        clock: Callable[[], float] = time.monotonic,
        tick_interval: float = 1.0,
    ) -> None:
        self._state = state
        self._inbox = inbox
        self._clock = clock
        self._tick_interval = tick_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.wait(self._tick_interval):
            try:
                for event in self._state.tick(self._clock()):
                    self._inbox.put(event)
            except Exception as exc:
                self._inbox.put(MonitorError(f"监控线程异常：{exc}"))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add lan_proxy_switcher/monitor.py tests/test_monitor.py
git commit -m "feat(monitor): 代理 TCP 健康探测与带去抖动的网络变化检测"
```

---

### Task 9: controller.py 核心状态机（启动、扫描、设代理）

**Files:**
- Create: `lan_proxy_switcher/controller.py`
- Create: `tests/conftest.py`
- Test: `tests/test_controller_scan.py`

**Interfaces:**
- Consumes: `config.Config`、`network.NetworkService` / `Adapter` / `select_active` / `current_identity`、`proxy.ProxyService` / `ProxyState`、`scanner.discover` / `scan` / `select_best` / `ScanHit` / `DiscoveryResult`
- Produces:
  - `State` 枚举：`IDLE` / `SCANNING` / `PROXY_ACTIVE` / `NO_PROXY` / `SWITCHING`
  - inbox 消息：`Start`、`ScanRequested(adapter_index: int | None = None)`、`ScanFinished(result, adapter, token)`、`UseHitRequested(hit)`、`DisableProxyRequested`、`RefreshAdaptersRequested`、`Shutdown`
  - UI 事件：`LogLine(text)`、`AdaptersUpdated(adapters, active_index)`、`ScanHitFound(hit)`、`ScanResults(hits)`、`ProxyStatus(server, status)`、`StateChanged(state)`
  - `Controller(cfg, network, proxy, monitor, ui, spawn, *, identity=..., discover=..., scan=..., sleep=...)`，方法 `post(msg)`、`run()`、`process_pending(limit=100)`
  - `monitor` 只需提供 `set_proxy(server: str | None)`，即 Task 8 的 `MonitorState`
  - `spawn(fn)`：生产环境起后台线程，测试里同步调用

- [ ] **Step 1: 写公共 Fake（Task 10、11 复用）**

`tests/conftest.py`：

```python
import queue
from dataclasses import replace

import pytest

from lan_proxy_switcher.config import Config
from lan_proxy_switcher.network import Adapter, AdapterKind, AdapterStatus
from lan_proxy_switcher.proxy import ProxyState


def make_adapter(
    name="Ethernet 10",
    index=32,
    kind=AdapterKind.WIRED,
    status=AdapterStatus.UP,
    ipv4="172.20.10.7",
    prefix_length=28,
    gateway="172.20.10.1",
    metric=25,
):
    return Adapter(name, index, f"{name} 描述", kind, status, ipv4, prefix_length, gateway, metric)


WIFI = make_adapter(
    name="Wi-Fi",
    index=23,
    kind=AdapterKind.WIRELESS,
    status=AdapterStatus.DISCONNECTED,
    ipv4=None,
    prefix_length=None,
    gateway=None,
    metric=None,
)


class FakeNetwork:
    def __init__(self, adapters):
        self.adapters = list(adapters)
        self.switches = []
        self.fail_on = set()

    def list_adapters(self):
        return list(self.adapters)

    def set_adapter_enabled(self, index, enabled):
        if index in self.fail_on:
            raise RuntimeError(f"网卡 {index} 拒绝操作")
        self.switches.append((index, enabled))
        self.adapters = [
            replace(a, status=AdapterStatus.UP if enabled else AdapterStatus.DISABLED)
            if a.index == index
            else a
            for a in self.adapters
        ]


class FakeProxy:
    def __init__(self, state=None):
        self.state = state or ProxyState(False, None, None, None)
        self.applied = []
        self.disabled = 0
        self.restored = []

    def read(self):
        return self.state

    def apply(self, server):
        self.applied.append(server)
        self.state = ProxyState(True, server, self.state.override, None)

    def disable(self):
        self.disabled += 1
        self.state = replace(self.state, enable=False)

    def restore(self, state):
        self.restored.append(state)
        self.state = state


class FakeMonitor:
    def __init__(self):
        self.servers = []

    def set_proxy(self, server):
        self.servers.append(server)


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def ui():
    return queue.Queue()


def drain(ui_queue):
    events = []
    while not ui_queue.empty():
        events.append(ui_queue.get_nowait())
    return events


def events_of(ui_queue, kind):
    return [event for event in drain(ui_queue) if isinstance(event, kind)]
```

- [ ] **Step 2: 写失败的测试**

`tests/test_controller_scan.py`：

```python
import threading
from dataclasses import replace

import pytest

from conftest import FakeMonitor, FakeNetwork, FakeProxy, WIFI, drain, make_adapter
from lan_proxy_switcher.controller import (
    AdaptersUpdated,
    Controller,
    DisableProxyRequested,
    LogLine,
    ProxyStatus,
    RefreshAdaptersRequested,
    ScanHitFound,
    ScanRequested,
    ScanResults,
    Shutdown,
    Start,
    State,
    StateChanged,
    UseHitRequested,
)
from lan_proxy_switcher.proxy import ProxyState
from lan_proxy_switcher.scanner import DiscoveryResult, ScanHit


def build(cfg, ui, adapters=None, discovery=None, proxy=None, on_scan=None):
    """装一个全 Fake 的控制器；spawn 同步执行，扫描结果预设。"""
    network = FakeNetwork(adapters if adapters is not None else [make_adapter(), WIFI])
    result = discovery if discovery is not None else DiscoveryResult((), "subnet", "命中 0 个")

    def fake_discover(adapter, ports, scan_fn, cancel, max_hosts_prefix=24):
        if on_scan is not None:
            on_scan(adapter, scan_fn, cancel)
        return result

    controller = Controller(
        cfg,
        network,
        proxy or FakeProxy(),
        FakeMonitor(),
        ui,
        spawn=lambda fn: fn(),
        identity=lambda: "DESKTOP\\llooo S-1-5-21-1",
        discover=fake_discover,
        scan=lambda *args, **kwargs: None,
    )
    return controller, network


def run_all(controller, *messages):
    for message in messages:
        controller.post(message)
    controller.process_pending()


HIT_7890 = ScanHit("172.20.10.1", 7890, 12.0)
HIT_1080 = ScanHit("172.20.10.1", 1080, 15.0)


# ---------- 启动 ----------

def test_start_logs_the_running_account(cfg, ui):
    controller, _ = build(cfg, ui)

    run_all(controller, Start())

    logs = [e.text for e in drain(ui) if isinstance(e, LogLine)]
    assert any("S-1-5-21-1" in line for line in logs)


def test_start_snapshots_the_existing_proxy(cfg, ui):
    before = ProxyState(True, "1.2.3.4:8080", "<local>", None)
    controller, _ = build(cfg, ui, proxy=FakeProxy(before))

    run_all(controller, Start())

    assert controller.startup_proxy == before


def test_start_publishes_the_adapter_list(cfg, ui):
    controller, _ = build(cfg, ui)

    run_all(controller, Start())

    updates = [e for e in drain(ui) if isinstance(e, AdaptersUpdated)]
    assert updates
    assert [a.name for a in updates[0].adapters] == ["Ethernet 10", "Wi-Fi"]
    assert updates[0].active_index == 32


def test_auto_scan_false_goes_straight_to_no_proxy(cfg, ui):
    controller, _ = build(replace(cfg, auto_scan=False), ui)

    run_all(controller, Start())

    assert controller.state is State.NO_PROXY


# ---------- 扫描 → 设代理 ----------

def test_a_hit_is_applied_as_the_system_proxy(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(
        cfg, ui, proxy=proxy, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中")
    )

    run_all(controller, Start())

    assert proxy.applied == ["172.20.10.1:7890"]
    assert controller.state is State.PROXY_ACTIVE


def test_prefer_port_decides_which_hit_is_applied(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(
        cfg, ui, proxy=proxy, discovery=DiscoveryResult((HIT_1080, HIT_7890), "subnet", "命中 2 个")
    )

    run_all(controller, Start())

    assert proxy.applied == ["172.20.10.1:7890"]


def test_monitor_is_told_about_the_new_proxy(cfg, ui):
    controller, _ = build(cfg, ui, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中"))

    run_all(controller, Start())

    assert controller.monitor.servers == ["172.20.10.1:7890"]


def test_scan_results_reach_the_ui(cfg, ui):
    controller, _ = build(
        cfg, ui, discovery=DiscoveryResult((HIT_7890, HIT_1080), "subnet", "命中 2 个")
    )

    run_all(controller, Start())

    results = [e for e in drain(ui) if isinstance(e, ScanResults)]
    assert results[-1].hits == (HIT_7890, HIT_1080)


def test_hits_are_streamed_while_scanning(cfg, ui):
    """命中要即时推给 GUI，不能等整轮扫完。"""

    def emit_hit(adapter, scan_fn, cancel):
        scan_fn([(HIT_7890.ip, HIT_7890.port)])

    controller, _ = build(
        cfg,
        ui,
        discovery=DiscoveryResult((HIT_7890,), "subnet", "命中 1 个"),
        on_scan=emit_hit,
    )
    # scan_fn 内部会调用注入的 scan；这里换成会回调 on_hit 的假实现
    controller._scan = lambda targets, timeout, conc, cancel, on_hit=None: (
        on_hit(HIT_7890) if on_hit else None
    )

    run_all(controller, Start())

    assert [e.hit for e in drain(ui) if isinstance(e, ScanHitFound)] == [HIT_7890]


def test_no_hit_leaves_proxy_untouched_by_default(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(cfg, ui, proxy=proxy)

    run_all(controller, Start())

    assert proxy.applied == []
    assert proxy.disabled == 0
    assert controller.state is State.NO_PROXY


def test_disable_proxy_when_unavailable_turns_it_off(cfg, ui):
    proxy = FakeProxy(ProxyState(True, "1.2.3.4:80", None, None))
    controller, _ = build(replace(cfg, disable_proxy_when_unavailable=True), ui, proxy=proxy)

    run_all(controller, Start())

    assert proxy.disabled == 1
    assert controller.state is State.NO_PROXY


def test_auto_set_proxy_false_lists_results_without_applying(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(
        replace(cfg, auto_set_proxy=False),
        ui,
        proxy=proxy,
        discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中"),
    )

    run_all(controller, Start())

    assert proxy.applied == []
    assert controller.state is State.NO_PROXY
    assert [e for e in drain(ui) if isinstance(e, ScanResults)][-1].hits == (HIT_7890,)


def test_scan_of_a_specific_adapter_overrides_auto_selection(cfg, ui):
    seen = []
    controller, _ = build(
        replace(cfg, auto_scan=False), ui, on_scan=lambda a, s, c: seen.append(a.name)
    )

    run_all(controller, Start(), ScanRequested(adapter_index=23))

    assert seen == ["Wi-Fi"]


def test_scan_without_any_usable_adapter_reports_it(cfg, ui):
    controller, _ = build(replace(cfg, auto_scan=False), ui, adapters=[WIFI])

    run_all(controller, Start(), ScanRequested())

    logs = [e.text for e in drain(ui) if isinstance(e, LogLine)]
    assert any("没有可用于扫描的网卡" in line for line in logs)
    assert controller.state is State.NO_PROXY


def test_stale_scan_results_are_discarded(cfg, ui):
    """网卡切换/网络变化会掐掉在途扫描，晚到的结果不许覆盖新状态。"""
    from lan_proxy_switcher.controller import ScanFinished

    proxy = FakeProxy()
    controller, _ = build(replace(cfg, auto_scan=False), ui, proxy=proxy)
    stale = ScanFinished(
        DiscoveryResult((HIT_7890,), "subnet", "过期结果"), make_adapter(), object()
    )

    run_all(controller, Start(), stale)

    assert proxy.applied == []


def test_scan_state_is_published(cfg, ui):
    controller, _ = build(cfg, ui, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中"))

    run_all(controller, Start())

    states = [e.state for e in drain(ui) if isinstance(e, StateChanged)]
    assert State.SCANNING in states
    assert states[-1] is State.PROXY_ACTIVE


# ---------- 手动操作 ----------

def test_use_hit_applies_the_chosen_result(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(replace(cfg, auto_scan=False), ui, proxy=proxy)

    run_all(controller, Start(), UseHitRequested(HIT_1080))

    assert proxy.applied == ["172.20.10.1:1080"]
    assert controller.state is State.PROXY_ACTIVE


def test_disable_proxy_request_turns_it_off(cfg, ui):
    proxy = FakeProxy()
    controller, _ = build(
        cfg, ui, proxy=proxy, discovery=DiscoveryResult((HIT_7890,), "gateway", "网关命中")
    )

    run_all(controller, Start(), DisableProxyRequested())

    assert proxy.disabled == 1
    assert controller.state is State.NO_PROXY
    assert controller.monitor.servers[-1] is None
    assert [e for e in drain(ui) if isinstance(e, ProxyStatus)][-1].server is None


def test_refresh_republishes_the_adapter_list(cfg, ui):
    controller, network = build(replace(cfg, auto_scan=False), ui)
    run_all(controller, Start())
    drain(ui)
    network.adapters = [WIFI]

    run_all(controller, RefreshAdaptersRequested())

    updates = [e for e in drain(ui) if isinstance(e, AdaptersUpdated)]
    assert [a.name for a in updates[-1].adapters] == ["Wi-Fi"]


# ---------- 异常与退出 ----------

def test_a_failing_handler_logs_and_falls_back_to_no_proxy(cfg, ui):
    class ExplodingProxy(FakeProxy):
        def apply(self, server):
            raise RuntimeError("拒绝访问注册表")

    controller, _ = build(replace(cfg, auto_scan=False), ui, proxy=ExplodingProxy())

    run_all(controller, Start(), UseHitRequested(HIT_7890))

    logs = [e.text for e in drain(ui) if isinstance(e, LogLine)]
    assert any("拒绝访问注册表" in line for line in logs)
    assert controller.state is State.NO_PROXY


def test_shutdown_restores_the_startup_proxy_when_configured(cfg, ui):
    before = ProxyState(True, "1.2.3.4:8080", None, None)
    proxy = FakeProxy(before)
    controller, _ = build(replace(cfg, restore_proxy_on_exit=True), ui, proxy=proxy)

    run_all(controller, Start(), Shutdown())

    assert proxy.restored == [before]


def test_shutdown_leaves_the_proxy_alone_by_default(cfg, ui):
    proxy = FakeProxy(ProxyState(True, "1.2.3.4:8080", None, None))
    controller, _ = build(cfg, ui, proxy=proxy)

    run_all(controller, Start(), Shutdown())

    assert proxy.restored == []
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_controller_scan.py -v`
Expected: FAIL，`ModuleNotFoundError: lan_proxy_switcher.controller`

- [ ] **Step 4: 写实现**

`lan_proxy_switcher/controller.py`：

```python
"""唯一的状态机。串行消费一个 inbox 队列，因此全程无需任何锁。

耗时操作（扫描、网卡切换）交给临时线程，完成后把结果投回 inbox。
本模块不导入 tkinter。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from . import scanner
from .config import Config
from .network import Adapter, current_identity, select_active
from .proxy import ProxyState
from .scanner import DiscoveryResult, ScanHit


class State(Enum):
    IDLE = "空闲"
    SCANNING = "扫描中"
    PROXY_ACTIVE = "代理生效"
    NO_PROXY = "未使用代理"
    SWITCHING = "切换网卡中"


# ---------- inbox 消息 ----------

@dataclass(frozen=True)
class Start:
    pass


@dataclass(frozen=True)
class ScanRequested:
    adapter_index: int | None = None


@dataclass(frozen=True)
class ScanFinished:
    result: DiscoveryResult
    adapter: Adapter
    token: object


@dataclass(frozen=True)
class UseHitRequested:
    hit: ScanHit


@dataclass(frozen=True)
class DisableProxyRequested:
    pass


@dataclass(frozen=True)
class RefreshAdaptersRequested:
    pass


@dataclass(frozen=True)
class Shutdown:
    pass


# ---------- UI 事件 ----------

@dataclass(frozen=True)
class LogLine:
    text: str


@dataclass(frozen=True)
class AdaptersUpdated:
    adapters: tuple[Adapter, ...]
    active_index: int | None


@dataclass(frozen=True)
class ScanHitFound:
    hit: ScanHit


@dataclass(frozen=True)
class ScanResults:
    hits: tuple[ScanHit, ...]


@dataclass(frozen=True)
class ProxyStatus:
    server: str | None
    status: str


@dataclass(frozen=True)
class StateChanged:
    state: State


class Controller:
    def __init__(
        self,
        cfg: Config,
        network,
        proxy,
        monitor,
        ui: "queue.Queue[object]",
        spawn: Callable[[Callable[[], None]], None],
        *,
        identity: Callable[[], str] = current_identity,
        discover=scanner.discover,
        scan=scanner.scan,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cfg = cfg
        self._network = network
        self._proxy = proxy
        self.monitor = monitor
        self._ui = ui
        self._spawn = spawn
        self._identity = identity
        self._discover = discover
        self._scan = scan
        self._sleep = sleep

        self.inbox: "queue.Queue[object]" = queue.Queue()
        self.state = State.IDLE
        self.adapters: tuple[Adapter, ...] = ()
        self.hits: tuple[ScanHit, ...] = ()
        self.current_server: str | None = None
        self.startup_proxy: ProxyState | None = None

        self._scan_token: object | None = None
        self._cancel = threading.Event()

        self._handlers: dict[type, Callable[[object], None]] = {
            Start: self._on_start,
            ScanRequested: self._on_scan_requested,
            ScanFinished: self._on_scan_finished,
            UseHitRequested: self._on_use_hit,
            DisableProxyRequested: self._on_disable_proxy,
            RefreshAdaptersRequested: self._on_refresh,
            Shutdown: self._on_shutdown,
        }

    # ---------- 队列 ----------

    def post(self, message: object) -> None:
        self.inbox.put(message)

    def run(self) -> None:
        while True:
            message = self.inbox.get()
            self._dispatch(message)
            if isinstance(message, Shutdown):
                return

    def process_pending(self, limit: int = 100) -> None:
        """测试与退出收尾用：把当前排队的消息处理完。"""
        for _ in range(limit):
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                return
            self._dispatch(message)

    def _dispatch(self, message: object) -> None:
        handler = self._handlers.get(type(message))
        if handler is None:
            self._log(f"忽略未知消息：{type(message).__name__}")
            return
        try:
            handler(message)
        except Exception as exc:  # worker 的异常也会经这里落到日志
            self._log(f"处理 {type(message).__name__} 出错：{exc}")
            self._set_state(State.NO_PROXY)

    # ---------- 工具 ----------

    def _log(self, text: str) -> None:
        self._ui.put(LogLine(f"{time.strftime('%H:%M:%S')} {text}"))

    def _set_state(self, state: State) -> None:
        self.state = state
        self._ui.put(StateChanged(state))

    def _publish_adapters(self) -> list[Adapter]:
        self.adapters = tuple(self._network.list_adapters())
        active = select_active(self.adapters)
        self._ui.put(AdaptersUpdated(self.adapters, active.index if active else None))
        return list(self.adapters)

    def _cancel_scan(self) -> None:
        self._cancel.set()
        self._scan_token = None

    def _apply_hit(self, hit: ScanHit) -> None:
        server = f"{hit.ip}:{hit.port}"
        self._proxy.apply(server)
        self.current_server = server
        self.monitor.set_proxy(server)
        self._log(f"设置 Windows 系统代理成功，已校验：{server}")
        self._ui.put(ProxyStatus(server, "TCP 连接正常"))
        self._set_state(State.PROXY_ACTIVE)

    def _turn_proxy_off(self) -> None:
        self._proxy.disable()
        self.current_server = None
        self.monitor.set_proxy(None)
        self._log("已关闭 Windows 系统代理")
        self._ui.put(ProxyStatus(None, "未使用代理"))
        self._set_state(State.NO_PROXY)

    # ---------- 处理器 ----------

    def _on_start(self, _message: object) -> None:
        try:
            self._log(f"运行账户：{self._identity()}")
        except Exception as exc:
            self._log(f"无法获取运行账户：{exc}")

        self.startup_proxy = self._proxy.read()
        if self.startup_proxy.enable and self.startup_proxy.server:
            self._log(f"启动前系统代理：已启用 {self.startup_proxy.server}")
        else:
            self._log("启动前系统代理：未启用")

        self._publish_adapters()
        if self._cfg.auto_scan:
            self._begin_scan(None)
        else:
            self._log("autoScan=false，等待手动扫描")
            self._set_state(State.NO_PROXY)

    def _on_scan_requested(self, message: ScanRequested) -> None:
        self._begin_scan(message.adapter_index)

    def _begin_scan(self, adapter_index: int | None) -> None:
        self._cancel_scan()
        adapters = self._publish_adapters()

        if adapter_index is None:
            target = select_active(adapters)
        else:
            target = next((a for a in adapters if a.index == adapter_index), None)

        if target is None:
            self._log("没有可用于扫描的网卡")
            self._set_state(State.NO_PROXY)
            return

        cancel = threading.Event()
        token = object()
        self._cancel = cancel
        self._scan_token = token
        self.hits = ()
        self._ui.put(ScanResults(()))
        self._set_state(State.SCANNING)

        self._log(f"当前网卡：{target.name}（{target.description}）")
        if target.ipv4:
            self._log(f"IP：{target.ipv4}/{target.prefix_length}")
        if target.gateway:
            self._log(f"优先检测 Gateway：{target.gateway}")

        cfg = self._cfg
        ui = self._ui
        scan = self._scan

        def job() -> None:
            def scan_fn(targets):
                return scan(
                    targets,
                    cfg.scan_timeout_ms,
                    cfg.scan_concurrency,
                    cancel,
                    on_hit=lambda hit: ui.put(ScanHitFound(hit)),
                )

            try:
                result = self._discover(target, cfg.ports, scan_fn, cancel)
            except Exception as exc:
                result = DiscoveryResult((), "error", f"扫描异常：{exc}")
            self.post(ScanFinished(result, target, token))

        self._spawn(job)

    def _on_scan_finished(self, message: ScanFinished) -> None:
        if message.token is not self._scan_token:
            return  # 已被新一轮扫描取代，丢弃

        self.hits = message.result.hits
        self._ui.put(ScanResults(self.hits))
        self._log(message.result.message)

        if message.result.phase == "cancelled":
            return

        best = scanner.select_best(self.hits, self._cfg.prefer_port, self._cfg.ports)
        if best is None:
            self._log("未发现可用代理")
            if self._cfg.disable_proxy_when_unavailable:
                self._turn_proxy_off()
            else:
                self._set_state(State.NO_PROXY)
            return

        if self._cfg.auto_set_proxy:
            self._apply_hit(best)
        else:
            self._log("autoSetProxy=false，已列出扫描结果，等待手动选择")
            self._set_state(State.NO_PROXY)

    def _on_use_hit(self, message: UseHitRequested) -> None:
        self._apply_hit(message.hit)

    def _on_disable_proxy(self, _message: object) -> None:
        self._turn_proxy_off()

    def _on_refresh(self, _message: object) -> None:
        self._publish_adapters()

    def _on_shutdown(self, _message: object) -> None:
        self._cancel_scan()
        if self._cfg.restore_proxy_on_exit and self.startup_proxy is not None:
            self._proxy.restore(self.startup_proxy)
            self._log("已还原启动前的代理状态")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add lan_proxy_switcher/controller.py tests/conftest.py tests/test_controller_scan.py
git commit -m "feat(controller): 无锁状态机的启动、扫描与系统代理设置流程"
```

---

### Task 10: controller.py 网卡切换、网络变化与代理失效

**Files:**
- Modify: `lan_proxy_switcher/controller.py`（追加消息类型与处理器）
- Modify: `tests/conftest.py`（给 `FakeNetwork` 加 `ip_on_enable`，加 `FakeClock`）
- Test: `tests/test_controller_switch.py`

**Interfaces:**
- Consumes: Task 9 的全部内容；Task 8 的 `NetworkChanged` / `ProxyOk` / `ProxyLost` / `MonitorError`
- Produces:
  - 新 inbox 消息：`SwitchRequested(adapter_index: int)`、`SwitchFinished(ok: bool, message: str, adapter_index: int, token: object)`
  - `Controller` 新增构造参数 `clock: Callable[[], float] = time.monotonic`
  - `Controller.DISABLE_TIMEOUT_S = 15.0`、`Controller.ENABLE_TIMEOUT_S = 30.0`
  - 至此控制器覆盖规格第 9.2 节状态机的全部转换

- [ ] **Step 1: 扩展公共 Fake**

把 `tests/conftest.py` 里 `FakeNetwork` 的 `__init__` 与 `set_adapter_enabled` 换成：

```python
class FakeNetwork:
    def __init__(self, adapters, ip_on_enable=None):
        self.adapters = list(adapters)
        self.switches = []
        self.fail_on = set()
        # 启用某张网卡后它应当拿到的 (ipv4, prefix_length)；不给就保持原样
        self.ip_on_enable = ip_on_enable or {}

    def list_adapters(self):
        return list(self.adapters)

    def set_adapter_enabled(self, index, enabled):
        if index in self.fail_on:
            raise RuntimeError(f"网卡 {index} 拒绝操作")
        self.switches.append((index, enabled))
        updated = []
        for adapter in self.adapters:
            if adapter.index != index:
                updated.append(adapter)
                continue
            if enabled:
                ipv4, prefix = self.ip_on_enable.get(index, (adapter.ipv4, adapter.prefix_length))
                updated.append(
                    replace(adapter, status=AdapterStatus.UP, ipv4=ipv4, prefix_length=prefix)
                )
            else:
                updated.append(
                    replace(adapter, status=AdapterStatus.DISABLED, ipv4=None, prefix_length=None)
                )
        self.adapters = updated
```

并在文件末尾追加：

```python
class FakeClock:
    """可控时钟：sleep 直接推进时间，等待循环因而瞬间收敛。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
```

- [ ] **Step 2: 写失败的测试**

`tests/test_controller_switch.py`：

```python
from dataclasses import replace

from conftest import FakeClock, FakeMonitor, FakeNetwork, FakeProxy, WIFI, drain, make_adapter
from lan_proxy_switcher.controller import (
    AdaptersUpdated,
    Controller,
    LogLine,
    ProxyStatus,
    ScanRequested,
    Start,
    State,
    SwitchRequested,
)
from lan_proxy_switcher.monitor import MonitorError, NetworkChanged, ProxyLost, ProxyOk
from lan_proxy_switcher.network import AdapterStatus
from lan_proxy_switcher.scanner import DiscoveryResult, ScanHit

HIT = ScanHit("172.20.10.1", 7890, 12.0)
ETHERNET = make_adapter()


def build(cfg, ui, adapters=None, ip_on_enable=None, discovery=None, on_scan=None):
    clock = FakeClock()
    network = FakeNetwork(
        adapters if adapters is not None else [ETHERNET, WIFI], ip_on_enable=ip_on_enable
    )
    result = discovery if discovery is not None else DiscoveryResult((), "subnet", "命中 0 个")

    def fake_discover(adapter, ports, scan_fn, cancel, max_hosts_prefix=24):
        if on_scan is not None:
            on_scan(adapter, scan_fn, cancel)
        return result

    controller = Controller(
        replace(cfg, auto_scan=False),
        network,
        FakeProxy(),
        FakeMonitor(),
        ui,
        spawn=lambda fn: fn(),
        identity=lambda: "DESKTOP\\llooo S-1-5-21-1",
        discover=fake_discover,
        scan=lambda *args, **kwargs: None,
        clock=clock,
        sleep=clock.sleep,
    )
    return controller, network, clock


def run_all(controller, *messages):
    for message in messages:
        controller.post(message)
    controller.process_pending()


def logs(ui):
    return [event.text for event in drain(ui) if isinstance(event, LogLine)]


# ---------- 网卡切换 ----------

def test_switch_disables_others_before_enabling_the_target(cfg, ui):
    controller, network, _ = build(cfg, ui, ip_on_enable={23: ("192.168.1.5", 24)})

    run_all(controller, Start(), SwitchRequested(23))

    assert network.switches == [(32, False), (23, True)]


def test_switch_then_scans_the_newly_enabled_adapter(cfg, ui):
    seen = []
    controller, _, _ = build(
        cfg,
        ui,
        ip_on_enable={23: ("192.168.1.5", 24)},
        on_scan=lambda adapter, scan_fn, cancel: seen.append(adapter.name),
    )

    run_all(controller, Start(), SwitchRequested(23))

    assert seen == ["Wi-Fi"]


def test_switch_success_reaches_scanning_then_no_proxy(cfg, ui):
    controller, _, _ = build(cfg, ui, ip_on_enable={23: ("192.168.1.5", 24)})

    run_all(controller, Start(), SwitchRequested(23))

    assert controller.state is State.NO_PROXY  # 扫到 0 个，正常落到未使用代理


def test_switch_applies_the_proxy_found_afterwards(cfg, ui):
    controller, _, _ = build(
        cfg,
        ui,
        ip_on_enable={23: ("192.168.1.5", 24)},
        discovery=DiscoveryResult((HIT,), "gateway", "网关命中"),
    )

    run_all(controller, Start(), SwitchRequested(23))

    assert controller.state is State.PROXY_ACTIVE


def test_switch_times_out_waiting_for_an_address(cfg, ui):
    """目标网卡启用了但一直拿不到 IP（DHCP 失败 / Wi-Fi 连不上）。"""
    controller, _, clock = build(cfg, ui, ip_on_enable=None)

    run_all(controller, Start(), SwitchRequested(23))

    assert controller.state is State.NO_PROXY
    assert any("超时" in line for line in logs(ui))
    assert clock.now >= Controller.ENABLE_TIMEOUT_S


def test_timeout_message_says_which_step_got_stuck(cfg, ui):
    controller, _, _ = build(cfg, ui, ip_on_enable=None)

    run_all(controller, Start(), SwitchRequested(23))

    assert any("取得 IPv4" in line for line in logs(ui))


def test_switch_failure_is_reported_not_swallowed(cfg, ui):
    controller, network, _ = build(cfg, ui)
    network.fail_on = {32}

    run_all(controller, Start(), SwitchRequested(23))

    assert controller.state is State.NO_PROXY
    assert any("拒绝操作" in line for line in logs(ui))


def test_adapter_list_is_republished_after_a_switch(cfg, ui):
    controller, _, _ = build(cfg, ui, ip_on_enable={23: ("192.168.1.5", 24)})
    run_all(controller, Start())
    drain(ui)

    run_all(controller, SwitchRequested(23))

    updates = [event for event in drain(ui) if isinstance(event, AdaptersUpdated)]
    wifi = next(a for a in updates[-1].adapters if a.index == 23)
    assert wifi.status is AdapterStatus.UP
    assert wifi.ipv4 == "192.168.1.5"


def test_switch_to_an_unknown_index_is_reported(cfg, ui):
    controller, _, _ = build(cfg, ui)

    run_all(controller, Start(), SwitchRequested(999))

    assert any("999" in line for line in logs(ui))
    assert controller.state is State.NO_PROXY


# ---------- 网络变化 ----------

def test_network_change_triggers_a_rescan(cfg, ui):
    seen = []
    controller, _, _ = build(cfg, ui, on_scan=lambda a, s, c: seen.append(a.name))
    run_all(controller, Start())

    run_all(controller, NetworkChanged((ETHERNET,)))

    assert seen == ["Ethernet 10"]


def test_network_change_is_ignored_while_switching(cfg, ui):
    """切换过程中自己触发的网络变化不许打断自己。"""
    seen = []

    def during_switch(adapter, scan_fn, cancel):
        seen.append(adapter.name)

    controller, _, _ = build(cfg, ui, ip_on_enable={23: ("192.168.1.5", 24)}, on_scan=during_switch)
    run_all(controller, Start())
    controller.state = State.SWITCHING

    run_all(controller, NetworkChanged((ETHERNET,)))

    assert seen == []
    assert any("忽略网络变化" in line for line in logs(ui))


def test_network_change_without_a_usable_adapter_stops_at_no_proxy(cfg, ui):
    controller, network, _ = build(cfg, ui)
    run_all(controller, Start())
    network.adapters = [WIFI]

    run_all(controller, NetworkChanged((WIFI,)))

    assert controller.state is State.NO_PROXY
    assert any("等待网络恢复" in line for line in logs(ui))


def test_network_change_republishes_adapters(cfg, ui):
    controller, _, _ = build(cfg, ui)
    run_all(controller, Start())
    drain(ui)

    run_all(controller, NetworkChanged((ETHERNET,)))

    assert [event for event in drain(ui) if isinstance(event, AdaptersUpdated)]


# ---------- 代理监控事件 ----------

def test_proxy_ok_updates_the_status_line(cfg, ui):
    controller, _, _ = build(cfg, ui)
    run_all(controller, Start())
    drain(ui)

    run_all(controller, ProxyOk("172.20.10.1:7890", 12.4))

    status = [event for event in drain(ui) if isinstance(event, ProxyStatus)][-1]
    assert status.server == "172.20.10.1:7890"
    assert "12" in status.status


def test_proxy_lost_triggers_a_rescan(cfg, ui):
    seen = []
    controller, _, _ = build(cfg, ui, on_scan=lambda a, s, c: seen.append(a.name))
    run_all(controller, Start())

    run_all(controller, ProxyLost("172.20.10.1:7890", 3))

    assert seen == ["Ethernet 10"]
    assert any("判定失效" in line for line in logs(ui))


def test_proxy_lost_reports_the_failure_count(cfg, ui):
    controller, _, _ = build(cfg, ui)
    run_all(controller, Start())
    drain(ui)

    run_all(controller, ProxyLost("172.20.10.1:7890", 3))

    assert any("连续 3 次" in line for line in logs(ui))


def test_monitor_error_is_logged_without_changing_state(cfg, ui):
    controller, _, _ = build(cfg, ui)
    run_all(controller, Start(), ScanRequested())
    before = controller.state
    drain(ui)

    run_all(controller, MonitorError("网卡查询失败：PowerShell 退出码 1"))

    assert controller.state is before
    assert any("监控异常" in line for line in logs(ui))
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_controller_switch.py -v`
Expected: FAIL，`ImportError: cannot import name 'SwitchRequested'`

- [ ] **Step 4: 写实现**

在 `lan_proxy_switcher/controller.py` 顶部 import 段补上：

```python
from .monitor import MonitorError, NetworkChanged, ProxyLost, ProxyOk
from .network import AdapterStatus
```

在 inbox 消息区追加：

```python
@dataclass(frozen=True)
class SwitchRequested:
    adapter_index: int


@dataclass(frozen=True)
class SwitchFinished:
    ok: bool
    message: str
    adapter_index: int
    token: object
```

`Controller` 类体开头加两个常量：

```python
    DISABLE_TIMEOUT_S = 15.0
    ENABLE_TIMEOUT_S = 30.0
```

`__init__` 的签名加一个关键字参数 `clock: Callable[[], float] = time.monotonic`，并在函数体里补上：

```python
        self._clock = clock
        self._switch_token: object | None = None
```

`self._handlers` 字典补齐 6 个条目：

```python
            SwitchRequested: self._on_switch_requested,
            SwitchFinished: self._on_switch_finished,
            NetworkChanged: self._on_network_changed,
            ProxyOk: self._on_proxy_ok,
            ProxyLost: self._on_proxy_lost,
            MonitorError: self._on_monitor_error,
```

在类末尾追加处理器：

```python
    # ---------- 网卡切换 ----------

    @staticmethod
    def _status_of(adapters, index: int) -> AdapterStatus:
        for adapter in adapters:
            if adapter.index == index:
                return adapter.status
        return AdapterStatus.DISABLED  # 已从列表消失，视同已禁用

    @staticmethod
    def _has_address(adapters, index: int) -> bool:
        for adapter in adapters:
            if adapter.index == index:
                return adapter.status is AdapterStatus.UP and adapter.ipv4 is not None
        return False

    def _wait_for(self, predicate, timeout_s: float, description: str) -> None:
        deadline = self._clock() + timeout_s
        while True:
            adapters = self._network.list_adapters()
            if predicate(adapters):
                return
            if self._clock() >= deadline:
                raise TimeoutError(f"{description} 超时（{timeout_s:.0f} 秒）")
            self._log(f"{description}…")
            self._sleep(1.0)

    def _on_switch_requested(self, message: SwitchRequested) -> None:
        self._cancel_scan()
        adapters = self._publish_adapters()
        target = next((a for a in adapters if a.index == message.adapter_index), None)
        if target is None:
            self._log(f"找不到 InterfaceIndex={message.adapter_index} 的网卡")
            self._set_state(State.NO_PROXY)
            return

        others = [a for a in adapters if a.index != target.index]
        token = object()
        self._switch_token = token
        self._set_state(State.SWITCHING)
        self._log(f"切换到 {target.name}，先禁用其余 {len(others)} 张物理网卡")

        def job() -> None:
            try:
                for other in others:
                    self._log(f"禁用 {other.name}")
                    self._network.set_adapter_enabled(other.index, False)
                self._wait_for(
                    lambda ads: all(
                        self._status_of(ads, o.index) is AdapterStatus.DISABLED for o in others
                    ),
                    self.DISABLE_TIMEOUT_S,
                    "等待其余网卡变为已禁用",
                )
                self._log(f"启用 {target.name}")
                self._network.set_adapter_enabled(target.index, True)
                self._wait_for(
                    lambda ads: self._has_address(ads, target.index),
                    self.ENABLE_TIMEOUT_S,
                    f"等待 {target.name} 连接并取得 IPv4",
                )
                self.post(SwitchFinished(True, f"{target.name} 已就绪", target.index, token))
            except Exception as exc:
                self.post(SwitchFinished(False, str(exc), target.index, token))

        self._spawn(job)

    def _on_switch_finished(self, message: SwitchFinished) -> None:
        if message.token is not self._switch_token:
            return
        self._switch_token = None
        self._publish_adapters()
        if message.ok:
            self._log(f"网卡切换完成：{message.message}")
            self._begin_scan(message.adapter_index)
        else:
            self._log(f"网卡切换失败：{message.message}")
            self._set_state(State.NO_PROXY)

    # ---------- 监控事件 ----------

    def _on_network_changed(self, message: NetworkChanged) -> None:
        if self.state is State.SWITCHING:
            self._log("网卡切换进行中，忽略网络变化事件")
            return

        self.adapters = tuple(message.adapters)
        active = select_active(self.adapters)
        self._ui.put(AdaptersUpdated(self.adapters, active.index if active else None))
        self._log("检测到网络变化")
        self._cancel_scan()

        if active is None:
            self._log("当前没有可用网卡，等待网络恢复")
            self._set_state(State.NO_PROXY)
            return
        self._begin_scan(None)

    def _on_proxy_ok(self, message: ProxyOk) -> None:
        self._ui.put(ProxyStatus(message.server, f"TCP 连接正常（{message.latency_ms:.0f}ms）"))

    def _on_proxy_lost(self, message: ProxyLost) -> None:
        self._log(
            f"代理 {message.server} 连续 {message.failures} 次 TCP 连接失败，判定失效，重新扫描"
        )
        self._ui.put(ProxyStatus(message.server, "连接失败"))
        self._begin_scan(None)

    def _on_monitor_error(self, message: MonitorError) -> None:
        self._log(f"监控异常：{message.message}")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add lan_proxy_switcher/controller.py tests/conftest.py tests/test_controller_switch.py
git commit -m "feat(controller): 独占式网卡切换、网络变化响应与代理失效重扫"
```

---

### Task 11: gui.py Tkinter 界面

**Files:**
- Create: `lan_proxy_switcher/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: Task 9/10 的全部 UI 事件与 inbox 消息类型；`Adapter`、`AdapterKind`、`AdapterStatus`、`ScanHit`
- Produces:
  - `AppWindow(root, controller, ui_queue, poll_ms=100)`
  - 公开属性供测试与主程序使用：`adapter_tree`、`hit_tree`、`log_text`、`proxy_label`、`status_label`、`switch_button`、`refresh_button`、`scan_button`、`use_button`、`disable_button`
  - 方法 `pump_once() -> None`（处理当前排队的 UI 事件，不重新调度）、`start_pump() -> None`、`on_close() -> None`
  - `LOG_MAX_LINES = 2000`

- [ ] **Step 1: 写失败的测试**

`tests/test_gui.py`：

```python
import queue

import pytest

from conftest import WIFI, make_adapter
from lan_proxy_switcher.controller import (
    AdaptersUpdated,
    DisableProxyRequested,
    LogLine,
    ProxyStatus,
    RefreshAdaptersRequested,
    ScanHitFound,
    ScanRequested,
    ScanResults,
    Shutdown,
    State,
    StateChanged,
    SwitchRequested,
    UseHitRequested,
)
from lan_proxy_switcher.scanner import ScanHit

tk = pytest.importorskip("tkinter")

from lan_proxy_switcher.gui import AppWindow  # noqa: E402

HIT_7890 = ScanHit("172.20.10.1", 7890, 12.0)
HIT_1080 = ScanHit("172.20.10.1", 1080, 15.4)


class RecordingController:
    def __init__(self):
        self.posted = []

    def post(self, message):
        self.posted.append(message)


@pytest.fixture
def window():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("当前环境没有可用的窗口站")
    root.withdraw()
    ui = queue.Queue()
    app = AppWindow(root, RecordingController(), ui)
    app.ui = ui
    yield app
    root.destroy()


def feed(app, *events):
    for event in events:
        app.ui.put(event)
    app.pump_once()


def rows(tree):
    return [tree.item(item, "values") for item in tree.get_children()]


# ---------- 网卡列表 ----------

def test_adapters_are_rendered_in_chinese(window):
    feed(window, AdaptersUpdated((make_adapter(), WIFI), 32))

    values = rows(window.adapter_tree)
    assert values[0][0] == "有线"
    assert values[0][1] == "Ethernet 10"
    assert values[0][3] == "已连接"
    assert values[0][4] == "172.20.10.7"
    assert values[1][0] == "无线"
    assert values[1][3] == "未连接"


def test_adapter_without_ipv4_shows_a_dash(window):
    feed(window, AdaptersUpdated((WIFI,), None))

    assert rows(window.adapter_tree)[0][4] == "-"


def test_adapter_list_is_replaced_not_appended(window):
    feed(window, AdaptersUpdated((make_adapter(), WIFI), 32))
    feed(window, AdaptersUpdated((WIFI,), None))

    assert len(rows(window.adapter_tree)) == 1


# ---------- 扫描结果 ----------

def test_hits_stream_in_one_by_one(window):
    feed(window, ScanHitFound(HIT_7890), ScanHitFound(HIT_1080))

    assert [row[0:2] for row in rows(window.hit_tree)] == [
        ("172.20.10.1", "7890"),
        ("172.20.10.1", "1080"),
    ]


def test_connect_time_is_rendered_in_milliseconds(window):
    feed(window, ScanHitFound(HIT_1080))

    assert rows(window.hit_tree)[0][2] == "15ms"


def test_scan_results_replace_the_streamed_rows(window):
    feed(window, ScanHitFound(HIT_7890), ScanHitFound(HIT_1080))
    feed(window, ScanResults((HIT_7890,)))

    assert len(rows(window.hit_tree)) == 1


def test_empty_scan_results_clear_the_table(window):
    feed(window, ScanHitFound(HIT_7890))
    feed(window, ScanResults(()))

    assert rows(window.hit_tree) == []


# ---------- 代理状态与日志 ----------

def test_proxy_status_is_displayed(window):
    feed(window, ProxyStatus("172.20.10.1:7890", "TCP 连接正常（12ms）"))

    assert "172.20.10.1:7890" in window.proxy_label.cget("text")
    assert "TCP 连接正常" in window.status_label.cget("text")


def test_proxy_status_without_a_server_shows_a_dash(window):
    feed(window, ProxyStatus(None, "未使用代理"))

    assert "-" in window.proxy_label.cget("text")


def test_log_lines_are_appended(window):
    feed(window, LogLine("12:30:01 发现代理"), LogLine("12:30:02 设置成功"))

    content = window.log_text.get("1.0", "end")
    assert "发现代理" in content
    assert "设置成功" in content


def test_log_is_capped(window):
    feed(window, *[LogLine(f"第 {i} 行") for i in range(AppWindow.LOG_MAX_LINES + 50)])

    line_count = int(window.log_text.index("end-1c").split(".")[0])
    assert line_count <= AppWindow.LOG_MAX_LINES + 1


def test_log_widget_is_read_only(window):
    assert str(window.log_text.cget("state")) == "disabled"


# ---------- 按钮 ----------

def test_scan_button_posts_a_scan_request(window):
    window.scan_button.invoke()

    assert isinstance(window.controller.posted[-1], ScanRequested)


def test_scan_button_targets_the_selected_adapter(window):
    feed(window, AdaptersUpdated((make_adapter(), WIFI), 32))
    window.adapter_tree.selection_set(window.adapter_tree.get_children()[1])

    window.scan_button.invoke()

    assert window.controller.posted[-1] == ScanRequested(adapter_index=23)


def test_refresh_button_posts_a_refresh(window):
    window.refresh_button.invoke()

    assert isinstance(window.controller.posted[-1], RefreshAdaptersRequested)


def test_switch_button_needs_a_selection(window):
    window.switch_button.invoke()

    assert not any(isinstance(m, SwitchRequested) for m in window.controller.posted)


def test_switch_button_posts_the_selected_index(window):
    feed(window, AdaptersUpdated((make_adapter(), WIFI), 32))
    window.adapter_tree.selection_set(window.adapter_tree.get_children()[1])

    window.switch_button.invoke()

    assert window.controller.posted[-1] == SwitchRequested(23)


def test_use_button_posts_the_selected_hit(window):
    feed(window, ScanResults((HIT_7890, HIT_1080)))
    window.hit_tree.selection_set(window.hit_tree.get_children()[1])

    window.use_button.invoke()

    assert window.controller.posted[-1] == UseHitRequested(HIT_1080)


def test_use_button_needs_a_selection(window):
    feed(window, ScanResults((HIT_7890,)))

    window.use_button.invoke()

    assert not any(isinstance(m, UseHitRequested) for m in window.controller.posted)


def test_disable_button_posts_a_disable_request(window):
    window.disable_button.invoke()

    assert isinstance(window.controller.posted[-1], DisableProxyRequested)


def test_buttons_are_greyed_out_while_busy(window):
    feed(window, StateChanged(State.SCANNING))

    assert str(window.scan_button.cget("state")) == "disabled"
    assert str(window.switch_button.cget("state")) == "disabled"


def test_buttons_come_back_when_idle(window):
    feed(window, StateChanged(State.SCANNING))
    feed(window, StateChanged(State.PROXY_ACTIVE))

    assert str(window.scan_button.cget("state")) == "normal"


def test_closing_the_window_posts_shutdown(window):
    window.on_close()

    assert isinstance(window.controller.posted[-1], Shutdown)


# ---------- 线程约束 ----------

def test_gui_module_is_the_only_place_that_imports_tkinter():
    import pathlib

    offenders = []
    for path in pathlib.Path("lan_proxy_switcher").glob("*.py"):
        if path.name == "gui.py":
            continue
        if "tkinter" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)

    assert offenders == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui.py -v`
Expected: FAIL，`ModuleNotFoundError: lan_proxy_switcher.gui`

- [ ] **Step 3: 写实现**

`lan_proxy_switcher/gui.py`：

```python
"""Tkinter 界面。

GUI 控件只在 Tkinter 主线程里被碰：工作线程一律通过 ui_queue 投事件，
由 pump_once 在主线程消费。
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import scrolledtext, ttk

from .controller import (
    AdaptersUpdated,
    DisableProxyRequested,
    LogLine,
    ProxyStatus,
    RefreshAdaptersRequested,
    ScanHitFound,
    ScanRequested,
    ScanResults,
    Shutdown,
    State,
    StateChanged,
    SwitchRequested,
    UseHitRequested,
)
from .scanner import ScanHit

BUSY_STATES = (State.SCANNING, State.SWITCHING)


class AppWindow:
    LOG_MAX_LINES = 2000

    def __init__(self, root: tk.Misc, controller, ui_queue: "queue.Queue[object]", poll_ms: int = 100):
        self.root = root
        self.controller = controller
        self.ui = ui_queue
        self.poll_ms = poll_ms
        self._hits: list[ScanHit] = []

        root.title("LANProxySwitcher")
        root.geometry("900x700")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)  # 多余高度给日志
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_adapters(root)
        self._build_proxy(root)
        self._build_hits(root)
        self._build_log(root)

        self._handlers = {
            AdaptersUpdated: self._render_adapters,
            ScanHitFound: self._append_hit,
            ScanResults: self._render_hits,
            ProxyStatus: self._render_proxy,
            LogLine: self._append_log,
            StateChanged: self._render_state,
        }

    # ---------- 构建 ----------

    def _build_adapters(self, root: tk.Misc) -> None:
        frame = ttk.LabelFrame(root, text="网络适配器")
        frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        frame.columnconfigure(0, weight=1)

        columns = ("kind", "name", "desc", "status", "ipv4")
        headings = ("类型", "名称", "InterfaceDescription", "状态", "IPv4")
        widths = (60, 140, 300, 80, 140)
        self.adapter_tree = ttk.Treeview(
            frame, columns=columns, show="headings", height=4, selectmode="browse"
        )
        for column, heading, width in zip(columns, headings, widths):
            self.adapter_tree.heading(column, text=heading)
            self.adapter_tree.column(column, width=width, anchor="w")
        self.adapter_tree.grid(row=0, column=0, sticky="ew", padx=6, pady=6)

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, sticky="e", padx=6, pady=(0, 6))
        self.switch_button = ttk.Button(
            buttons, text="启用选中网卡（独占）", command=self._on_switch_clicked
        )
        self.switch_button.pack(side="left", padx=4)
        self.refresh_button = ttk.Button(buttons, text="刷新", command=self._on_refresh_clicked)
        self.refresh_button.pack(side="left", padx=4)

    def _build_proxy(self, root: tk.Misc) -> None:
        frame = ttk.LabelFrame(root, text="HTTP Proxy")
        frame.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        frame.columnconfigure(0, weight=1)

        self.proxy_label = ttk.Label(frame, text="当前代理：-")
        self.proxy_label.grid(row=0, column=0, sticky="w", padx=6, pady=(6, 0))
        self.status_label = ttk.Label(frame, text="状态：未使用代理")
        self.status_label.grid(row=1, column=0, sticky="w", padx=6)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="e", padx=6, pady=6)
        self.scan_button = ttk.Button(buttons, text="扫描代理", command=self._on_scan_clicked)
        self.scan_button.pack(side="left", padx=4)
        self.use_button = ttk.Button(buttons, text="使用选中结果", command=self._on_use_clicked)
        self.use_button.pack(side="left", padx=4)
        self.disable_button = ttk.Button(buttons, text="关闭代理", command=self._on_disable_clicked)
        self.disable_button.pack(side="left", padx=4)

    def _build_hits(self, root: tk.Misc) -> None:
        frame = ttk.LabelFrame(root, text="扫描结果")
        frame.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        frame.columnconfigure(0, weight=1)

        columns = ("ip", "port", "connect")
        headings = ("IP", "Port", "Connect")
        widths = (200, 80, 100)
        self.hit_tree = ttk.Treeview(
            frame, columns=columns, show="headings", height=5, selectmode="browse"
        )
        for column, heading, width in zip(columns, headings, widths):
            self.hit_tree.heading(column, text=heading)
            self.hit_tree.column(column, width=width, anchor="w")
        self.hit_tree.grid(row=0, column=0, sticky="ew", padx=6, pady=6)

        ttk.Label(
            frame,
            text="注：Connect 仅为 TCP 建连耗时，不代表代理实际访问速度",
            foreground="#666666",
        ).grid(row=1, column=0, sticky="w", padx=6, pady=(0, 6))

    def _build_log(self, root: tk.Misc) -> None:
        frame = ttk.LabelFrame(root, text="日志")
        frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=(4, 8))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(frame, height=12, state="disabled", wrap="none")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

    # ---------- 事件泵 ----------

    def start_pump(self) -> None:
        self.pump_once()
        self.root.after(self.poll_ms, self.start_pump)

    def pump_once(self) -> None:
        while True:
            try:
                event = self.ui.get_nowait()
            except queue.Empty:
                return
            handler = self._handlers.get(type(event))
            if handler is not None:
                handler(event)

    # ---------- 渲染 ----------

    def _render_adapters(self, event: AdaptersUpdated) -> None:
        self.adapter_tree.delete(*self.adapter_tree.get_children())
        for adapter in event.adapters:
            self.adapter_tree.insert(
                "",
                "end",
                iid=str(adapter.index),
                values=(
                    adapter.kind.value,
                    adapter.name,
                    adapter.description,
                    adapter.status.value,
                    adapter.ipv4 or "-",
                ),
            )

    def _append_hit(self, event: ScanHitFound) -> None:
        self._insert_hit(event.hit)

    def _render_hits(self, event: ScanResults) -> None:
        self.hit_tree.delete(*self.hit_tree.get_children())
        self._hits = []
        for hit in event.hits:
            self._insert_hit(hit)

    def _insert_hit(self, hit: ScanHit) -> None:
        self._hits.append(hit)
        self.hit_tree.insert(
            "",
            "end",
            iid=str(len(self._hits) - 1),
            values=(hit.ip, str(hit.port), f"{hit.latency_ms:.0f}ms"),
        )

    def _render_proxy(self, event: ProxyStatus) -> None:
        self.proxy_label.configure(text=f"当前代理：{event.server or '-'}")
        self.status_label.configure(text=f"状态：{event.status}")

    def _append_log(self, event: LogLine) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", event.text + "\n")
        total = int(self.log_text.index("end-1c").split(".")[0])
        if total > self.LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{total - self.LOG_MAX_LINES + 1}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _render_state(self, event: StateChanged) -> None:
        state = "disabled" if event.state in BUSY_STATES else "normal"
        for button in (self.scan_button, self.switch_button, self.use_button, self.disable_button):
            button.configure(state=state)

    # ---------- 交互 ----------

    def _selected_adapter_index(self) -> int | None:
        selection = self.adapter_tree.selection()
        return int(selection[0]) if selection else None

    def _selected_hit(self) -> ScanHit | None:
        selection = self.hit_tree.selection()
        if not selection:
            return None
        return self._hits[int(selection[0])]

    def _on_scan_clicked(self) -> None:
        self.controller.post(ScanRequested(adapter_index=self._selected_adapter_index()))

    def _on_refresh_clicked(self) -> None:
        self.controller.post(RefreshAdaptersRequested())

    def _on_switch_clicked(self) -> None:
        index = self._selected_adapter_index()
        if index is None:
            self._append_log(LogLine("请先在列表里选中一张网卡"))
            return
        self.controller.post(SwitchRequested(index))

    def _on_use_clicked(self) -> None:
        hit = self._selected_hit()
        if hit is None:
            self._append_log(LogLine("请先在扫描结果里选中一行"))
            return
        self.controller.post(UseHitRequested(hit))

    def _on_disable_clicked(self) -> None:
        self.controller.post(DisableProxyRequested())

    def on_close(self) -> None:
        self.controller.post(Shutdown())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add lan_proxy_switcher/gui.py tests/test_gui.py
git commit -m "feat(gui): Tkinter 界面与主线程事件泵"
```

---

### Task 12: main.py 装配、干净退出与正式打包

**Files:**
- Modify: `main.py`（替换 Task 1 的骨架）
- Modify: `lan_proxy_switcher/controller.py`（新增 `ShutdownComplete` UI 事件）
- Modify: `lan_proxy_switcher/gui.py`（收到 `ShutdownComplete` 后销毁窗口）
- Modify: `lan_proxy_switcher/config.py`（目录不可写时不崩）
- Test: `tests/test_shutdown.py`
- Modify: `tests/test_config.py`（追加一个用例）
- Modify: `MANUAL-VERIFICATION.md`（补完第 3~6 节）

**Interfaces:**
- Consumes: 前 11 个任务的全部产物
- Produces:
  - `ShutdownComplete` UI 事件（无字段）
  - `main.main() -> None` 完整实现
  - `dist/LANProxySwitcher.exe`

> 退出为什么要多一个事件：`on_close` 只投 `Shutdown` 的话，窗口不会关；直接 `root.destroy()` 又会在控制器还原代理之前就把进程拆掉。让控制器处理完 `Shutdown` 后回投一个 `ShutdownComplete`，GUI 收到再 `destroy()`，顺序就确定了，不需要 sleep 去赌。

- [ ] **Step 1: 写失败的测试**

`tests/test_shutdown.py`：

```python
import queue
from dataclasses import replace

import pytest

from conftest import FakeMonitor, FakeNetwork, FakeProxy, drain, make_adapter
from lan_proxy_switcher.controller import Controller, Shutdown, ShutdownComplete, Start
from lan_proxy_switcher.proxy import ProxyState

tk = pytest.importorskip("tkinter")

from lan_proxy_switcher.gui import AppWindow  # noqa: E402


def build(cfg, ui, proxy=None):
    return Controller(
        replace(cfg, auto_scan=False),
        FakeNetwork([make_adapter()]),
        proxy or FakeProxy(),
        FakeMonitor(),
        ui,
        spawn=lambda fn: fn(),
        identity=lambda: "DESKTOP\\llooo S-1-5-21-1",
        discover=lambda *args, **kwargs: None,
        scan=lambda *args, **kwargs: None,
    )


def test_shutdown_emits_a_completion_event(cfg, ui):
    controller = build(cfg, ui)
    controller.post(Start())
    controller.post(Shutdown())

    controller.process_pending()

    assert any(isinstance(event, ShutdownComplete) for event in drain(ui))


def test_completion_is_emitted_after_the_proxy_is_restored(cfg, ui):
    before = ProxyState(True, "1.2.3.4:8080", None, None)
    proxy = FakeProxy(before)
    controller = build(replace(cfg, restore_proxy_on_exit=True), ui, proxy=proxy)
    controller.post(Start())
    controller.post(Shutdown())

    controller.process_pending()

    events = drain(ui)
    assert proxy.restored == [before]
    assert isinstance(events[-1], ShutdownComplete)


def test_run_returns_after_shutdown(cfg, ui):
    controller = build(cfg, ui)
    controller.post(Start())
    controller.post(Shutdown())

    controller.run()  # 不会挂住


def test_window_is_destroyed_on_shutdown_complete():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("当前环境没有可用的窗口站")
    root.withdraw()
    ui = queue.Queue()

    class Recorder:
        def post(self, message):
            pass

    window = AppWindow(root, Recorder(), ui)
    ui.put(ShutdownComplete())
    window.pump_once()

    assert not root.winfo_exists()
```

`tests/test_config.py` 末尾追加：

```python
def test_unwritable_directory_still_yields_defaults(tmp_path, monkeypatch):
    """打包后的 exe 可能放在只读目录里，写不了配置也不能崩。"""
    path = tmp_path / "config.json"

    def refuse(*args, **kwargs):
        raise PermissionError("拒绝访问")

    monkeypatch.setattr(config.Path, "write_text", refuse)
    lines = []

    cfg = config.load(path, lines.append)

    assert cfg == config.Config()
    assert any("写入" in line for line in lines)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shutdown.py -v`
Expected: FAIL，`ImportError: cannot import name 'ShutdownComplete'`

- [ ] **Step 3: 补三处实现**

`lan_proxy_switcher/controller.py` 的 UI 事件区追加：

```python
@dataclass(frozen=True)
class ShutdownComplete:
    pass
```

`_on_shutdown` 末尾追加一行（必须在 `restore` 之后）：

```python
        self._ui.put(ShutdownComplete())
```

`lan_proxy_switcher/gui.py` 的 import 里加上 `ShutdownComplete`，`self._handlers` 加一条 `ShutdownComplete: self._on_shutdown_complete`，并新增方法：

```python
    def _on_shutdown_complete(self, _event: ShutdownComplete) -> None:
        self.root.destroy()
```

`lan_proxy_switcher/config.py` 的 `load` 里，把写默认配置那段改成：

```python
    if not path.exists():
        cfg = Config()
        try:
            save(path, cfg)
            log(f"配置文件不存在，已写入默认配置：{path}")
        except OSError as exc:
            log(f"配置文件写入失败（{exc}），本次使用默认值：{path}")
        return cfg
```

- [ ] **Step 4: 写 main.py**

替换 `main.py` 全文：

```python
"""LANProxySwitcher 入口：装配各服务、起线程、跑 Tkinter 主循环。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk

from lan_proxy_switcher import config as config_module
from lan_proxy_switcher import scanner
from lan_proxy_switcher.controller import Controller, LogLine, Start
from lan_proxy_switcher.gui import AppWindow
from lan_proxy_switcher.monitor import MonitorState, MonitorThread
from lan_proxy_switcher.network import PowerShellNetworkService
from lan_proxy_switcher.proxy import RegistryProxyService


def main() -> None:
    ui: "queue.Queue[object]" = queue.Queue()

    startup_lines: list[str] = []
    path = config_module.config_path()
    cfg = config_module.load(path, startup_lines.append)

    network = PowerShellNetworkService()
    proxy = RegistryProxyService()

    monitor_state = MonitorState(
        list_adapters=network.list_adapters,
        probe=scanner.probe,
        monitor_interval_s=cfg.monitor_interval_s,
        scan_timeout_ms=cfg.scan_timeout_ms,
        proxy_check_failures=cfg.proxy_check_failures,
    )

    controller = Controller(
        cfg,
        network,
        proxy,
        monitor_state,
        ui,
        spawn=lambda job: threading.Thread(target=job, daemon=True).start(),
    )
    monitor = MonitorThread(monitor_state, controller.inbox)

    root = tk.Tk()
    window = AppWindow(root, controller, ui)

    ui.put(LogLine(f"配置文件：{path}"))
    for line in startup_lines:
        ui.put(LogLine(line))

    controller_thread = threading.Thread(target=controller.run, name="controller", daemon=True)
    controller_thread.start()
    monitor.start()

    controller.post(Start())
    window.start_pump()
    root.mainloop()

    # 窗口已销毁（Shutdown 处理完毕），收尾后台线程
    monitor.stop()
    controller_thread.join(2.0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑全部测试**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: 全部 PASS

- [ ] **Step 6: 源码方式真机跑一次**

```bash
.venv/Scripts/python.exe main.py
```

窗口应出现，日志区依次出现：配置文件路径 → 运行账户（含 SID）→ 启动前系统代理 → 当前网卡 → IP/网段 → 优先检测 Gateway → 扫描结论。网卡列表应列出 3 张物理网卡且**不含** Hyper-V / VMware / Tailscale 等虚拟网卡。关窗口后进程应在 2 秒内退出（`Get-Process python` 查不到残留）。

此时程序是普通权限，**网卡切换会失败**（日志里能看到「拒绝访问」之类的报错），这是预期的——完整验证要用 Step 7 的 exe。

- [ ] **Step 7: 正式打包**

```bash
rm -rf build dist LANProxySwitcher.spec
.venv/Scripts/python.exe -m PyInstaller --onefile --windowed --uac-admin --name LANProxySwitcher main.py
ls -l dist/LANProxySwitcher.exe
```

- [ ] **Step 8: 补完手动验证清单**

在 `MANUAL-VERIFICATION.md` 末尾追加：

```markdown
## 3. 场景 A：iPhone 共享网络

- [ ] Windows 连上 iPhone 热点（或 USB 网络共享），iPhone 上 Shadowrocket 已开
- [ ] 启动程序，日志显示网关 172.20.10.1 被优先检测并命中
- [ ] 全程未触发全网段扫描（日志里没有「扫描 N 个目标」这行）
- [ ] 系统代理被设为 172.20.10.1:7890
- [ ] 浏览器可以正常走代理上网

## 4. 场景 B：A/B 网线直连

- [ ] 两台电脑网线直连，B 提供 7890 代理且**没有**默认网关
- [ ] A 上启动程序，日志显示走了全网段扫描并发现 B 的 IP:7890
- [ ] 系统代理被设为 B 的地址
- [ ] 扫描结果表格里 Connect 时间合理（个位数到几十毫秒）

## 5. 网卡切换（需 exe 的管理员权限）

- [ ] 选中 Wi-Fi 点「启用选中网卡（独占）」：其余物理网卡被禁用，Wi-Fi 启用并拿到 IP
- [ ] 切换期间 GUI 不卡死，按钮置灰，日志逐秒显示等待进度
- [ ] 切换完成后自动开始扫描
- [ ] 反向切回有线，行为一致
- [ ] 目标网卡拿不到 IP 时，30 秒后超时退出并说明卡在哪一步，不留中间态

## 6. 监控与退出

- [ ] 代理生效后，状态行每 30 秒刷新一次「TCP 连接正常（Nms）」
- [ ] 关掉 iPhone 上的 Shadowrocket，约 90 秒后日志出现「判定失效，重新扫描」
- [ ] 拔掉网线 / 关掉 Wi-Fi，约 10 秒后日志出现「检测到网络变化」并重扫
- [ ] 扫描 /24 全程 GUI 可正常拖动、按钮可点，不冻结
- [ ] `restoreProxyOnExit=false`（默认）时退出，系统代理保持程序设置的值
- [ ] 改成 `true` 后退出，系统代理还原成启动前的状态
- [ ] 任务管理器里没有残留的 LANProxySwitcher 进程
```

- [ ] **Step 9: 用 exe 跑完整手动验证**

按 `MANUAL-VERIFICATION.md` 第 1~6 节逐条执行 `dist/LANProxySwitcher.exe`。**有任何一条不过就停下来报告**，不要勾掉。

- [ ] **Step 10: Commit**

```bash
git add main.py lan_proxy_switcher/controller.py lan_proxy_switcher/gui.py lan_proxy_switcher/config.py tests/test_shutdown.py tests/test_config.py MANUAL-VERIFICATION.md
git commit -m "feat: 装配入口、确定性退出序列与完整手动验证清单"
```

---

## 附录：任务与规格章节对照

| 规格章节 | 覆盖任务 |
|---|---|
| 1 目标与范围 | 全部 |
| 2 代理检测规则（只做 TCP Connect） | Task 4（`probe`）、Task 8（监控探测）、Global Constraints |
| 3 对原需求的 4 条修订 | Task 1（`--uac-admin`）、Task 3（/24 收窄）、Task 11（网卡列表）、Task 9（controller 模块） |
| 4 开发机实测环境 | Task 5（fixture 与三条解析规则） |
| 5 技术选型（PowerShell + JSON） | Task 5、Task 6 |
| 6 模块划分与接口 | Task 2~11 逐一对应 |
| 7.1 当前网卡选择 | Task 5（`select_active`） |
| 7.2 目标枚举与安全闸门 | Task 3 |
| 7.3 两阶段扫描 | Task 4（`discover`） |
| 7.4 代理选择 | Task 3（`select_best`）、Task 9（应用） |
| 8 Windows 系统代理 | Task 7 |
| 9.1 监控线程 | Task 8 |
| 9.2 控制器状态机 | Task 9、Task 10 |
| 9.3 SWITCHING 序列 | Task 10 |
| 10 线程模型 | Task 9（无锁 inbox）、Task 11（事件泵）、Task 12（装配与退出） |
| 11 GUI | Task 11 |
| 12 配置 | Task 2、Task 12（不可写目录） |
| 13 错误处理 | Task 4（unreachable）、Task 5（NetworkError）、Task 6（启停失败）、Task 7（写后校验）、Task 9（`_dispatch` 兜底） |
| 14 测试策略 | 每个任务的测试步骤 + Task 6/7/12 的 MANUAL-VERIFICATION.md |
| 15 打包 | Task 1（风险前置）、Task 12（正式打包） |
