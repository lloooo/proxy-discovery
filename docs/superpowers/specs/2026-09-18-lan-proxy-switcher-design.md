# 局域网代理自动发现与网卡切换工具 — 设计规格

- 日期：2026-09-18
- 状态：已确认，待编写实现计划
- 需求来源：`需求.md`
- 目标产物：`LANProxySwitcher.exe`（Windows 11，单文件，目标机器无需安装 Python）

## 1. 目标与范围

Windows 桌面小工具，完成以下闭环：

1. 列出物理网卡，支持「启用选中网卡并禁用其余物理网卡」的独占式切换
2. 取当前网卡的 IPv4 / 掩码 / 默认网关，算出扫描网段
3. 并发 TCP Connect 扫描 `7890` / `1082`，网关优先
4. 选出最优 `IP:Port`，写入当前用户的 Windows 系统代理
5. 定期监控代理与网络状态，代理失效或网络变化后自动重扫

两个主场景：

- **场景 A（iPhone 共享网络）**：Windows 连接 iPhone 热点或 USB 网络共享，iPhone 上的 Shadowrocket 在 `7890` / `1082` 提供 HTTP 代理，网关即代理（如 `172.20.10.1:7890`）
- **场景 B（A/B 直连）**：两台电脑网线直连，B 提供 `192.168.10.20:7890`，B 没有默认网关，只能靠全网段扫描发现

### 1.1 非目标

- 不做 TUN，不安装任何网络驱动
- 不验证 HTTP 代理协议，不验证代理能否访问互联网
- 不扫描公网，不接受用户输入的任意网段
- 不做托盘常驻（关闭窗口即退出进程）

## 2. 代理检测规则（硬约束）

判定唯一依据：**`IP:Port` 能否建立 TCP 连接**。

TCP connect 成功 → 认为代理可用。超时 / connection refused / network unreachable / 其他 TCP 错误 → 认为不可用。

**禁止**：发送 HTTP GET、发送 CONNECT、请求任何 URL、验证 HTTP 状态码、验证能否访问互联网、验证代理节点、验证 DNS、验证 HTTPS、验证公网 IP。

监控阶段同样只做 TCP Connect。

## 3. 对原始需求的修订

以下 4 条与 `需求.md` 不同，均已确认：

| # | 原需求 | 本设计 | 原因 |
|---|---|---|---|
| 1 | 第十一节：程序尽量普通用户运行，仅启停网卡请求 UAC | **整个程序以管理员运行**（PyInstaller `--uac-admin`） | 用户决定，换取切换网卡时不重复弹 UAC |
| 2 | 第四节：按网卡掩码算 CIDR 扫描 | 掩码 `>= /24` 用真实掩码；`< /24` 收窄到本机 IP 所在 /24 | 避免 /16 产生 13 万次连接 |
| 3 | 第九节：有线/无线两个固定切换按钮 | 物理网卡列表 + 「启用选中网卡（独占）」 | 实测开发机有 3 张物理网卡，二元切换语义不成立 |
| 4 | 第十八节：7 个模块 | 新增 `controller.py` | 自动模式状态机在原模块划分里无处安放，否则会绑死进 `gui.py` 而无法测试 |

修订 1 的两个副作用需知悉：

- 必须以**当前账户**提权运行。若以「其他用户身份运行」，代理会写进那个管理员账户的 `HKCU`，对当前用户无效。程序启动时把写入目标用户名与 SID 打进日志首行，便于一眼识别。
- `requireAdministrator` 的程序放进「启动」文件夹会每次弹 UAC；如需开机自启，须走计划任务（本期不实现）。

## 4. 开发机实测环境

以下为 2026-09-18 在开发机（Windows 11 Pro 26100，Python 3.14.3，Tk 8.6，Windows PowerShell 5.1）实测结果，作为解析层的测试 fixture 依据。

`Get-NetAdapter` 返回 12 张网卡，`-Physical` 过滤后剩 3 张：

