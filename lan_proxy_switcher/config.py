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
