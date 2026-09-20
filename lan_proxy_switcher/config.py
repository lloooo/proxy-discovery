"""JSON 配置的读取、校验与写回。"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from .network import StaticIpProfile


@dataclass(frozen=True)
class Config:
    ports: tuple[int, ...] = (7890, 1082)
    prefer_port: int = 7890
    scan_timeout_ms: int = 500
    scan_concurrency: int = 100
    monitor_interval_s: int = 30
    proxy_check_failures: int = 3
    auto_scan: bool = True
    auto_set_proxy: bool = True
    disable_proxy_when_unavailable: bool = False
    restore_proxy_on_exit: bool = False
    # 网卡名 → 上次填写的静态 IP 档位，供弹窗预填与一键套用
    static_ip_profiles: dict[str, StaticIpProfile] = field(default_factory=dict)


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
    "staticIpProfiles": "static_ip_profiles",
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


def _static_ip_profiles(
    value: object, log: Callable[[str], None]
) -> dict[str, StaticIpProfile]:
    """逐条校验。一张网卡的档位写坏了，不该拖累其余网卡。"""
    if not isinstance(value, dict):
        log(f"配置项 staticIpProfiles 非法：必须是对象，得到 {type(value).__name__}，改用空档位")
        return {}

    profiles: dict[str, StaticIpProfile] = {}
    for name, raw in value.items():
        if not isinstance(raw, dict):
            log(f"静态 IP 档位 {name} 非法：必须是对象，已丢弃")
            continue
        try:
            profiles[name] = StaticIpProfile(
                ip=raw.get("ip"),
                prefix_length=raw.get("prefix"),
                gateway=raw.get("gateway"),
                dns=tuple(raw.get("dns") or ()),
            )
        except (ValueError, TypeError) as exc:
            log(f"静态 IP 档位 {name} 非法：{exc}，已丢弃")
    return profiles


def _profiles_to_json(profiles: dict[str, StaticIpProfile]) -> dict[str, object]:
    return {
        name: {
            "ip": profile.ip,
            "prefix": profile.prefix_length,
            "gateway": profile.gateway,
            "dns": list(profile.dns),
        }
        for name, profile in profiles.items()
    }


# staticIpProfiles 不在这里：它要逐条记日志，需要 load 传进来的 log
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


# ---------- 设置表单 ----------

# 表单字段顺序即弹窗里的控件顺序；标签同时用于出错时的提示文案
PORTS_LABEL = "扫描端口"
FORM_INTS: tuple[tuple[str, str], ...] = (
    ("prefer_port", "首选端口"),
    ("scan_timeout_ms", "TCP 超时(ms)"),
    ("scan_concurrency", "并发数"),
    ("monitor_interval_s", "监控间隔(s)"),
    ("proxy_check_failures", "代理失败阈值"),
)
FORM_FLAGS: tuple[tuple[str, str], ...] = (
    ("auto_scan", "启动时自动扫描"),
    ("auto_set_proxy", "自动设置系统代理"),
    ("disable_proxy_when_unavailable", "无可用代理时关闭系统代理"),
    ("restore_proxy_on_exit", "退出时还原原有代理"),
)

_PORT_SEPARATORS = re.compile(r"[,\s]+")


def parse_ports_field(text: str) -> tuple[int, ...]:
    """把 "7890, 1082" 这样的一行文本转成端口元组。"""
    parts = [part for part in _PORT_SEPARATORS.split(text.strip()) if part]
    if not parts:
        raise ValueError(f"{PORTS_LABEL}不能为空")
    for part in parts:
        if not part.isdigit():
            raise ValueError(f"{PORTS_LABEL}必须是数字，得到 {part!r}")
    try:
        return _ports([int(part) for part in parts])
    except ValueError as exc:
        raise ValueError(f"{PORTS_LABEL}：{exc}") from None


def _form_int(field: str, label: str, raw: object) -> int:
    text = str(raw).strip()
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"{label}必须是整数，得到 {text!r}") from None
    try:
        return VALIDATORS[field](value)  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"{label}{exc}") from None


def ports_field(cfg: Config) -> str:
    return ", ".join(str(port) for port in cfg.ports)


def apply_form(base: Config, values: dict[str, object]) -> Config:
    """把设置弹窗的输入合成新配置。任何一项非法都抛 ValueError，整体不生效。

    staticIpProfiles 不在表单里，原样保留。
    """
    changes: dict[str, object] = {"ports": parse_ports_field(str(values["ports"]))}
    for field_name, label in FORM_INTS:
        changes[field_name] = _form_int(field_name, label, values[field_name])
    for field_name, label in FORM_FLAGS:
        try:
            changes[field_name] = _flag(values[field_name])
        except ValueError as exc:
            raise ValueError(f"{label}{exc}") from None

    prefer = changes["prefer_port"]
    ports = changes["ports"]
    if prefer not in ports:  # type: ignore[operator]
        raise ValueError(f"首选端口 {prefer} 不在{PORTS_LABEL} {list(ports)} 中")  # type: ignore[arg-type]

    return replace(base, **changes)  # type: ignore[arg-type]


def config_path() -> Path:
    """冻结运行时用 exe 同级目录；--onefile 会解压到临时目录，不能用 __file__。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.json"
    return Path(__file__).resolve().parent.parent / "config.json"


def to_json_dict(cfg: Config) -> dict[str, object]:
    out: dict[str, object] = {}
    for json_key, field_name in JSON_KEYS.items():
        value = getattr(cfg, field_name)
        if field_name == "static_ip_profiles":
            out[json_key] = _profiles_to_json(value)
        else:
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
        if json_key not in raw or field_name not in VALIDATORS:
            continue
        try:
            values[field_name] = VALIDATORS[field_name](raw[json_key])
        except ValueError as exc:
            log(f"配置项 {json_key} 非法：{exc}，使用默认值")

    if "staticIpProfiles" in raw:
        values["static_ip_profiles"] = _static_ip_profiles(raw["staticIpProfiles"], log)

    cfg = Config(**values)  # type: ignore[arg-type]
    if cfg.prefer_port not in cfg.ports:
        log(f"preferPort={cfg.prefer_port} 不在 ports={list(cfg.ports)} 中，改用 {cfg.ports[0]}")
        cfg = replace(cfg, prefer_port=cfg.ports[0])
    return cfg