| 名称 | 描述 | 状态 | PhysicalMediaType | IPv4 | 网关 |
|---|---|---|---|---|---|
| Ethernet 10 | Apple Mobile Device Ethernet #8 | Up | Unspecified | 172.20.10.7/28 | 172.20.10.1 |
| Ethernet | Realtek PCIe GbE Family Controller | Up | 802.3 | 10.0.0.2/24 | 无 |
| Wi-Fi | RZ616 Wi-Fi 6E 160MHz | Disconnected | Native 802.11 | 169.254.25.234（APIPA） | 无 |

被过滤掉的 9 张：Hyper-V vEthernet ×2、VMware VMnet1/VMnet8、TAP-Windows (cfw-tap)、WireGuard、Wintun、Tailscale、Bluetooth PAN。

由此确立三条解析规则：

1. 只列 `HardwareInterface == true && Virtual == false` 的网卡（即 `Get-NetAdapter -Physical`）
2. `169.254.0.0/16`（APIPA）视为「无可用 IPv4」，`ipv4` 置 `None`
3. iPhone 共享网络的掩码是 **/28 而非 /24**，必须按真实掩码计算

## 5. 技术选型

**PowerShell 子进程 + JSON**（`Get-NetAdapter -Physical`、`Get-NetIPAddress`、`Get-NetRoute`、`Enable-NetAdapter`、`Disable-NetAdapter`），零第三方依赖。

选它而非 ctypes 直调 Win32 的决定性理由：**被禁用的网卡不会出现在 `GetAdaptersAddresses` 的结果里**，而 GUI 必须显示「已禁用」状态；`Get-NetAdapter` 是唯一能可靠列出已禁用网卡的单一数据源。此外启停网卡在 Win32 层需走 SetupDi 设备接口，繁琐易错。子进程约 300–500ms 的开销，对 5 秒一次的后台轮询无感知。

同理不采用 `pywin32` / `wmi`：引入依赖与 COM 打包风险，能力无增量。

调用约定：

- 统一 `-NoProfile -NonInteractive`
- `subprocess` 加 `CREATE_NO_WINDOW`，防控制台黑框闪烁
- 命令前缀 `[Console]::OutputEncoding = [Text.UTF8Encoding]::new()`，否则中文网卡名在中文 Windows 上按 GBK 输出会乱码
- `ConvertTo-Json` 在单对象时不输出数组，Python 侧统一归一化为 list

## 6. 模块划分与接口

```
main.py        入口：加载配置 → 装配服务 → 启动 GUI
config.py      Config 数据类 + JSON 读写 + 默认值 + 校验
network.py     Adapter 数据类；NetworkService 协议 + PowerShellNetworkService
scanner.py     目标枚举（纯函数）+ 并发 TCP Connect
proxy.py       ProxyState 数据类；ProxyService 协议 + 注册表实现
monitor.py     后台线程：代理探测 + 网络快照比对 → 发事件
controller.py  状态机，串起全部服务，不导入 tkinter
gui.py         Tkinter 视图 + 事件泵
```

切分意图：`scanner.py` 不含任何 Windows 代码（纯 socket + 纯函数）；`controller.py` 只依赖 `Protocol` 不依赖实现（测试用 Fake 全量驱动）；只有 `network.py` 与 `proxy.py` 真正接触 Windows，且薄到可以主要靠手动验证。

```python
# network.py
class AdapterKind(Enum):   WIRED, WIRELESS
class AdapterStatus(Enum): UP, DISCONNECTED, DISABLED

@dataclass(frozen=True)
class Adapter:
    name: str; index: int; description: str
    kind: AdapterKind; status: AdapterStatus
    ipv4: str | None            # 已剔除 APIPA
    prefix_length: int | None
    gateway: str | None
    metric: int | None

class NetworkService(Protocol):
    def list_adapters(self) -> list[Adapter]: ...
    def set_adapter_enabled(self, index: int, enabled: bool) -> None: ...

# scanner.py
@dataclass(frozen=True)
class ScanHit:
    ip: str; port: int; latency_ms: float

def enumerate_targets(ipv4, prefix_length, gateway, ports,
                      max_hosts_prefix=24) -> list[tuple[str, int]]: ...
def scan(targets, timeout_ms, concurrency,
         cancel: threading.Event, on_hit) -> list[ScanHit]: ...

# proxy.py
@dataclass(frozen=True)
class ProxyState:
    enable: bool; server: str | None
    override: str | None; auto_config_url: str | None

class ProxyService(Protocol):
    def read(self) -> ProxyState: ...
    def apply(self, server: str) -> None: ...
    def disable(self) -> None: ...
    def restore(self, state: ProxyState) -> None: ...
```

