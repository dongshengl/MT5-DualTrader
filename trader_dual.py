# ============================================================
# trader_dual.py - MT5 自动交易 v2
# 改进入场逻辑：ADX趋势强度 + RSI动量确认 + 偏离度过滤
#
# 策略框架：
#   顺势模式 (ADX >= ADX_TREND_MIN)：
#     BUY  = 价格站上均线 + RSI < RSI_TREND_OB（不追极端超买）
#     SELL = 价格跌破均线 + RSI > RSI_TREND_OS（不追极端超卖）
#
#   逆势模式 (ADX < ADX_RANGE_MAX)：
#     BUY  = 价格偏离均线向下 >= BAND_WIDTH% + RSI <= RSI_OVERSOLD
#     SELL = 价格偏离均线向上 >= BAND_WIDTH% + RSI >= RSI_OVERBOUGHT
#
#   过渡区 (ADX_RANGE_MAX <= ADX < ADX_TREND_MIN)：静默，不开新单
# ============================================================
import json
import os
import sys
import re
import time
import ctypes
import logging
import datetime
import collections

import MetaTrader5 as mt5
import pyautogui
import pygetwindow as gw

# ---- 全局状态 ----
_stop_flag = False
_stats = {}
_log_queue = collections.deque(maxlen=500)
_mt5_connected = False

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("trader")


class _QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_queue.append(self.format(record))
        except Exception:
            pass


_qh = _QueueHandler()
_qh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
_logger.addHandler(_qh)
_logger.propagate = False


# ============================================================
# 配置
# ============================================================
DEFAULTS = {
    "MT5_ACCOUNT_FILTER": 0,
    "SYMBOL": "XAUUSDm",
    "TIMEFRAME": "M5",
    "MA_PERIOD": 28,
    "LOT_SIZE": 0.01,
    "ORDER_INTERVAL_SEC": 60,
    "TAKE_PROFIT_USD": 30.0,
    "STOP_LOSS_USD": 5000.0,
    "STRATEGY_MODE": "shunshi",
    "ORDER_DIRECTION": "both",

    # --- 自动模式 ---
    "AUTO_MODE": True,

    # --- ADX 参数 ---
    "ADX_PERIOD": 14,
    "ADX_RANGE_MAX": 20,      # ADX < 20  → 震荡，做逆势
    "ADX_TREND_MIN": 25,      # ADX >= 25 → 趋势，做顺势
                               # 20 ~ 25  → 过渡区，静默不开单

    # --- RSI 参数 ---
    "RSI_PERIOD": 14,
    "RSI_OVERBOUGHT": 70,     # 超买线（逆势卖出 / 顺势不追）
    "RSI_OVERSOLD": 30,       # 超卖线（逆势买入 / 顺势不追）

    # --- 逆势偏离度 ---
    "BAND_WIDTH": 0.15,       # 价格偏离均线 >= 0.15% 才做逆势
                               # 黄金M5建议 0.10~0.25%

    # --- 顺势 RSI 安全区间 ---
    "RSI_TREND_OB": 75,       # 顺势做多时 RSI < 75（不追极端超买）
    "RSI_TREND_OS": 25,       # 顺势做空时 RSI > 25（不追极端超卖）
}

TIMEFRAME_MAP = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 16385, "H4": 16387, "D1": 16408,
}


def _exe_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _config_path():
    return os.path.join(_exe_dir(), "config.json")


def load_cfg() -> dict:
    path = _config_path()
    if not os.path.exists(path):
        return dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return dict(DEFAULTS)
    if isinstance(data, list):
        if data:
            acc = dict(DEFAULTS)
            acc.update({k: v for k, v in data[0].items() if k in DEFAULTS})
            return acc
        return dict(DEFAULTS)
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    return cfg


def save_cfg(cfg: dict):
    path = _config_path()
    out = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=4)


def get_timeframe(cfg: dict) -> int:
    return TIMEFRAME_MAP.get(cfg.get("TIMEFRAME", "M5"), 5)


# ============================================================
# 技术指标计算
# ============================================================

