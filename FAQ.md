# FAQ

## 中文

### 1. 这个项目最主要解决什么问题？

它主要解决 MT5 账户持仓很多时，EA 逐单发送平仓请求速度慢的问题。持仓达到阈值后，程序定位 MT5 窗口，调用终端自身的菜单和“全部平仓”功能，一次处理全部持仓。

ADX/RSI 双模式开仓和后台服务是辅助功能，不是本项目的核心卖点。

### 2. 什么时候走 API，什么时候走终端一键全平？

`close_all_smart()` 会读取当前持仓数：

- 少于 50 单：通过 MT5 Python API 逐单平仓；
- 50 单及以上：切换到 MT5 窗口菜单，使用键盘导航执行终端的“全部平仓”。

阈值定义在 `trader_dual.py` 的 `CLOSE_THRESHOLD` 中。

### 3. 使用终端一键全平时，MT5 窗口必须满足什么条件？

MT5 终端必须已经启动并登录目标账户，窗口能够被程序找到和激活。执行终端菜单方式时，MT5 窗口需要允许被切换到前台；不要在程序执行期间操作鼠标或键盘，以免菜单焦点被改变。

如果同时打开多个 MT5 终端，请在配置中设置 `MT5_ACCOUNT_FILTER`，帮助程序选择目标账户。

### 4. 断开远程桌面后还能运行吗？

后台服务与 GUI 分开运行，交易循环可以在 RDP 断开后继续运行。但“终端菜单 + 键盘导航”的快速全平依赖 Windows 图形界面和可激活的 MT5 窗口。不要把 GUI 平仓能力理解成完全无桌面的服务器接口。

### 5. 为什么没有找到 MT5 窗口？

请依次检查：

1. MT5 是否已经启动并登录；
2. 目标账号是否与 `MT5_ACCOUNT_FILTER` 一致；
3. 是否同时打开了多个 MT5 终端；
4. 是否有权限或远程桌面策略阻止窗口切换到前台；
5. 是否有其他程序抢占了鼠标和键盘焦点。

### 6. 能不能直接用于实盘？

建议先使用模拟账户验证窗口匹配、菜单位置、平仓结果和日志。程序会处理平仓动作，但无法保证经纪商成交速度、滑点或网络状况。实盘前应先确认自己理解“一键全平”的不可逆后果。

### 7. 运行 GUI 前需要先启动什么？

先启动 `service.py`，再启动 `gui_dual.py`。两者通过 `127.0.0.1:19527` 通信。如果提示端口被占用，通常是已有服务实例正在运行，或上一次进程没有正常退出。

### 8. 这个项目会保存我的 MT5 密码吗？

示例配置中的 `MT5_ACCOUNT_FILTER` 默认为 0，表示使用当前已登录的 MT5 账号。请不要把账号密码、API 密钥或个人配置文件提交到公开仓库。

## English

### What is the main purpose of this project?

It addresses the slow one-by-one close requests that occur when an MT5 account has many open positions. Once the position count reaches the threshold, the program locates the MT5 window and invokes the terminal's native **Close All** menu action.

The ADX/RSI entry logic and background service are auxiliary features, not the project's main purpose.

### When does it use the API or the terminal menu?

`close_all_smart()` reads the current position count:

- Below 50 positions: close positions through the MT5 Python API;
- 50 positions or more: activate the MT5 window and navigate to the terminal's native Close All action with the keyboard.

The threshold is defined by `CLOSE_THRESHOLD` in `trader_dual.py`.

### Does it work after an RDP disconnect?

The background service is separate from the GUI, so its trading loop can continue after an RDP disconnect. The terminal-menu close-all path still depends on a Windows graphical session and an MT5 window that can be activated. It is not a fully headless server interface.

### Is live trading recommended?

Start with a demo account. Verify window matching, menu navigation, close results, and logs before live use. Execution speed, slippage, and network conditions are outside this project's control. A Close All action is irreversible, so use it only when you understand the consequence.

### What should I do if the MT5 window is not found?

Check that MT5 is running and logged in, that `MT5_ACCOUNT_FILTER` matches the target account, and that no other MT5 instance or application is taking keyboard focus.

### How do I start the program?

Start `service.py` first, then start `gui_dual.py`. They communicate through `127.0.0.1:19527`. A port-in-use message usually means another service instance is already running.

### Does the project store my MT5 password?

The example configuration uses `MT5_ACCOUNT_FILTER: 0`, meaning the currently logged-in MT5 account is used. Never commit account passwords, API keys, or personal configuration files to the public repository.
