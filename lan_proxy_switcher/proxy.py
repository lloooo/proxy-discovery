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
