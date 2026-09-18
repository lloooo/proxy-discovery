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
