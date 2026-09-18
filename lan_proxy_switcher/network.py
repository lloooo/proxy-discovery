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


def switch_script(index: int, enabled: bool) -> str:
    """按 InterfaceIndex 找到网卡对象，再启用/禁用。

    Enable-NetAdapter / Disable-NetAdapter 本身没有 -InterfaceIndex 参数；不能把
    Get-NetAdapter 支持的参数直接传给它们。使用 InputObject 既保留索引这一稳定
    身份，也避免把显示名称拼接进 PowerShell 命令。
    """
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError(f"InterfaceIndex 必须是整数，得到 {index!r}")
    verb = "Enable-NetAdapter" if enabled else "Disable-NetAdapter"
    return (
        f"$adapter = Get-NetAdapter -InterfaceIndex {int(index)} -IncludeHidden -ErrorAction Stop; "
        f"{verb} -InputObject $adapter -Confirm:$false -ErrorAction Stop"
    )


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

    def set_adapter_enabled(self, index: int, enabled: bool) -> None:
        run_powershell(switch_script(index, enabled), timeout=60.0)