def calculate_adx(symbol: str, timeframe: int, adx_period: int = 14, count: int = 100) -> tuple:
    """
    计算 ADX 指标。
    返回 (adx, plus_di, minus_di)，失败返回 (-1, 0, 0)。
    plus_di > minus_di 表示上升趋势，反之下降趋势。
    """
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) < adx_period + 15:
        _logger.warning(f"ADX 计算失败：K线数据不足 ({mt5.last_error()})")
        return -1.0, 0.0, 0.0

    highs = [r["high"] for r in rates]
    lows = [r["low"] for r in rates]
    closes = [r["close"] for r in rates]

    tr_list, plus_dm_list, minus_dm_list = [], [], []
    for i in range(1, len(rates)):
        h, l, c_prev = highs[i], lows[i], closes[i - 1]
        tr_list.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm_list.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm_list.append(down_move if down_move > up_move and down_move > 0 else 0.0)

    # Wilder's smoothing（RMA）
    def wilder_smooth(values, period):
        if len(values) < period:
            return []
        result = [sum(values[:period])]
        for v in values[period:]:
            result.append(result[-1] - result[-1] / period + v)
        return result

    tr_s = wilder_smooth(tr_list, adx_period)
    pdm_s = wilder_smooth(plus_dm_list, adx_period)
    mdm_s = wilder_smooth(minus_dm_list, adx_period)

    if not tr_s:
        return -1.0, 0.0, 0.0

    plus_di_list, minus_di_list, dx_list = [], [], []
    for i in range(len(tr_s)):
        if tr_s[i] == 0:
            plus_di_list.append(0.0)
            minus_di_list.append(0.0)
            dx_list.append(0.0)
        else:
            pdi = 100 * pdm_s[i] / tr_s[i]
            mdi = 100 * mdm_s[i] / tr_s[i]
            plus_di_list.append(pdi)
            minus_di_list.append(mdi)
            denom = pdi + mdi
            dx_list.append(100 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    if len(dx_list) < adx_period:
        return -1.0, 0.0, 0.0

    # ADX = Wilder smooth of DX
    adx_series = wilder_smooth(dx_list, adx_period)
    if not adx_series:
        return -1.0, 0.0, 0.0

    adx_val = round(adx_series[-1], 2)
    pdi_val = round(plus_di_list[-1], 2)
    mdi_val = round(minus_di_list[-1], 2)
    return adx_val, pdi_val, mdi_val


def calculate_rsi(symbol: str, timeframe: int, rsi_period: int = 14, count: int = 60) -> float:
    """
    计算 RSI 指标。
    返回最新 RSI 值（0~100），失败返回 -1。
    """
    need = rsi_period + 20
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, max(count, need))
    if rates is None or len(rates) < rsi_period + 2:
        _logger.warning(f"RSI 计算失败：K线数据不足")
        return -1.0

    closes = [r["close"] for r in rates]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    if len(gains) < rsi_period:
        return -1.0

    # 初始平均
    avg_gain = sum(gains[:rsi_period]) / rsi_period
    avg_loss = sum(losses[:rsi_period]) / rsi_period

    # Wilder's smoothing
    for i in range(rsi_period, len(gains)):
        avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
        avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def get_price_deviation(price: float, ma: float) -> float:
    """
    计算价格偏离均线的百分比（带符号）。
    正值 = 价格高于均线，负值 = 价格低于均线。
    """
    if ma == 0:
        return 0.0
    return round((price - ma) / ma * 100, 4)


# ============================================================
# 行情类型判断
# ============================================================
_last_market_check = 0.0
_cached_market_type = "silent"   # silent / shunshi / nishi
_cached_adx = 0.0
_cached_pdi = 0.0
_cached_mdi = 0.0