网卡类型判定：`PhysicalMediaType` / `MediaType` 含 `802.11` → `WIRELESS`，否则 `WIRED`。不依据中文名称判断（需求第十节）。

## 7. 扫描

### 7.1 当前网卡选择

自动模式按序：

1. 有默认网关且 `InterfaceMetric` 最小的物理网卡（即 Windows 自身选定的出口）
2. 若无任何网卡有网关，取第一张有非 APIPA IPv4 的物理网卡（场景 B）

GUI 中选中某行再点「扫描代理」则强制扫该网卡，覆盖自动规则。

### 7.2 目标枚举（`enumerate_targets`，纯函数）

1. **安全闸门**：网卡 IPv4 必须落在 `10/8`、`172.16/12`、`192.168/16`。APIPA、回环、任何公网地址一律拒绝扫描并记录原因。这是需求第二十节的执行点，且不接受用户输入的任意网段。
2. **网段计算**：`prefix_length >= 24` 用真实掩码；`< 24` 收窄到本机 IP 所在的 /24。`/31`、`/32` 这类没有可用邻居地址的情况直接产出空目标集，此时只剩网关快路径可用（若连网关也没有，则直接判定「无可扫描目标」并记录原因）。
3. **剔除**：网络地址、广播地址、本机自身 IP。剔除本机是有意为之——开发机运行着 ClashForWindows，若本机监听 7890，扫描会把「自己」报成局域网代理，这不是本工具的目的。
4. **展开**：每个 IP × `ports`（默认 `[7890, 1082]`）。

### 7.3 两阶段扫描

- **阶段一（网关快路径）**：存在默认网关时，先并发探 `gateway:7890` 与 `gateway:1082`。任一命中即结束，不再扫全网段。这是场景 A 的常规路径，耗时约等于一次 TCP 握手。
- **阶段二（全网段）**：网关无命中或无网关时执行。/24 为 254 × 2 = 508 次连接，`ThreadPoolExecutor(scanConcurrency)` + `scanTimeout`，最坏约 3 秒。/28 仅 28 次，瞬时完成。命中通过 `on_hit` 回调即时推送 GUI，不等全部完成。

全程可取消：网卡切换、网络变化、用户重新发起扫描，均先 `cancel.set()` 掐掉在途扫描再开新的。

### 7.4 代理选择

先按 `preferPort`（默认 7890）过滤，有命中则取 TCP 建连耗时最低者；无命中则退到 `ports` 中的下一个端口。即 `7890 > 1082`，同端口比延迟。

延迟仅代表 TCP 建连耗时，不代表代理实际访问互联网的速度，GUI 需显式标注。

GUI 表格展示全部命中，当前使用项高亮，支持手动选中后点「使用选中结果」改选。

## 8. Windows 系统代理

