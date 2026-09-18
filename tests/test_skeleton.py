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