def detect_market_type(cfg: dict) -> str:
    """
    判断当前行情类型，同时更新全局缓存的 ADX/+DI/-DI。
    返回：
      "shunshi" → ADX >= ADX_TREND_MIN，趋势行情
      "nishi"   → ADX < ADX_RANGE_MAX，震荡行情
      "silent"  → 过渡区，不开新单
    """
    global _cached_adx, _cached_pdi, _cached_mdi

    symbol = cfg.get("SYMBOL", "XAUUSDm")
    tf = get_timeframe(cfg)
    adx_period = cfg.get("ADX_PERIOD", 14)
    adx_range_max = cfg.get("ADX_RANGE_MAX", 20)
    adx_trend_min = cfg.get("ADX_TREND_MIN", 25)

    sym_info = mt5.symbol_info(symbol)
    if sym_info is None or not sym_info.visible:
        _logger.info(f"ADX计算：尝试添加品种 {symbol}...")
        mt5.symbol_select(symbol, True)
        time.sleep(0.5)

    adx_val, pdi, mdi = calculate_adx(symbol, tf, adx_period)
    _cached_adx = adx_val
    _cached_pdi = pdi
    _cached_mdi = mdi

    if adx_val < 0:
        _logger.warning("ADX 计算失败，默认使用震荡策略（逆势）")
        return "nishi"

    if adx_val >= adx_trend_min:
        _logger.info(f"行情判断：ADX={adx_val} >= {adx_trend_min}，趋势行情 → 顺势")
        return "shunshi"
    elif adx_val < adx_range_max:
        _logger.info(f"行情判断：ADX={adx_val} < {adx_range_max}，震荡行情 → 逆势")
        return "nishi"
    else:
        _logger.info(f"行情判断：ADX={adx_val} 在过渡区({adx_range_max}~{adx_trend_min})，静默不开单")
        return "silent"


def get_current_market_type() -> str:
    return _cached_market_type


def get_current_indicators() -> dict:
    """返回最新指标缓存，供 GUI 显示。"""
    return {
        "adx": _cached_adx,
        "plus_di": _cached_pdi,
        "minus_di": _cached_mdi,
    }


# ============================================================
# 核心策略：decide()
# ============================================================
def decide(price: float, ma: float, rsi: float, cfg: dict) -> str | None:
    """
    决定是否开单及方向。

    顺势逻辑：
      - ADX >= ADX_TREND_MIN，且 +DI > -DI（上升趋势）→ 考虑 BUY
      - ADX >= ADX_TREND_MIN，且 -DI > +DI（下降趋势）→ 考虑 SELL
      - RSI 过滤：做多要求 RSI < RSI_TREND_OB（不追极端超买）
                 做空要求 RSI > RSI_TREND_OS（不追极端超卖）

    逆势逻辑：
      - ADX < ADX_RANGE_MAX（震荡），价格偏离均线 >= BAND_WIDTH%
      - 价格高于均线 + RSI >= RSI_OVERBOUGHT → SELL（高了卖）
      - 价格低于均线 + RSI <= RSI_OVERSOLD   → BUY（低了买）

    过渡区（ADX_RANGE_MAX <= ADX < ADX_TREND_MIN）：返回 None，静默等待

    返回 "BUY" / "SELL" / None
    """
    global _last_market_check, _cached_market_type

    # ----- 行情类型（带缓存，每60秒重算一次）-----
    if cfg.get("AUTO_MODE", True):
        now = time.time()
        if now - _last_market_check > 60:
            _cached_market_type = detect_market_type(cfg)
            _last_market_check = now
        mode = _cached_market_type
    else:
        mode = cfg.get("STRATEGY_MODE", "shunshi")

    direction_filter = cfg.get("ORDER_DIRECTION", "both")

    # 过渡区：不开单
    if mode == "silent":
        return None

    rsi_ob = cfg.get("RSI_OVERBOUGHT", 70)
    rsi_os = cfg.get("RSI_OVERSOLD", 30)
    rsi_trend_ob = cfg.get("RSI_TREND_OB", 75)
    rsi_trend_os = cfg.get("RSI_TREND_OS", 25)
    band_width = cfg.get("BAND_WIDTH", 0.15)

    deviation = get_price_deviation(price, ma)

    raw = None

    if mode == "shunshi":
        # 顺势：借助 +DI/-DI 判断方向，RSI 做安全过滤
        if rsi < 0:
            # RSI 数据不足，降级到简单均线判断
            if price > ma:
                raw = "BUY"
            elif price < ma:
                raw = "SELL"
        else:
            pdi = _cached_pdi
            mdi = _cached_mdi
            if pdi > mdi and price > ma:
                # 上升趋势，且价格站上均线 → BUY
                if rsi < rsi_trend_ob:  # RSI 未极端超买才追
                    raw = "BUY"
                else:
                    _logger.info(f"顺势BUY条件满足，但RSI={rsi} >= {rsi_trend_ob}（超买），跳过")
            elif mdi > pdi and price < ma:
                # 下降趋势，且价格跌破均线 → SELL
                if rsi > rsi_trend_os:  # RSI 未极端超卖才追
                    raw = "SELL"
                else:
                    _logger.info(f"顺势SELL条件满足，但RSI={rsi} <= {rsi_trend_os}（超卖），跳过")
            else:
                # 趋势方向与价格位置不一致，等待确认
                _logger.info(f"顺势：趋势方向与价格位置不一致，等待确认 "
                             f"(+DI={pdi:.1f}, -DI={mdi:.1f}, price={'>' if price>ma else '<'}MA)")

    elif mode == "nishi":
        # 逆势：需要偏离度达标 + RSI 极端才入场
        abs_dev = abs(deviation)
        if abs_dev < band_width:
            _logger.info(f"逆势：偏离度 {deviation:+.3f}% < {band_width}%，等待更大偏离")
            return None

        if rsi < 0:
            # RSI 数据不足，仅用偏离度判断
            raw = "BUY" if deviation < 0 else "SELL"
        else:
            if deviation < 0:
                # 价格低于均线，等待超卖做多
                if rsi <= rsi_os:
                    raw = "BUY"
                else:
                    _logger.info(f"逆势BUY偏离达标({deviation:.3f}%)，但RSI={rsi} > {rsi_os}，等待超卖")
            else:
                # 价格高于均线，等待超买做空
                if rsi >= rsi_ob:
                    raw = "SELL"
                else:
                    _logger.info(f"逆势SELL偏离达标({deviation:.3f}%)，但RSI={rsi} < {rsi_ob}，等待超买")

    if raw is None:
        return None

    # 方向过滤
    if direction_filter == "buy_only" and raw != "BUY":
        return None
    if direction_filter == "sell_only" and raw != "SELL":
        return None
    return raw