仅操作 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`，使用标准库 `winreg`，不碰 HKLM。

- **`read()`**：读 `ProxyEnable`(DWORD)、`ProxyServer`(SZ)、`ProxyOverride`(SZ)、`AutoConfigURL`(SZ)，缺失键返回 `None` 而非抛异常。程序启动时读一次存为「启动前快照」。
- **`apply(server)`**：`ProxyEnable=1`、`ProxyServer="IP:Port"`，并**删除 `AutoConfigURL`**。删除这一步需求文档未提及，但不做则代理静默失效：`AutoConfigURL`（PAC）存在时 Windows 优先走 PAC，所设 `ProxyServer` 不生效。原值已在启动快照中。
- **`ProxyOverride` 不修改**，保留用户原有绕过列表。
- **`disable()`**：仅置 `ProxyEnable=0`，保留 `ProxyServer` 值以便恢复。
- **刷新**：写完后经 `ctypes` 调 `wininet.dll` 的 `InternetSetOption`，依次发 `INTERNET_OPTION_SETTINGS_CHANGED (39)` 与 `INTERNET_OPTION_REFRESH (37)`。
- **写后校验**：`apply` 之后重新 `read` 确认值已落地，不一致则抛错并记录日志。禁止只记「设置成功」而不校验。

注册表根路径做成可注入参数，供测试指向假键。

## 9. 监控与状态机

### 9.1 监控线程

单线程，`Event.wait(1)` 走 1 秒粒度（退出最多 1 秒收尾），内部两个计时器：

- **网络快照轮询，每 5 秒**：抓物理网卡快照 `(index, name, status, ipv4, prefix, gateway)`，与上次比对，有差异发 `NetworkChanged`（带 diff 文本入日志）。覆盖需求第十四节的网卡启停、连接状态变化、IPv4 变化、网络切换。
  不采用 `NotifyIpInterfaceChange` 事件驱动：5 秒轮询对人的感知足够，可省去一整套 ctypes 回调与跨线程生命周期管理。
- **代理健康探测，每 `monitorInterval`（默认 30 秒）**：对当前代理做一次 TCP Connect，超时用 `scanTimeout`。成功则失败计数清零并发 `ProxyOk`；失败累加，达 `proxyCheckFailures`（默认 3）发 `ProxyLost`。
  默认值意味着代理失效后最长 90 秒才触发重扫；调 `monitorInterval` 即可加快，无需改代码。

**去抖动**：网卡切换、Wi-Fi 重连会在数秒内连抛多次变化。控制器要求快照**连续两次轮询一致**（约 5–10 秒）才认定网络稳定并触发重扫。

### 9.2 控制器状态机

状态：`IDLE` / `SCANNING` / `PROXY_ACTIVE` / `NO_PROXY` / `SWITCHING`。

```
IDLE ──启动且 autoScan──→ SCANNING
SCANNING ──命中 + autoSetProxy──→ apply() ──→ PROXY_ACTIVE
SCANNING ──命中 + 不自动设置──→ NO_PROXY（结果列表待用户选择）
SCANNING ──无命中──→ NO_PROXY（disableProxyWhenUnavailable=true 时调 disable()）
PROXY_ACTIVE ──ProxyLost──→ SCANNING
NO_PROXY ──用户点「使用选中结果」──→ apply() ──→ PROXY_ACTIVE
PROXY_ACTIVE ──用户点「使用选中结果」（改选其他命中）──→ apply() ──→ PROXY_ACTIVE
PROXY_ACTIVE ──用户点「关闭代理」──→ disable() ──→ NO_PROXY
任意状态 ──NetworkChanged（去抖后）──→ 取消在途扫描 → SCANNING（有可用 IP）或 NO_PROXY
任意状态 ──用户点「启用选中网卡」──→ SWITCHING
```

命名说明：`NO_PROXY` 表示「当前未在使用代理」，不代表扫描结果为空——扫到了但 `autoSetProxy` 为 false 时同样处于该状态，结果照常列在表格里等待用户选择。

### 9.3 SWITCHING 序列

1. 禁用其余全部物理网卡
2. 等待其状态变为 `Disabled`，超时 15 秒
3. 启用目标网卡
4. 等待 `Up` **且取得非 APIPA IPv4**，超时 30 秒（DHCP 与 Wi-Fi 重连均慢），每秒写一行进度日志
5. 进入 `SCANNING`

等待期间控制器**屏蔽 `NetworkChanged`**，避免自身触发的变化打断自己。任一步超时则退回 `NO_PROXY` 并在日志写明卡在哪一步，不保留半启用的中间态。

## 10. 线程模型

```
GUI 主线程 (Tkinter)
  │  点击 → controller.post(消息)
  │  root.after(100) 泵 ui_queue → 仅此处操作 Tk 控件
  ↓
