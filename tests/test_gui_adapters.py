"""适配器表格的 IP 展示，以及静态 / 动态 IP 的两个入口。"""

import queue

import pytest

tk = pytest.importorskip("tkinter")

from conftest import WIFI, make_adapter
from lan_proxy_switcher.controller import (
    SetDhcpRequested,
    SetStaticIpRequested,
    StaticIpProfilesUpdated,
)
from lan_proxy_switcher.gui import Application, parse_static_ip_form, prefill
from lan_proxy_switcher.network import StaticIpProfile

ETHERNET = make_adapter(dhcp=True)
STATIC_ETHERNET = make_adapter(dhcp=False)
PROFILE = StaticIpProfile("192.168.1.50", 24, "192.168.1.1", ("8.8.8.8", "1.1.1.1"))


class FakeController:
    def __init__(self):
        self.posted = []

    def post(self, event):
        self.posted.append(event)


@pytest.fixture
def app(tk_root):
    for child in tk_root.winfo_children():
        child.destroy()
    application = Application(tk_root, FakeController(), queue.Queue(), lambda: None)
    yield application
    application._closing = True  # 停掉 after 轮询，但把 root 留给下一个用例


def row(application, index):
    return application._adapters_view.item(str(index), "values")


def select(application, index):
    application._adapters_view.selection_set(str(index))
    application._root.update()


# ---------- 表格展示 ----------

def test_row_shows_the_gateway(app):
    app._replace_adapters((ETHERNET,), ETHERNET.index)

    assert ETHERNET.gateway in row(app, ETHERNET.index)


def test_row_shows_dhcp_as_the_addressing_mode(app):
    app._replace_adapters((ETHERNET,), ETHERNET.index)

    assert "DHCP" in row(app, ETHERNET.index)[-1]


def test_row_shows_static_as_the_addressing_mode(app):
    app._replace_adapters((STATIC_ETHERNET,), None)

    assert row(app, STATIC_ETHERNET.index)[-1] == "静态"


def test_row_shows_a_dash_when_the_mode_is_unknown(app):
    """已禁用的网卡查不到寻址方式。"""
    app._replace_adapters((WIFI,), None)

    assert row(app, WIFI.index)[-1] == "-"


def test_row_shows_a_dash_when_there_is_no_gateway(app):
    app._replace_adapters((WIFI,), None)

    assert "-" in row(app, WIFI.index)


# ---------- 两个入口 ----------

def test_ip_buttons_are_disabled_without_a_selection(app):
    app._replace_adapters((ETHERNET, WIFI), ETHERNET.index)

    assert app._static_button.instate(("disabled",))
    assert app._dhcp_button.instate(("disabled",))


def test_ip_buttons_enable_once_a_row_is_selected(app):
    app._replace_adapters((ETHERNET, WIFI), ETHERNET.index)

    select(app, ETHERNET.index)

    assert app._static_button.instate(("!disabled",))
    assert app._dhcp_button.instate(("!disabled",))


def test_set_dhcp_targets_the_selected_adapter(app):
    app._replace_adapters((ETHERNET, WIFI), ETHERNET.index)
    select(app, WIFI.index)

    app._set_dhcp()

    assert app._controller.posted == [SetDhcpRequested(WIFI.index)]


def test_set_dhcp_does_nothing_without_a_selection(app):
    app._replace_adapters((ETHERNET,), ETHERNET.index)

    app._set_dhcp()

    assert app._controller.posted == []


def test_submitting_the_static_form_posts_the_profile(app):
    app._replace_adapters((ETHERNET,), ETHERNET.index)
    select(app, ETHERNET.index)

    app._apply_static_ip(ETHERNET.index, "192.168.1.50", "24", "192.168.1.1", "8.8.8.8, 1.1.1.1")

    assert app._controller.posted == [SetStaticIpRequested(ETHERNET.index, PROFILE)]


def test_an_illegal_static_form_is_logged_instead_of_posted(app):
    app._apply_static_ip(ETHERNET.index, "192.168.1.999", "24", "", "")

    assert app._controller.posted == []
    assert "192.168.1.999" in app._log.get("1.0", "end-1c")


# ---------- 表单解析 ----------

def test_form_parses_every_field():
    profile = parse_static_ip_form("192.168.1.50", "24", "192.168.1.1", "8.8.8.8, 1.1.1.1")

    assert profile == PROFILE


def test_form_treats_a_blank_gateway_as_none():
    assert parse_static_ip_form("192.168.1.50", "24", "   ", "").gateway is None


def test_form_splits_dns_on_commas_and_spaces():
    profile = parse_static_ip_form("192.168.1.50", "24", "", "8.8.8.8 1.1.1.1,9.9.9.9")

    assert profile.dns == ("8.8.8.8", "1.1.1.1", "9.9.9.9")


def test_form_rejects_a_non_numeric_prefix():
    with pytest.raises(ValueError):
        parse_static_ip_form("192.168.1.50", "二十四", "", "")


def test_form_rejects_a_malformed_address():
    with pytest.raises(ValueError):
        parse_static_ip_form("192.168.1.999", "24", "", "")


# ---------- 弹窗预填 ----------

def test_prefill_prefers_the_saved_profile():
    assert prefill(ETHERNET, PROFILE) == ("192.168.1.50", "24", "192.168.1.1", "8.8.8.8, 1.1.1.1")


def test_prefill_falls_back_to_the_current_address():
    assert prefill(ETHERNET, None) == (
        ETHERNET.ipv4,
        str(ETHERNET.prefix_length),
        ETHERNET.gateway,
        "",
    )


def test_prefill_of_an_adapter_without_an_address_is_blank():
    assert prefill(WIFI, None) == ("", "", "", "")


# ---------- 档位事件 ----------

def test_published_profiles_are_remembered_for_the_dialog(app):
    app._handle(StaticIpProfilesUpdated({ETHERNET.name: PROFILE}))

    assert app._profiles == {ETHERNET.name: PROFILE}


def test_the_selection_survives_an_adapter_refresh(app):
    """扫描每轮都会刷新表格，不能把用户选中的行弄丢。"""
    app._replace_adapters((ETHERNET, WIFI), ETHERNET.index)
    select(app, WIFI.index)

    app._replace_adapters((ETHERNET, WIFI), ETHERNET.index)

    assert app._adapters_view.selection() == (str(WIFI.index),)


def test_the_dialog_prefills_from_the_saved_profile(app):
    app._replace_adapters((ETHERNET,), ETHERNET.index)
    select(app, ETHERNET.index)
    app._handle(StaticIpProfilesUpdated({ETHERNET.name: PROFILE}))

    dialog = app._open_static_ip_dialog()

    try:
        assert tuple(var.get() for var in dialog.fields) == prefill(ETHERNET, PROFILE)
    finally:
        dialog.destroy()