# ============================================================
# 停止控制
# ============================================================
def request_stop():
    global _stop_flag
    _stop_flag = True
    _logger.info("已请求停止交易...")


# ============================================================
# 日志队列
# ============================================================
def get_logs(clear=False) -> list:
    lines = list(_log_queue)
    if clear:
        _log_queue.clear()
    return lines


# ============================================================
# 统计数据
# ============================================================
def _init_stats():
    global _stats
    _stats = {
        "account_no": 0, "balance": 0.0, "equity": 0.0,
        "profit": 0.0,
        "pos_count": 0, "buy_count": 0, "sell_count": 0,
        "day_profit": 0.0, "week_profit": 0.0,
        "month_profit": 0.0, "year_profit": 0.0,
        "max_float_loss": 0.0,
        "last_update": "",
    }


def get_stats(symbol=None) -> dict:
    """返回统计数据，同时附带最新指标缓存（供 service.py 一并返回给 GUI）。"""
    result = dict(_stats)
    # 附带指标数据
    result["adx"] = _cached_adx
    result["plus_di"] = _cached_pdi
    result["minus_di"] = _cached_mdi
    result["market_type"] = _cached_market_type
    return result


def _update_stats(cfg: dict):
    global _stats
    try:
        info = mt5.account_info()
        if info is None:
            return
        _stats["account_no"] = info.login
        _stats["balance"] = round(info.balance, 2)
        _stats["equity"] = round(info.equity, 2)

        symbol = cfg.get("SYMBOL", "XAUUSDm")
        positions = mt5.positions_get(symbol=symbol) or []
        buy_count = sum(1 for p in positions if p.type == mt5.ORDER_TYPE_BUY)
        sell_count = sum(1 for p in positions if p.type == mt5.ORDER_TYPE_SELL)
        current_profit = round(sum(p.profit for p in positions), 2)

        _stats["pos_count"] = len(positions)
        _stats["buy_count"] = buy_count
        _stats["sell_count"] = sell_count
        _stats["profit"] = current_profit

        prev_max_loss = _stats.get("max_float_loss", 0.0)
        if current_profit < prev_max_loss:
            _stats["max_float_loss"] = current_profit

        now = datetime.datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - datetime.timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)
        year_start = today_start.replace(month=1, day=1)

        def _sum_deals(from_dt: datetime.datetime) -> float:
            deals = mt5.history_deals_get(from_dt, now)
            if not deals:
                return 0.0
            total = 0.0
            for d in deals:
                if d.entry in (1, 3):
                    total += d.profit
            return round(total, 2)

        _stats["day_profit"] = _sum_deals(today_start)
        _stats["week_profit"] = _sum_deals(week_start)
        _stats["month_profit"] = _sum_deals(month_start)
        _stats["year_profit"] = _sum_deals(year_start)
        _stats["last_update"] = now.strftime("%H:%M:%S")
    except Exception as e:
        _logger.warning(f"统计更新失败: {e}")


