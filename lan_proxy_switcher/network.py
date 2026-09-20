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
    "$interfaces = Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
    "Select-Object InterfaceIndex,Dhcp; "
    "[pscustomobject]@{adapters=@($adapters); addresses=@($addresses); routes=@($routes); "
    "interfaces=@($interfaces)} | "
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
    dhcp: bool | None = None  # None：网卡已禁用，Get-NetIPInterface 里查不到


def _ipv4(value: object, label: str) -> str:
    """校验后原样返回。值会拼进 PowerShell 命令串，这里是唯一的防线。"""
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是字符串，得到 {value!r}")
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ipaddress.AddressValueError as exc:
        raise ValueError(f"{label}不是合法的 IPv4 地址：{value!r}") from exc


@dataclass(frozen=True)
class StaticIpProfile:
    """一张网卡的静态 IP 档位。构造即校验，之后可以放心拼进命令串。"""

    ip: str
    prefix_length: int
    gateway: str | None
    dns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ip", _ipv4(self.ip, "IP 地址"))
        prefix = self.prefix_length
        if isinstance(prefix, bool) or not isinstance(prefix, int) or not 1 <= prefix <= 32:
            raise ValueError(f"前缀长度必须是 1~32 之间的整数，得到 {prefix!r}")
        if self.gateway is not None:
            object.__setattr__(self, "gateway", _ipv4(self.gateway, "网关"))
        object.__setattr__(self, "dns", tuple(_ipv4(item, "DNS") for item in self.dns))


class NetworkService(Protocol):
    def list_adapters(self) -> list[Adapter]: ...

    def set_adapter_enabled(self, index: int, enabled: bool) -> None: ...

    def set_static_ip(self, index: int, profile: StaticIpProfile) -> None: ...

    def set_dhcp(self, index: int) -> None: ...


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


def _dhcp(row: dict) -> bool | None:
    """ConvertTo-Json 把 NetIPInterfaceDhcp 序列化成整数（Disabled=0、Enabled=1），
    但不同 PowerShell 版本也可能给出枚举名，两种都接。"""
    raw = row.get("Dhcp")
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return {0: False, 1: True}.get(raw)
    if isinstance(raw, str):
        return {"disabled": False, "enabled": True}.get(raw.strip().lower())
    return None


def parse_snapshot(payload: dict) -> list[Adapter]:
    """把 QUERY_SCRIPT 的 JSON 输出解析成 Adapter 列表，只保留物理网卡。"""
    addresses: dict[int, list[dict]] = {}
    for row in payload.get("addresses") or []:
        addresses.setdefault(int(row["InterfaceIndex"]), []).append(row)

    routes: dict[int, list[dict]] = {}
    for row in payload.get("routes") or []:
        routes.setdefault(int(row["InterfaceIndex"]), []).append(row)

    dhcp: dict[int, bool | None] = {}
    for row in payload.get("interfaces") or []:
        dhcp[int(row["InterfaceIndex"])] = _dhcp(row)

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
                dhcp=dhcp.get(index),
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


def _interface_index(index: object) -> int:
    """索引直接拼进命令串，必须是整数。bool 是 int 的子类，显式挡掉。"""
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError(f"InterfaceIndex 必须是整数，得到 {index!r}")
    return int(index)


def switch_script(index: int, enabled: bool) -> str:
    """按 InterfaceIndex 找到网卡对象，再启用/禁用。

    Enable-NetAdapter / Disable-NetAdapter 本身没有 -InterfaceIndex 参数；不能把
    Get-NetAdapter 支持的参数直接传给它们。使用 InputObject 既保留索引这一稳定
    身份，也避免把显示名称拼接进 PowerShell 命令。
    """
    _interface_index(index)
    verb = "Enable-NetAdapter" if enabled else "Disable-NetAdapter"
    return (
        f"$adapter = Get-NetAdapter -InterfaceIndex {int(index)} -IncludeHidden -ErrorAction Stop; "
        f"{verb} -InputObject $adapter -Confirm:$false -ErrorAction Stop"
    )


def _clear_ip_script(index: int) -> str:
    """改寻址方式前先清干净：本来就没有静态地址时 Remove-* 会报错，故容错。"""
    return (
        f"Get-NetAdapter -InterfaceIndex {index} -ErrorAction Stop | Out-Null; "
        f"Remove-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 "
        "-Confirm:$false -ErrorAction SilentlyContinue; "
        f"Remove-NetRoute -InterfaceIndex {index} -AddressFamily IPv4 "
        "-DestinationPrefix '0.0.0.0/0' -Confirm:$false -ErrorAction SilentlyContinue; "
    )


def _dns_script(index: int, dns: Sequence[str]) -> str:
    if dns:
        servers = ",".join(f"'{server}'" for server in dns)
        return (
            f"Set-DnsClientServerAddress -InterfaceIndex {index} "
            f"-ServerAddresses @({servers}) -ErrorAction Stop"
        )
    # 静态 DNS 不随地址一起消失，必须显式恢复自动获取
    return f"Set-DnsClientServerAddress -InterfaceIndex {index} -ResetServerAddresses -ErrorAction Stop"


def static_ip_script(index: int, profile: StaticIpProfile) -> str:
    """DHCP 仍开着时 New-NetIPAddress 会失败，所以先 -Dhcp Disabled。"""
    idx = _interface_index(index)
    gateway = f" -DefaultGateway '{profile.gateway}'" if profile.gateway else ""
    return (
        _clear_ip_script(idx)
        + f"Set-NetIPInterface -InterfaceIndex {idx} -AddressFamily IPv4 "
        "-Dhcp Disabled -ErrorAction Stop; "
        + f"New-NetIPAddress -InterfaceIndex {idx} -AddressFamily IPv4 "
        f"-IPAddress '{profile.ip}' -PrefixLength {profile.prefix_length}{gateway} "
        "-ErrorAction Stop | Out-Null; "
        + _dns_script(idx, profile.dns)
    )


def dhcp_script(index: int) -> str:
    idx = _interface_index(index)
    return (
        _clear_ip_script(idx)
        + f"Set-NetIPInterface -InterfaceIndex {idx} -AddressFamily IPv4 "
        "-Dhcp Enabled -ErrorAction Stop; "
        + _dns_script(idx, ())
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

    def set_static_ip(self, index: int, profile: StaticIpProfile) -> None:
        run_powershell(static_ip_script(index, profile), timeout=60.0)

    def set_dhcp(self, index: int) -> None:
        run_powershell(dhcp_script(index), timeout=60.0)
