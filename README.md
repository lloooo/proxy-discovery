# LANProxySwitcher

Windows 局域网代理自动发现与网卡切换工具。它只对私有网段执行 TCP Connect
探测，发现 `7890` / `1082` 等可连通端口后，可设置当前用户的 Windows 系统代理。

## 运行

程序以管理员权限运行，用于启用或禁用网卡：

```powershell
python main.py
```

首次启动会在程序目录生成 `config.json`。主要配置包括扫描端口、TCP 超时、并发数和
代理失效重扫策略。

## IP 设置

适配器表格显示每张网卡的 IPv4、网关和寻址方式（自动 (DHCP) / 静态）。选中一行后：

- **设为静态 IP** 弹窗填写 IP、前缀长度、网关和 DNS，应用后按网卡名记进
  `config.json` 的 `staticIpProfiles`，下次弹窗自动预填。
- **设为动态 IP** 把该网卡改回 DHCP，并把 DNS 重置为自动获取。

两者都会在地址生效后重新扫描代理——换了网段，原先的扫描结果就作废了。

## 打包

```powershell
pyinstaller --noconfirm LANProxySwitcher.spec
```

生成的 `dist/LANProxySwitcher.exe` 带有 `requireAdministrator` UAC 清单；启动时必须确认
Windows 的管理员权限提示。

## 验证

核心逻辑测试：

```powershell
python -m pytest
```

真实网卡和系统代理的验收步骤见 `MANUAL-VERIFICATION.md`。