# ============================================================
# MT5 连接
# ============================================================
def init_mt5(cfg: dict) -> bool:
    global _mt5_connected
    target_account = cfg.get("MT5_ACCOUNT_FILTER", 0)

    if _mt5_connected:
        info = mt5.account_info()
        if info is not None:
            if not target_account or info.login == target_account:
                return True
        _mt5_connected = False

    try:
        mt5.shutdown()
    except Exception:
        pass

    ok = mt5.initialize()
    if not ok:
        err = mt5.last_error()
        _logger.error(f"MT5 初始化失败: {err}")
        return False

    info = mt5.account_info()
    if info is None:
        _logger.error("MT5 已初始化但无法获取账户信息，请确认 MT5 已登录。")
        mt5.shutdown()
        return False

    if target_account and info.login != target_account:
        _logger.error(f"账号不匹配：目标={target_account}, 实际={info.login}。")
        mt5.shutdown()
        return False

    _mt5_connected = True
    _logger.info(
        f"已连接 MT5 | 账号: {info.login} | "
        f"服务器: {info.server} | 余额: {info.balance:.2f}"
    )
    return True


# ============================================================
# MT5 窗口管理
# ============================================================
def _win32_activate(hwnd: int) -> bool:
    try:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        time.sleep(0.1)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)
        return True
    except Exception as e:
        _logger.warning(f"Win32 激活窗口异常: {e}")
        return False


def list_mt5_windows() -> list:
    results = []
    seen = set()
    keywords = ["metatrader", "mt5", "\u5143\u4ea4\u6613"]
    try:
        for w in gw.getAllWindows():
            if not w.visible:
                continue
            title_lower = w.title.lower()
            if not any(k in title_lower for k in keywords):
                continue
            if w._hWnd in seen:
                continue
            seen.add(w._hWnd)
            nums = re.findall(r"\d{5,12}", w.title)
            account = nums[0] if nums else ""
            results.append({"title": w.title, "handle": w._hWnd, "account": account})
    except Exception:
        pass
    return results


def bring_mt5_to_front(cfg: dict) -> bool:
    windows = list_mt5_windows()
    if not windows:
        _logger.error("未找到 MT5 窗口，请确认 MT5 已打开。")
        return False
    account_filter = cfg.get("MT5_ACCOUNT_FILTER", 0)
    account_str = str(account_filter) if account_filter else ""
    target = None
    if account_str:
        for w in windows:
            if w["account"] == account_str:
                target = w
                break
    if not target:
        target = windows[0]
    ok = _win32_activate(target["handle"])
    if ok:
        _logger.info(f"已激活 MT5 窗口: {target['title']}")
    return ok


# ============================================================
# 行情 & 均线 & 指标
# ============================================================
def get_ma_and_price(cfg: dict):
    """返回 (price, ma) 或 None。"""
    ma_period = cfg["MA_PERIOD"]
    symbol = cfg["SYMBOL"]
    tf = get_timeframe(cfg)

    sym_info = mt5.symbol_info(symbol)
    if sym_info is None or not sym_info.visible:
        _logger.info(f"品种 {symbol} 未在行情列表，尝试自动添加...")
        if not mt5.symbol_select(symbol, True):
            _logger.warning(f"无法添加品种 {symbol}，请在 MT5 行情列表中手动添加")
        time.sleep(0.5)

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, ma_period + 5)
    if rates is None or len(rates) < ma_period:
        _logger.warning(f"K线数据不足: {mt5.last_error()}")
        return None

    closes = [r["close"] for r in rates]
    ma = sum(closes[-ma_period:]) / ma_period
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        _logger.warning(f"获取 Tick 失败: {mt5.last_error()}")
        return None

    price = (tick.bid + tick.ask) / 2
    return price, ma


def get_rsi(cfg: dict) -> float:
    """获取最新 RSI 值。"""
    symbol = cfg.get("SYMBOL", "XAUUSDm")
    tf = get_timeframe(cfg)
    rsi_period = cfg.get("RSI_PERIOD", 14)
    return calculate_rsi(symbol, tf, rsi_period)


