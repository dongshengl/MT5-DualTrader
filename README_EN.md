# MT5 Fast Close & Dual-Mode Trader

> **English** | [中文](README.md)

> **Core purpose**: when an account holds many open positions, closing them one by one from an EA inside MT5 is painfully slow. This tool calls the MT5 terminal's built-in **"Close All"** directly: it locates the MT5 window, opens the terminal context menu, walks down the menu with the arrow keys to "Close All", and executes it. **Dozens or hundreds of positions are cleared in a single action.**
> Everything else (ADX/RSI dual-mode entries, background service, GUI monitoring) is auxiliary - MT5 EAs can already implement trading strategies on their own.

## Why it exists

| Scenario | Native MT5 / EA behaviour | This tool |
|---|---|---|
| 1 to a few dozen positions | EA sends close requests one by one - slow but usable | Uses the MT5 API per position (same path, less babysitting) |
| **Dozens to hundreds of positions** | EA loops position by position, getting slower while the market runs away | Calls the terminal's "Close All" directly, clearing everything at once |

The tool decides automatically: `close_all_smart()` reads the current position count and uses the **per-position API path below 50 positions**, switching to the **GUI one-click close-all at 50 or more** (see `CLOSE_THRESHOLD`).

## How the fast close works

```
Locate the MT5 window (match by title / account, multi-instance aware)
        │
        ▼
Activate the window to the foreground via Win32
        │
        ▼
Right-click inside the terminal area (at 75% of window height)
        │
        ▼
↓↓↓↓   (walk to the "Close All" entry with the arrow keys)
        │
        ▼
→ expand the submenu → Enter → Enter
        │
        ▼
MT5 terminal executes close-all
```

> **Evolution**: early versions located the menu with **on-screen image recognition** (matching a screenshot of the button), which broke whenever the resolution or theme changed.
> The current implementation uses **window handle targeting plus arrow-key menu navigation** instead, relying on no screenshot assets at all - far more stable.
> When it is done, focus is returned to the window you were using, so it never interrupts your work.

## Architecture

```
┌──────────────┐   JSON over TCP (localhost:19527)   ┌──────────────────┐
│  gui_dual.py │ ←----------------------------------→ │    service.py    │
│  tkinter GUI │                                      │  background svc  │
└──────────────┘                                      └────────┬─────────┘
                                                               │
                                                      ┌────────▼─────────┐
                                                      │  trader_dual.py  │
                                                      │ strategy + close │
                                                      └────────┬─────────┘
                                                     ┌─────────┴─────────┐
                                                     ▼                   ▼
                                             MT5 Python API      window / keyboard
                                          (orders, quotes,      automation
                                           per-position close)  (one-click close-all)
```

- **service.py**: standalone background process, independent of the GUI and the desktop session (trading continues after an RDP disconnect)
- **gui_dual.py**: parameter configuration, start/stop control, live status and logs, emergency close-all
- **trader_dual.py**: ADX/RSI dual-mode strategy plus smart close-all scheduling

## Auxiliary: ADX dual-mode entries

| Market state | Rule | Entry logic |
|---|---|---|
| Trending | ADX >= 25 | Trend-following: enter in the direction of the trend when price is beyond the moving average and RSI is not extreme |
| Ranging | ADX < 20 | Mean-reverting: enter against the move when price deviates from the moving average by at least the threshold and RSI is overbought/oversold |
| Transition | 20 <= ADX < 25 | Stay flat, no new entries |

This part overlaps with what MT5 EAs already do. It is bundled for convenience, not the point of this project.

## Getting started

1. Windows with a logged-in MetaTrader 5 terminal (Algo Trading enabled)
2. Install dependencies:

   ```
   pip install MetaTrader5 pyautogui pygetwindow
   ```

3. Start the background service: `python service.py`
4. Start the GUI: `python gui_dual.py`, fill in the parameters and click Start
5. Click "Close All" when you need to flatten the account, or let the strategy trigger take-profit / stop-loss automatically

A single-file exe can be built with the PyInstaller specs under `scripts/`:

```
pyinstaller scripts/MT5自动交易.spec
```

## Configuration (config.json, editable in the GUI)

| Key | Meaning | Default |
|---|---|---|
| MT5_ACCOUNT_FILTER | Target MT5 account, 0 = use the currently logged-in account | 0 |
| SYMBOL | Trading symbol (suffixes vary by broker, e.g. XAUUSDm) | XAUUSDm |
| TIMEFRAME | Chart timeframe, M1 to D1 | M5 |
| STRATEGY_MODE | Fixed strategy in manual mode: shunshi (trend) / nishi (counter-trend); ignored when AUTO_MODE=true, where ADX switches automatically | shunshi |
| ADX_RANGE_MAX / ADX_TREND_MIN | Mode-switching thresholds | 20 / 25 |
| LOT_SIZE | Lot size per order | 0.01 |
| TAKE_PROFIT_USD / STOP_LOSS_USD | Take-profit / stop-loss per trade, in USD | 30 / 5000 |

See [config.example.json](config.example.json) for a full example.

## FAQ

See [FAQ](FAQ.md) for details about the smart-close threshold, MT5 window requirements, the limits of RDP-disconnected operation, and demo-account testing recommendations.

## License

AGPL-3.0, see [LICENSE](LICENSE).

Trading carries risk. This tool is for study and research only; you are responsible for any losses from live use.