controller 线程（唯一状态机，串行消费 inbox）
  ↑ 完成消息                          ↓ ui_queue
  ├── 扫描线程（临时，一次一个，带 cancel Event）
  ├── 切换线程（临时，串行执行禁用/等待/启用序列）
  └── 监控线程（常驻 daemon）
```

状态机独占一个线程、串行消费单一 `inbox` 队列：GUI 点击、监控事件、扫描/切换完成通知统统投进同一队列。状态转换因此天然互斥，**全程无需任何 `Lock`**。控制器线程自身从不执行耗时操作，扫描与切换交给临时线程，完成后把结果投回 `inbox`。

反方向只投不可变事件对象进 `ui_queue`，GUI 用 `root.after(100, pump)` 取空并刷新。**工作线程不得出现任何 Tk 调用**。需求第十九节「禁止扫描 /24 时冻结 GUI」由此结构保证。

**退出**：窗口关闭 → 投 `Shutdown` → 取消在途扫描 → 按 `restoreProxyOnExit`（默认 false）决定是否还原启动快照 → 各线程限时 join（2 秒）→ `root.destroy()`。工作线程全部 `daemon=True` 兜底。

## 11. GUI

Tkinter，标准库 `ttk`，无第三方 GUI 框架。`grid` 布局，可缩放，多余高度给日志区。默认窗口 900×700。

```
┌─ 网络适配器 ─────────────────────────────────────────┐
│ 类型   名称        描述                  状态    IPv4        │
│ 有线   Ethernet 10 Apple Mobile Device…  已连接  172.20.10.7 │
│ 有线   Ethernet    Realtek PCIe GbE…     已连接  10.0.0.2    │
│ 无线   Wi-Fi       RZ616 Wi-Fi 6E…       未连接  -           │
│                        [启用选中网卡（独占）]  [刷新]        │
├─ HTTP Proxy ─────────────────────────────────────────┤
│ 当前代理：172.20.10.1:7890    状态：TCP 连接正常              │
│              [扫描代理]  [使用选中结果]  [关闭代理]           │
├─ 扫描结果 ───────────────────────────────────────────┤
│ IP            Port   Connect                                 │
│ 172.20.10.1   7890   12ms   ← 当前使用，高亮                 │
│ 172.20.10.1   1082   15ms                                    │
│ 注：Connect 仅为 TCP 建连耗时，不代表代理实际访问速度        │
├─ 日志 ───────────────────────────────────────────────┤
│ 12:30:01 当前网卡：Ethernet 10 (Apple Mobile Device Ethernet)│
│ 12:30:01 IP：172.20.10.7/28  网段：172.20.10.0/28            │
│ 12:30:01 优先检测 Gateway：172.20.10.1                       │
│ 12:30:01 172.20.10.1:7890 TCP 连接成功 (12ms)                │
│ 12:30:01 设置 Windows 系统代理成功，已校验                   │
└──────────────────────────────────────────────────────┘
```

- 网卡列表列：类型 / 名称 / InterfaceDescription / 状态 / IPv4（需求第十节）
- 进入 `SCANNING` / `SWITCHING` 时相关按钮置灰，防重入
- 日志区只读、自动滚到底、保留最近 2000 行滚动丢弃
- 日志**只在 GUI 内显示，不写日志文件**。需求未要求落盘，且程序以管理员运行时在 exe 所在目录写文件容易踩到权限与路径问题（如放在 `Program Files` 下）

## 12. 配置

JSON，路径：冻结运行时为 `Path(sys.executable).parent / "config.json"`；源码运行时为项目根目录。**不可**使用脚本所在目录，`--onefile` 会解压到临时目录，导致用户修改每次重启即丢失。

```json
{
  "ports": [7890, 1082],
  "preferPort": 7890,
  "scanTimeout": 500,
  "scanConcurrency": 100,
  "monitorInterval": 30,
  "proxyCheckFailures": 3,
  "autoScan": true,
  "autoSetProxy": true,
  "disableProxyWhenUnavailable": false,
  "restoreProxyOnExit": false
}
```

- `ports`：扫描端口
- `preferPort`：优先选择的端口
- `scanTimeout`：TCP Connect 超时，毫秒
- `scanConcurrency`：并发扫描数
- `monitorInterval`：代理监控间隔，秒
- `proxyCheckFailures`：连续失败多少次判定代理失效
- `autoScan`：启动时自动扫描
- `autoSetProxy`：发现代理后自动设置
- `disableProxyWhenUnavailable`：找不到代理时是否关闭系统代理
- `restoreProxyOnExit`：退出时是否还原启动前的代理状态

文件缺失时写入默认值；字段缺失用默认值补齐；非法值记录日志并回退默认值，不崩溃。

## 13. 错误处理

原则：**worker 线程中任何异常都不得静默消失**。每个工作线程外包一层捕获，异常转 `ErrorEvent` 投回 `inbox`，控制器记日志并退回安全状态（`NO_PROXY`），进程不崩。

- **PowerShell 层**：退出码非 0、输出为空、JSON 解析失败 → 抛 `NetworkError`，附命令与 stderr 尾部
- **启停网卡失败**（驱动拒绝、网卡被占用）：无法可靠回滚，日志必须写明「哪张卡、哪一步、系统返回什么」，并把网卡列表刷新为真实状态，不得假装成功
- **扫描**：单个 socket 的 timeout / refused 属正常「未开放」，静默处理；若**全部**目标返回同一 `Network unreachable`，判定为网卡刚掉线，给出明确提示而非「未发现代理」
- **注册表**：写入后读回校验，不一致即抛错

## 14. 测试策略

`pytest`，仅开发依赖，不进打包产物。

**纯逻辑单测（无需 Windows / 无需网络）——测试主体：**

- `enumerate_targets`：私有段闸门（公网、APIPA 必须拒绝）、/28 用真实掩码、/16 收窄到 /24、剔除网络地址 / 广播 / 本机 IP
- 代理选择：7890 优先于 1082、同端口比延迟、无 7890 时退到 1082
- `config`：默认值、字段缺失、非法值
- 网卡解析：以第 4 节实测的真实 PowerShell JSON（12 张网卡那份）为 fixture，验证虚拟网卡被过滤、三张物理网卡的类型与状态判定、APIPA 被置空
- 快照比对与去抖动
- 控制器状态机：全部用 Fake 服务驱动，覆盖「扫到 / 没扫到 / 代理掉线 / 网络变化 / 切换超时」各路径

**真实但无副作用的集成测试：**

- `scanner.scan`：本地起若干监听 socket，验证命中、未命中、超时、并发上限、取消
- `proxy.py`：注册表路径注入为 `HKCU\Software\LANProxySwitcher\TestSettings`，**永不触碰真实代理设置**
- `network.list_adapters()`：真实调用（只读），断言至少一张物理网卡且字段自洽

**只能手动验证（交付一份清单逐条执行）：**

1. 网卡真实启停与独占切换
2. iPhone 共享网络全流程（发现 `172.20.10.1:7890` 并设代理）
3. A/B 直连全流程（无网关，靠全网段扫描发现）
4. 代理掉线后自动重扫并切换
5. 退出时代理状态行为（`restoreProxyOnExit` 两种取值）

## 15. 打包

```
PyInstaller --onefile --windowed --uac-admin --name LANProxySwitcher
```

**风险前置**：开发机为 Python 3.14.3，属较新版本，PyInstaller 的支持情况需先验证。因此在编写任何功能代码之前，先用一个空 Tkinter 窗口跑通一次完整打包与启动，把该风险清除，而不是等功能完成后才发现无法打包。