# ============================================================
# 开单
# ============================================================
def place_order(direction: str, cfg: dict) -> bool:
    symbol = cfg["SYMBOL"]
    lot = cfg["LOT_SIZE"]

    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        _logger.error(f"无法获取品种信息: {symbol}")
        return False
    if not sym_info.visible:
        mt5.symbol_select(symbol, True)

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        _logger.error("获取 Tick 失败。")
        return False

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "BUY" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lot),
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": 20260523,
        "comment": "auto_v2",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else "None"
        _logger.error(f"开单失败 [{direction}] retcode={code}")
        return False

    _logger.info(
        f"开单成功 [{direction}] ticket={result.order} "
        f"价格={price:.5f} 手数={lot}"
    )
    return True


# ============================================================
# 盈亏
# ============================================================
def get_total_profit(cfg: dict) -> float:
    positions = mt5.positions_get(symbol=cfg["SYMBOL"])
    if positions is None:
        return 0.0
    return sum(p.profit for p in positions)


def has_positions(cfg: dict) -> bool:
    positions = mt5.positions_get(symbol=cfg["SYMBOL"])
    return positions is not None and len(positions) > 0


# ============================================================
# 平仓
# ============================================================
CLOSE_THRESHOLD = 50


def close_all_by_api(cfg_or_symbol) -> bool:
    if isinstance(cfg_or_symbol, str):
        cfg = {"SYMBOL": cfg_or_symbol, "MT5_ACCOUNT_FILTER": 0}
    else:
        cfg = cfg_or_symbol

    symbol = cfg.get("SYMBOL", "XAUUSDm")
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        _logger.info("API 平仓：当前无持仓")
        return True

    _logger.info(f"API 平仓：共 {len(positions)} 单，逐单发送平仓...")
    success_count, fail_count = 0, 0

    for pos in positions:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            fail_count += 1
            continue
        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type, price = mt5.ORDER_TYPE_SELL, tick.bid
        else:
            close_type, price = mt5.ORDER_TYPE_BUY, tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "magic": 20260523,
            "comment": "close_all_api",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else "None"
            _logger.warning(f"API 平仓失败 ticket={pos.ticket} retcode={code}")
            fail_count += 1
        else:
            success_count += 1

    _logger.info(f"API 平仓完成：成功 {success_count} 单，失败 {fail_count} 单")
    return fail_count == 0


def close_all_by_gui(cfg_or_symbol) -> bool:
    if isinstance(cfg_or_symbol, str):
        cfg = {"SYMBOL": cfg_or_symbol, "MT5_ACCOUNT_FILTER": 0}
    else:
        cfg = cfg_or_symbol

    _logger.info("触发 GUI 全部平仓...")
    mt5_windows = list_mt5_windows()
    if not mt5_windows:
        _logger.error("未找到 MT5 窗口")
        return False

    account_filter = cfg.get("MT5_ACCOUNT_FILTER", 0)
    account_str = str(account_filter) if account_filter else ""
    target = None
    if account_str:
        for w in mt5_windows:
            if w["account"] == account_str:
                target = w
                break
    if not target:
        target = mt5_windows[0]

    prev_hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not _win32_activate(target["handle"]):
        _logger.error("Win32 激活 MT5 失败")
        return False

    try:
        win = gw.Win32Window(target["handle"])
        rx = win.left + win.width // 2
        ry = win.top + int(win.height * 0.75)
    except Exception:
        rx, ry = 400, 580

    success = False
    try:
        pyautogui.rightClick(rx, ry)
        time.sleep(0.35)
        for _ in range(4):
            pyautogui.press('down')
            time.sleep(0.05)
        time.sleep(0.1)
        pyautogui.press('right')
        time.sleep(0.25)
        pyautogui.press('enter')
        time.sleep(0.45)
        pyautogui.press('enter')
        time.sleep(0.3)
        _logger.info("平仓指令已发送")
        success = True
    except Exception as e:
        _logger.error(f"平仓键盘操作异常: {e}")
    finally:
        try:
            if prev_hwnd and prev_hwnd != target["handle"]:
                ctypes.windll.user32.SetForegroundWindow(prev_hwnd)
        except Exception:
            pass
    return success


