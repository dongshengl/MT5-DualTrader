# MT5 Dual-Mode Auto Trader（MT5 双模式自动交易）

基于 MetaTrader 5 的 Windows 桌面自动交易工具：ADX 识别行情状态，自动在**顺势 / 震荡**两套入场逻辑间切换，配合 GUI 实时监控持仓与止盈止损。

> **完全免费，无授权密码**：本项目基于 AGPL-3.0 开源，无需激活码，下载即用。
> 交易有风险，本工具仅供学习研究，据此实盘操作盈亏自负。

## 架构

```
┌──────────────┐   JSON over TCP (localhost:19527)   ┌──────────────────┐
│  gui_dual.py │ ←----------------------------------→ │    service.py    │
│  tkinter GUI │                                      │   后台交易服务    │
└──────────────┘                                      └────────┬─────────┘
                                                               │
                                                      ┌────────▼─────────┐
                                                      │  trader_dual.py  │
                                                      │ 策略引擎（双模式）│
                                                      └────────┬─────────┘
                                                               │
                                                      ┌────────▼─────────┐
                                                      │   MetaTrader 5   │
                                                      └──────────────────┘
```

- **service.py**：独立后台进程，不依赖 GUI / 桌面会话（断开远程桌面后交易继续运行），负责连接 MT5、执行策略、持久化日志
- **gui_dual.py**：参数配置、启停控制、实时状态与日志展示
- **trader_dual.py**：策略引擎，可通过 GUI 热改参数

## 策略逻辑（ADX 双模式）

| 行情状态 | 判定 | 入场逻辑 |
|---|---|---|
| 趋势 | ADX ≥ 25 | 顺势：价格站上均线且 RSI 未极端时顺方向开单 |
| 震荡 | ADX < 20 | 逆势：价格偏离均线 ≥ 阈值且 RSI 到超买/超卖时反向开单 |
| 过渡区 | 20 ≤ ADX < 25 | 静默，不开新单 |

持仓管理：每笔订单带初始止损，按 `TAKE_PROFIT_USD` / `STOP_LOSS_USD`（美元口径）控制，`ORDER_INTERVAL_SEC` 控制开单节奏。

## 使用方法

1. Windows + 已登录的 MetaTrader 5 终端（算法交易按钮开启）
2. 安装依赖：

   ```
   pip install MetaTrader5 pyautogui pygetwindow
   ```

3. 启动后台服务：`python service.py`
4. 启动 GUI：`python gui_dual.py`，填参数后点「启动」

打包单文件 exe 可用 `scripts/` 下的 PyInstaller spec：

```
pyinstaller scripts/MT5自动交易.spec
```

## 配置说明（config.json，GUI 可视化修改）

| 键 | 说明 | 默认 |
|---|---|---|
| MT5_ACCOUNT_FILTER | 目标 MT5 账号，0 = 使用当前已登录账号 | 0 |
| SYMBOL | 交易品种（不同经纪商后缀可能不同，如 XAUUSDm） | XAUUSDm |
| TIMEFRAME | K 线周期 M1~D1 | M5 |
| STRATEGY_MODE | 手动模式下的固定策略：shunshi（顺势）/ nishi（逆势）；AUTO_MODE=true 时由 ADX 自动切换，此键不生效 | shunshi |
| ADX_RANGE_MAX / ADX_TREND_MIN | 模式切换阈值 | 20 / 25 |
| LOT_SIZE | 每单手数 | 0.01 |
| TAKE_PROFIT_USD / STOP_LOSS_USD | 单笔止盈/止损（美元） | 30 / 5000 |

完整示例见 [config.example.json](config.example.json)。

## 用 AI 改进本项目

本项目由 AI（GLM）在用户的自然语言指导下编写调试。欢迎你把自己的需求交给任意 AI 助手（Claude / ChatGPT / Cursor 等），附上本 README 和源码即可快速上手修改；欢迎提交 PR 并注明 AI 参与情况。

## License

AGPL-3.0，详见 [LICENSE](LICENSE)。