def close_all_smart(cfg_or_symbol) -> bool:
    if isinstance(cfg_or_symbol, str):
        cfg = {"SYMBOL": cfg_or_symbol, "MT5_ACCOUNT_FILTER": 0}
    else:
        cfg = cfg_or_symbol

    symbol = cfg.get("SYMBOL", "XAUUSDm")
    positions = mt5.positions_get(symbol=symbol) or []
    count = len(positions)

    if count == 0:
        _logger.info("智能平仓：当前无持仓，无需操作")
        return True
    if count < CLOSE_THRESHOLD:
        _logger.info(f"智能平仓：持仓 {count} 单 < {CLOSE_THRESHOLD}，使用 API 平仓")
        return close_all_by_api(cfg)
    else:
        _logger.info(f"智能平仓：持仓 {count} 单 >= {CLOSE_THRESHOLD}，使用 GUI 平仓")
        return close_all_by_gui(cfg)


# ============================================================
# 主循环
# ============================================================
def run(cfg: dict):
    global _stop_flag, _mt5_connected
    _stop_flag = False
    _mt5_connected = False
    _init_stats()

    mode_str = "自动(ADX+RSI)" if cfg.get("AUTO_MODE", True) else cfg.get("STRATEGY_MODE", "shunshi")
    direction = cfg.get("ORDER_DIRECTION", "both")
    _logger.info(f"===== 交易启动 v2 | 模式={mode_str} | 方向={direction} =====")
    _logger.info(f"逆势条件：偏离>{cfg.get('BAND_WIDTH', 0.15)}% + RSI超买>={cfg.get('RSI_OVERBOUGHT', 70)}/超卖<={cfg.get('RSI_OVERSOLD', 30)}")
    _logger.info(f"顺势条件：ADX>={cfg.get('ADX_TREND_MIN', 25)} + RSI安全区 {cfg.get('RSI_TREND_OS', 25)}~{cfg.get('RSI_TREND_OB', 75)}")
    _logger.info(f"过渡区（ADX {cfg.get('ADX_RANGE_MAX', 20)}~{cfg.get('ADX_TREND_MIN', 25)}）不开单")

    if not init_mt5(cfg):
        _logger.error("MT5 初始化失败，程序退出。")
        return

    last_order_time = 0.0
    last_stats_time = 0.0

    try:
        while not _stop_flag:
            now = time.time()
            interval = int(cfg.get("ORDER_INTERVAL_SEC", 60))

            if now - last_stats_time >= 5:
                _update_stats(cfg)
                last_stats_time = now

            # 止盈止损检查
            if has_positions(cfg):
                total_profit = get_total_profit(cfg)
                tp = cfg.get("TAKE_PROFIT_USD", 30.0)
                sl = cfg.get("STOP_LOSS_USD", 5000.0)

                if total_profit >= tp:
                    _logger.info(f"止盈触发！盈利 {total_profit:.2f} >= {tp}")
                    close_all_smart(cfg)
                    time.sleep(2)
                elif total_profit <= -abs(sl):
                    _logger.info(f"止损触发！亏损 {total_profit:.2f} >= {sl}")
                    close_all_smart(cfg)
                    time.sleep(2)

            # 开单逻辑
            if now - last_order_time >= interval:
                result = get_ma_and_price(cfg)
                rsi_val = get_rsi(cfg)

                if result is not None:
                    price, ma = result
                    deviation = get_price_deviation(price, ma)

                    try:
                        direction_signal = decide(price, ma, rsi_val, cfg)
                    except Exception as e:
                        _logger.error(f"策略执行错误: {e}")
                        direction_signal = None

                    market_info = f"ADX={_cached_adx:.1f} +DI={_cached_pdi:.1f} -DI={_cached_mdi:.1f} RSI={rsi_val:.1f} 偏离={deviation:+.3f}%"
                    if direction_signal in ("BUY", "SELL"):
                        _logger.info(
                            f"开单信号 [{direction_signal}] | 价格={price:.5f} MA={ma:.5f} | {market_info}"
                        )
                        place_order(direction_signal, cfg)
                        last_order_time = now
                    else:
                        _logger.info(f"无信号 | {market_info}")
                        last_order_time = now  # 即使无信号也重置计时，等下一个周期
                else:
                    _logger.warning("行情读取失败。")

            time.sleep(1)

    except KeyboardInterrupt:
        _logger.info("用户中断。")
    finally:
        mt5.shutdown()
        _mt5_connected = False
        _logger.info("MT5 连接已关闭。")
