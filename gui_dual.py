# gui_dual.py - MT5 自动交易 GUI v3（连接后台服务）
# 通过 socket 连接本地 service.py，断开 RDP 后服务继续运行
# ============================================================
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import socket
import time
import sys
import os
import json
import datetime
import subprocess

try:
    from trader_dual import load_cfg, save_cfg, DEFAULTS
except Exception as _e:
    print("import trader_dual failed:", _e)
    sys.exit(1)

# ============================================================
# 常量
# ============================================================
SERVICE_PORT = 19527
POLL_INTERVAL_MS = 2000    # GUI 轮询间隔（毫秒）
LOG_POLL_INTERVAL_MS = 500


# ============================================================
# Socket 客户端
# ============================================================
def _service_call(cmd: str, extra: dict = None, timeout: float = 5.0) -> dict:
    """
    向后台服务发送一条命令并等待响应。
    返回 {"ok": bool, "msg": str, "data": ...}
    失败返回 {"ok": False, "msg": "错误描述"}
    """
    payload = {"cmd": cmd}
    if extra:
        payload.update(extra)
    data_str = json.dumps(payload, ensure_ascii=False) + "\n"

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", SERVICE_PORT))
        s.sendall(data_str.encode("utf-8"))

        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        s.close()

        line = buf.split(b"\n")[0].strip()
        if not line:
            return {"ok": False, "msg": "服务响应为空"}
        return json.loads(line.decode("utf-8"))
    except ConnectionRefusedError:
        return {"ok": False, "msg": "服务未启动（连接被拒绝）"}
    except socket.timeout:
        return {"ok": False, "msg": "服务响应超时"}
    except Exception as e:
        return {"ok": False, "msg": f"通信异常: {e}"}


def _is_service_alive() -> bool:
    resp = _service_call("ping", timeout=1.5)
    return resp.get("ok", False)


def _launch_service():
    """
    以独立进程（无窗口）启动 service.py / 服务 exe。
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
        # 打包后找 service exe
        svc_exe = os.path.join(base, "MT5服务.exe")
        if os.path.exists(svc_exe):
            subprocess.Popen(
                [svc_exe],
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                close_fds=True,
            )
            return
        # 如果 service 和 gui 打包在一起（单 exe），用 --service 参数启动
        own_exe = sys.executable
        subprocess.Popen(
            [own_exe, "--service"],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
    else:
        # 开发模式：用 pythonw 启动 service.py（无控制台窗口）
        base = os.path.dirname(os.path.abspath(__file__))
        svc_script = os.path.join(base, "service.py")
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable  # fallback: 有控制台窗口
        subprocess.Popen(
            [pythonw, svc_script],
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )


# ============================================================
# 配色
# ============================================================
C = {
    "bg": "#0d1117",
    "fg": "#e6edf3",
    "accent": "#58a6ff",
    "accent2": "#f85149",
    "success": "#0d3320",
    "warn": "#d29922",
    "panel": "#161b22",
    "entry_bg": "#21262d",
    "entry_fg": "#ffffff",
    "log_bg": "#0c1016",
    "log_fg": "#8b949e",
    "stat_label": "#7d8590",
    "stat_value": "#f0f6fc",
    "btn_start": "#238636",
    "btn_stop": "#da3633",
    "btn_close": "#db6d28",
    "btn_svc": "#1f6feb",
    "btn_text": "#ffffff",
    "profit_green": "#3fb950",
    "loss_red": "#f85149",
    "border": "#30363d",
    "silent": "#6e7681",
    "online": "#3fb950",
    "offline": "#f85149",
}

_cfg = {}


class MT5App:
    def __init__(self, root):
        self.root = root
        root.title("MT5 自动交易 v3  ·  后台服务模式")
        root.geometry("960x820")
        root.resizable(True, True)
        root.configure(bg=C["bg"])
        self._build_styles()
        self._load_config()
        self._build_ui()
        self._check_auth_on_start()
        self._start_log_poller()
        self._start_status_poller()

    # --------------------------------------------------------
    # 授权（开源免费版：无需授权密码，任何人都可直接使用）
    # --------------------------------------------------------
    def _check_auth_on_start(self):
        self.pwd_hint.config(text="开源免费版：无需授权密码", fg=C["profit_green"])

    def _verify_auth(self) -> bool:
        return True

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def _load_config(self):
        global _cfg
        _cfg.clear()
        _cfg.update(load_cfg())

    def _save_config(self):
        global _cfg
        try:
            _cfg["MT5_ACCOUNT_FILTER"] = int(self.acc_entry.get() or 0)
        except ValueError:
            _cfg["MT5_ACCOUNT_FILTER"] = 0
        _cfg["SYMBOL"] = self.symbol_entry.get().strip() or "XAUUSDm"
        _cfg["TIMEFRAME"] = self.tf_var.get()
        _cfg["MA_PERIOD"] = int(self.ma_entry.get() or 20)
        _cfg["LOT_SIZE"] = float(self.lot_entry.get() or 0.01)
        _cfg["ORDER_INTERVAL_SEC"] = int(self.interval_entry.get() or 60)
        _cfg["TAKE_PROFIT_USD"] = float(self.tp_entry.get() or 30.0)
        _cfg["STOP_LOSS_USD"] = float(self.sl_entry.get() or 5000.0)
        _cfg["STRATEGY_MODE"] = self.mode_var.get()
        _cfg["ORDER_DIRECTION"] = self.dir_var.get()
        _cfg["AUTO_MODE"] = self.auto_var.get()
        try:
            _cfg["ADX_PERIOD"] = int(self.adx_period_entry.get() or 14)
        except ValueError:
            _cfg["ADX_PERIOD"] = 14
        try:
            _cfg["ADX_RANGE_MAX"] = float(self.adx_range_entry.get() or 20)
        except ValueError:
            _cfg["ADX_RANGE_MAX"] = 20
        try:
            _cfg["ADX_TREND_MIN"] = float(self.adx_trend_entry.get() or 25)
        except ValueError:
            _cfg["ADX_TREND_MIN"] = 25
        try:
            _cfg["RSI_PERIOD"] = int(self.rsi_period_entry.get() or 14)
        except ValueError:
            _cfg["RSI_PERIOD"] = 14
        try:
            _cfg["RSI_OVERBOUGHT"] = float(self.rsi_ob_entry.get() or 70)
        except ValueError:
            _cfg["RSI_OVERBOUGHT"] = 70
        try:
            _cfg["RSI_OVERSOLD"] = float(self.rsi_os_entry.get() or 30)
        except ValueError:
            _cfg["RSI_OVERSOLD"] = 30
        try:
            _cfg["RSI_TREND_OB"] = float(self.rsi_trend_ob_entry.get() or 75)
        except ValueError:
            _cfg["RSI_TREND_OB"] = 75
        try:
            _cfg["RSI_TREND_OS"] = float(self.rsi_trend_os_entry.get() or 25)
        except ValueError:
            _cfg["RSI_TREND_OS"] = 25
        try:
            _cfg["BAND_WIDTH"] = float(self.band_entry.get() or 0.15)
        except ValueError:
            _cfg["BAND_WIDTH"] = 0.15
        save_cfg(dict(_cfg))

    # --------------------------------------------------------
    # 样式
    # --------------------------------------------------------
    def _build_styles(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".", background=C["bg"], foreground=C["fg"])
        s.configure("TLabel", background=C["bg"], foreground=C["fg"])
        s.configure("TFrame", background=C["bg"])
        s.configure("TLabelframe", background=C["panel"], foreground=C["accent"])
        s.configure("TLabelframe.Label", background=C["panel"], foreground=C["accent"],
                     font=("Microsoft YaHei UI", 10, "bold"))
        s.configure("TEntry",
                     fieldbackground=C["entry_bg"], foreground=C["entry_fg"],
                     insertcolor=C["accent"])
        s.map("TEntry", fieldbackground=[("focus", "#1a3a5c")])
        s.configure("TCombobox",
                     fieldbackground=C["entry_bg"], foreground=C["entry_fg"],
                     background=C["entry_bg"])
        s.map("TCombobox",
              fieldbackground=[("readonly", C["entry_bg"])],
              foreground=[("readonly", C["entry_fg"])])

    # --------------------------------------------------------
    # UI 构建
    # --------------------------------------------------------
    def _build_ui(self):
        main = tk.Frame(self.root, bg=C["bg"])
        main.pack(fill="both", expand=True, padx=10, pady=10)

        # ---- 左栏：配置 ----
        left = tk.Frame(main, bg=C["bg"])
        left.pack(side="left", fill="y", padx=(0, 8))

        # == 基础配置 ==
        lf = tk.LabelFrame(left, text=" 基础配置 ", bg=C["panel"], fg=C["accent"],
                           font=("Microsoft YaHei UI", 10, "bold"),
                           padx=10, pady=8, relief="groove", bd=1)
        lf.pack(fill="x", pady=(0, 6))

        def mk_label(parent, text, row, col=0, fg=None):
            tk.Label(parent, text=text, bg=C["panel"], fg=fg or C["fg"],
                     font=("Microsoft YaHei UI", 9)).grid(
                row=row, column=col, sticky="w", pady=2)

        def mk_entry(parent, row, default, width=14):
            ent = ttk.Entry(parent, width=width, font=("Consolas", 10))
            ent.insert(0, default)
            ent.grid(row=row, column=1, sticky="w", pady=2, padx=(6, 0))
            return ent

        TF_OPTIONS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
        self.tf_var = tk.StringVar(value=_cfg.get("TIMEFRAME", "M5"))

        row = 0
        mk_label(lf, "MT5账号 (0=自动):", row)
        self.acc_entry = mk_entry(lf, row, str(_cfg.get("MT5_ACCOUNT_FILTER", 0) or ""))
        row += 1
        mk_label(lf, "交易品种:", row)
        self.symbol_entry = mk_entry(lf, row, _cfg.get("SYMBOL", "XAUUSDm"))
        row += 1
        mk_label(lf, "K线周期:", row)
        ttk.Combobox(lf, textvariable=self.tf_var, values=TF_OPTIONS,
                     state="readonly", width=12).grid(
            row=row, column=1, sticky="w", pady=2, padx=(6, 0))
        row += 1
        mk_label(lf, "均线周期:", row)
        self.ma_entry = mk_entry(lf, row, str(_cfg.get("MA_PERIOD", 28)))
        row += 1
        mk_label(lf, "手数:", row)
        self.lot_entry = mk_entry(lf, row, str(_cfg.get("LOT_SIZE", 0.01)))
        row += 1
        mk_label(lf, "开单间隔(秒):", row)
        self.interval_entry = mk_entry(lf, row, str(_cfg.get("ORDER_INTERVAL_SEC", 60)))
        row += 1
        mk_label(lf, "止盈 (USD):", row)
        self.tp_entry = mk_entry(lf, row, str(_cfg.get("TAKE_PROFIT_USD", 30.0)))
        row += 1
        mk_label(lf, "止损 (USD):", row)
        self.sl_entry = mk_entry(lf, row, str(_cfg.get("STOP_LOSS_USD", 5000.0)))
        row += 1
        mk_label(lf, "手动策略:", row, fg=C["accent"])
        self.mode_var = tk.StringVar(value=_cfg.get("STRATEGY_MODE", "shunshi"))
        ttk.Combobox(lf, textvariable=self.mode_var,
                     values=["shunshi", "nishi"], state="readonly",
                     width=12).grid(row=row, column=1, sticky="w", pady=2, padx=(6, 0))
        row += 1
        mk_label(lf, "下单方向:", row, fg=C["accent"])
        self.dir_var = tk.StringVar(value=_cfg.get("ORDER_DIRECTION", "both"))
        ttk.Combobox(lf, textvariable=self.dir_var,
                     values=["both", "buy_only", "sell_only"],
                     state="readonly", width=12).grid(
            row=row, column=1, sticky="w", pady=2, padx=(6, 0))
        row += 1
        self.pwd_hint = tk.Label(lf, text="开源免费版：无需授权密码",
                                  bg=C["panel"], fg=C["profit_green"],
                                  font=("Microsoft YaHei UI", 8))
        self.pwd_hint.grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        # == ADX + RSI 参数 ==
        af = tk.LabelFrame(left, text=" ADX + RSI 参数 ", bg=C["panel"], fg=C["accent"],
                           font=("Microsoft YaHei UI", 10, "bold"),
                           padx=10, pady=8, relief="groove", bd=1)
        af.pack(fill="x", pady=(0, 6))

        self.auto_var = tk.BooleanVar(value=_cfg.get("AUTO_MODE", True))
        tk.Checkbutton(
            af, text="自动模式（ADX+RSI 判断行情）",
            variable=self.auto_var,
            bg=C["panel"], fg=C["accent"],
            selectcolor=C["entry_bg"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        def mk_al(parent, text, row, col=0):
            tk.Label(parent, text=text, bg=C["panel"], fg=C["fg"],
                     font=("Microsoft YaHei UI", 9)).grid(
                row=row, column=col, sticky="w", pady=2)

        def mk_ae(parent, row, default, col=1):
            ent = ttk.Entry(parent, width=8, font=("Consolas", 10))
            ent.insert(0, default)
            ent.grid(row=row, column=col, sticky="w", pady=2, padx=(6, 0))
            return ent

        params = [
            ("ADX 周期:", "adx_period_entry", str(_cfg.get("ADX_PERIOD", 14)), 0, 1),
            ("ADX 震荡上限:", "adx_range_entry", str(_cfg.get("ADX_RANGE_MAX", 20)), 0, 1),
            ("ADX 趋势下限:", "adx_trend_entry", str(_cfg.get("ADX_TREND_MIN", 25)), 0, 1),
            ("RSI 周期:", "rsi_period_entry", str(_cfg.get("RSI_PERIOD", 14)), 0, 1),
            ("逆势偏离度%:", "band_entry", str(_cfg.get("BAND_WIDTH", 0.15)), 2, 3),
            ("逆势超买线:", "rsi_ob_entry", str(_cfg.get("RSI_OVERBOUGHT", 70)), 2, 3),
            ("逆势超卖线:", "rsi_os_entry", str(_cfg.get("RSI_OVERSOLD", 30)), 2, 3),
        ]
        row_map = {}
        r = 1
        for label, attr, default, lcol, ecol in params[:4]:
            mk_al(af, label, r, col=lcol)
            setattr(self, attr, mk_ae(af, r, default, col=ecol))
            row_map[attr] = r
            r += 1
        r = 1
        for label, attr, default, lcol, ecol in params[4:]:
            tk.Label(af, text=label, bg=C["panel"], fg=C["fg"],
                     font=("Microsoft YaHei UI", 9)).grid(
                row=r, column=lcol, sticky="w", pady=2, padx=(16, 0))
            ent = ttk.Entry(af, width=8, font=("Consolas", 10))
            ent.insert(0, default)
            ent.grid(row=r, column=ecol, sticky="w", pady=2, padx=(4, 0))
            setattr(self, attr, ent)
            r += 1

        r_last = max(4, 3) + 1
        tk.Label(af, text="顺势RSI上限:", bg=C["panel"], fg=C["fg"],
                 font=("Microsoft YaHei UI", 9)).grid(row=r_last, column=0, sticky="w", pady=2)
        self.rsi_trend_ob_entry = ttk.Entry(af, width=8, font=("Consolas", 10))
        self.rsi_trend_ob_entry.insert(0, str(_cfg.get("RSI_TREND_OB", 75)))
        self.rsi_trend_ob_entry.grid(row=r_last, column=1, sticky="w", pady=2, padx=(6, 0))
        tk.Label(af, text="顺势RSI下限:", bg=C["panel"], fg=C["fg"],
                 font=("Microsoft YaHei UI", 9)).grid(row=r_last, column=2, sticky="w", pady=2, padx=(16, 0))
        self.rsi_trend_os_entry = ttk.Entry(af, width=8, font=("Consolas", 10))
        self.rsi_trend_os_entry.insert(0, str(_cfg.get("RSI_TREND_OS", 25)))
        self.rsi_trend_os_entry.grid(row=r_last, column=3, sticky="w", pady=2, padx=(4, 0))

        tk.Label(af, text="震荡区: ADX<上限 | 过渡区: 不开单 | 趋势区: ADX≥下限",
                 bg=C["panel"], fg=C["silent"],
                 font=("Microsoft YaHei UI", 8, "italic")).grid(
            row=r_last + 1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # == 按钮 ==
        bf = tk.Frame(left, bg=C["bg"])
        bf.pack(fill="x", pady=(4, 0))

        self.svc_btn = tk.Button(
            bf, text="🚀 启动后台服务", command=self._on_launch_service,
            bg=C["btn_svc"], fg=C["btn_text"],
            font=("Microsoft YaHei UI", 9, "bold"),
            relief="flat", padx=8, pady=5,
            activebackground="#388bfd", borderwidth=0)
        self.svc_btn.pack(side="left", padx=(0, 4))

        self.start_btn = tk.Button(
            bf, text="▶  开始交易", command=self._on_start,
            bg=C["btn_start"], fg=C["btn_text"],
            font=("Microsoft YaHei UI", 10, "bold"),
            relief="flat", padx=10, pady=5,
            activebackground="#2ea043", borderwidth=0)
        self.start_btn.pack(side="left", padx=(0, 4))

        self.stop_btn = tk.Button(
            bf, text="⏹  停止", command=self._on_stop,
            bg=C["btn_stop"], fg=C["btn_text"],
            font=("Microsoft YaHei UI", 10, "bold"),
            relief="flat", padx=10, pady=5,
            activebackground="#f85149", borderwidth=0, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 4))

        self.close_btn = tk.Button(
            bf, text="✕  一键平仓", command=self._on_close_all,
            bg=C["btn_close"], fg=C["btn_text"],
            font=("Microsoft YaHei UI", 10, "bold"),
            relief="flat", padx=10, pady=5,
            activebackground="#e0883f", borderwidth=0)
        self.close_btn.pack(side="left")

        # ---- 右栏 ----
        rf = tk.Frame(main, bg=C["bg"])
        rf.pack(side="left", fill="both", expand=True)

        # 服务状态
        svc_frame = tk.LabelFrame(rf, text=" 后台服务状态 ", bg=C["panel"], fg=C["accent"],
                                   font=("Microsoft YaHei UI", 10, "bold"),
                                   padx=8, pady=4, relief="groove", bd=1)
        svc_frame.pack(fill="x", pady=(0, 5))

        self.svc_status_label = tk.Label(
            svc_frame, text="⚫ 检测中...", bg=C["panel"], fg=C["silent"],
            font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        self.svc_status_label.pack(side="left", padx=6)

        self.trade_status_label = tk.Label(
            svc_frame, text="", bg=C["panel"], fg=C["silent"],
            font=("Microsoft YaHei UI", 9), anchor="w")
        self.trade_status_label.pack(side="left", padx=12)

        # 实时统计
        sf = tk.LabelFrame(rf, text=" 实时统计 ", bg=C["panel"], fg=C["accent"],
                           font=("Microsoft YaHei UI", 10, "bold"),
                           padx=8, pady=6, relief="groove", bd=1)
        sf.pack(fill="x", pady=(0, 5))
        self.stat_labels = {}

        def add_stat_row(parent, row, items):
            for c, (label, key) in enumerate(items):
                tk.Label(parent, text=label + ":", bg=C["panel"],
                         fg=C["stat_label"],
                         font=("Microsoft YaHei UI", 9)).grid(
                    row=row, column=c * 2, sticky="w", padx=(6, 2), pady=3)
                val = tk.Label(parent, text="--", bg=C["panel"],
                               fg=C["stat_value"],
                               font=("Consolas", 10, "bold"))
                val.grid(row=row, column=c * 2 + 1, sticky="w", padx=(0, 10), pady=3)
                self.stat_labels[key] = val

        add_stat_row(sf, 0, [
            ("账号", "account_no"), ("余额", "balance"),
            ("净值", "equity"), ("更新", "last_update"),
        ])
        add_stat_row(sf, 1, [
            ("浮动盈亏", "profit"), ("总持仓", "pos_count"),
            ("多单", "buy_count"), ("空单", "sell_count"),
        ])
        add_stat_row(sf, 2, [
            ("本日盈亏", "day_profit"), ("本周盈亏", "week_profit"),
            ("本月盈亏", "month_profit"), ("本年盈亏", "year_profit"),
        ])
        add_stat_row(sf, 3, [
            ("最大浮亏", "max_float_loss"), ("行情状态", "market_type"),
        ])

        # 实时指标
        indf = tk.LabelFrame(rf, text=" 实时指标 ", bg=C["panel"], fg=C["accent"],
                             font=("Microsoft YaHei UI", 10, "bold"),
                             padx=8, pady=4, relief="groove", bd=1)
        indf.pack(fill="x", pady=(0, 5))
        self.ind_labels = {}
        for c, (label, key) in enumerate([
            ("ADX", "adx"), ("+DI", "plus_di"), ("-DI", "minus_di"),
            ("RSI", "rsi"), ("偏离%", "deviation"),
        ]):
            tk.Label(indf, text=label + ":", bg=C["panel"], fg=C["stat_label"],
                     font=("Microsoft YaHei UI", 9)).grid(
                row=0, column=c * 2, sticky="w", padx=(6, 2), pady=4)
            val = tk.Label(indf, text="--", bg=C["panel"], fg=C["stat_value"],
                           font=("Consolas", 10, "bold"))
            val.grid(row=0, column=c * 2 + 1, sticky="w", padx=(0, 10), pady=4)
            self.ind_labels[key] = val

        # 日志
        lf2 = tk.LabelFrame(rf, text=" 运行日志 ", bg=C["panel"], fg=C["accent"],
                            font=("Microsoft YaHei UI", 10, "bold"),
                            padx=6, pady=4, relief="groove", bd=1)
        lf2.pack(fill="both", expand=True)
        self.log_text = scrolledtext.ScrolledText(
            lf2, height=12, font=("Consolas", 9),
            bg=C["log_bg"], fg=C["log_fg"],
            insertbackground=C["accent"],
            selectbackground=C["accent"],
            selectforeground="#000",
            relief="flat", borderwidth=0,
        )
        self.log_text.pack(fill="both", expand=True)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪 — 请先启动后台服务")
        tk.Label(main, textvariable=self.status_var,
                 bg=C["success"], fg=C["fg"],
                 anchor="w", padx=8, pady=3,
                 font=("Microsoft YaHei UI", 9)).pack(
            side="bottom", fill="x", pady=(6, 0))

    # --------------------------------------------------------
    # 服务轮询
    # --------------------------------------------------------
    def _start_status_poller(self):
        def _poll():
            try:
                alive = _is_service_alive()
                if alive:
                    resp = _service_call("get_status", timeout=3)
                    if resp.get("ok"):
                        data = resp.get("data", {})
                        self._update_stats_from_data(data)
                        running = data.get("running", False)
                        self.svc_status_label.config(
                            text="🟢 后台服务运行中", fg=C["online"])
                        self.trade_status_label.config(
                            text="📊 交易中" if running else "⏸ 待机",
                            fg=C["profit_green"] if running else C["silent"])
                        self.start_btn.config(state="disabled" if running else "normal")
                        self.stop_btn.config(state="normal" if running else "disabled")
                    else:
                        self.svc_status_label.config(
                            text="🟡 服务异常", fg=C["warn"])
                else:
                    self.svc_status_label.config(
                        text="🔴 后台服务未启动", fg=C["offline"])
                    self.trade_status_label.config(text="", fg=C["silent"])
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
            except Exception:
                pass
            self.root.after(POLL_INTERVAL_MS, _poll)

        _poll()

    def _start_log_poller(self):
        def _poll():
            try:
                if _is_service_alive():
                    resp = _service_call("get_logs", timeout=3)
                    if resp.get("ok"):
                        logs = resp.get("data", {}).get("logs", [])
                        if logs:
                            self.log_text.config(state="normal")
                            for line in logs:
                                self.log_text.insert("end", line + "\n")
                            self.log_text.see("end")
                            self.log_text.config(state="disabled")
            except Exception:
                pass
            self.root.after(LOG_POLL_INTERVAL_MS, _poll)

        _poll()

    def _update_stats_from_data(self, data: dict):
        def fmt_usd(val):
            try:
                v = float(val)
                return f"${v:+,.2f}"
            except (ValueError, TypeError):
                return str(val)

        mapping = {
            "account_no": str(data.get("account_no", "--")),
            "balance": str(data.get("balance", "--")),
            "equity": str(data.get("equity", "--")),
            "last_update": data.get("last_update", "--"),
            "profit": fmt_usd(data.get("profit", 0)),
            "pos_count": str(data.get("pos_count", "--")),
            "buy_count": str(data.get("buy_count", "--")),
            "sell_count": str(data.get("sell_count", "--")),
            "day_profit": fmt_usd(data.get("day_profit", 0)),
            "week_profit": fmt_usd(data.get("week_profit", 0)),
            "month_profit": fmt_usd(data.get("month_profit", 0)),
            "year_profit": fmt_usd(data.get("year_profit", 0)),
            "max_float_loss": fmt_usd(data.get("max_float_loss", 0)),
        }
        for key, val in mapping.items():
            if key in self.stat_labels:
                self.stat_labels[key].config(text=val)

        mt_type = data.get("market_type", "")
        if "market_type" in self.stat_labels and mt_type:
            mt_text = {"shunshi": "📈 趋势", "nishi": "〰 震荡", "silent": "⏸ 过渡区"}.get(mt_type, mt_type)
            mt_color = {"shunshi": C["profit_green"], "nishi": C["accent"],
                        "silent": C["silent"]}.get(mt_type, C["fg"])
            self.stat_labels["market_type"].config(text=mt_text, fg=mt_color)

        for k in ("profit", "day_profit", "week_profit", "month_profit", "year_profit"):
            if k in self.stat_labels:
                try:
                    v = float(data.get(k, 0))
                    self.stat_labels[k].config(
                        fg=C["profit_green"] if v > 0 else (C["loss_red"] if v < 0 else C["stat_value"]))
                except (ValueError, TypeError):
                    pass

        if "max_float_loss" in self.stat_labels:
            try:
                v = float(data.get("max_float_loss", 0))
                self.stat_labels["max_float_loss"].config(
                    fg=C["loss_red"] if v < 0 else C["stat_value"])
            except (ValueError, TypeError):
                pass

        # 指标
        adx_v = data.get("adx", -1)
        pdi_v = data.get("plus_di", 0)
        mdi_v = data.get("minus_di", 0)
        if adx_v >= 0:
            self.ind_labels["adx"].config(text=f"{adx_v:.1f}")
            self.ind_labels["plus_di"].config(
                text=f"{pdi_v:.1f}",
                fg=C["profit_green"] if pdi_v > mdi_v else C["stat_value"])
            self.ind_labels["minus_di"].config(
                text=f"{mdi_v:.1f}",
                fg=C["loss_red"] if mdi_v > pdi_v else C["stat_value"])

    # --------------------------------------------------------
    # 按钮回调
    # --------------------------------------------------------
    def _on_launch_service(self):
        if _is_service_alive():
            messagebox.showinfo("提示", "后台服务已在运行中，无需重复启动。")
            return
        self.status_var.set("正在启动后台服务...")
        _launch_service()
        # 等待服务就绪
        for _ in range(12):
            time.sleep(0.5)
            if _is_service_alive():
                self.status_var.set("后台服务启动成功！")
                self.svc_status_label.config(text="🟢 后台服务运行中", fg=C["online"])
                self._log("后台服务已启动。关闭此 GUI 窗口后服务仍会继续运行。")
                return
        messagebox.showerror("错误", "后台服务启动超时，请检查 service.exe 是否存在或查看 service.log。")
        self.status_var.set("服务启动失败")

    def _on_start(self):
        if not self._verify_auth():
            return
        if not _is_service_alive():
            messagebox.showerror("错误", "后台服务未运行，请先点击【启动后台服务】")
            return
        self._save_config()
        resp = _service_call("start", {"cfg": dict(_cfg)})
        if resp.get("ok"):
            self.status_var.set("交易已启动（后台持续运行）")
            self._log("已发送开始交易命令。")
        else:
            messagebox.showerror("错误", resp.get("msg", "未知错误"))

    def _on_stop(self):
        if not _is_service_alive():
            return
        resp = _service_call("stop")
        if resp.get("ok"):
            self.status_var.set("已发送停止信号")
            self._log("已发送停止交易命令。")
        else:
            messagebox.showerror("错误", resp.get("msg", ""))

    def _on_close_all(self):
        if not _is_service_alive():
            messagebox.showerror("错误", "后台服务未运行，无法执行平仓。")
            return
        self.status_var.set("正在执行一键平仓...")
        self._log("已发送一键平仓命令。")
        resp = _service_call("close_all")
        if not resp.get("ok"):
            messagebox.showerror("错误", resp.get("msg", ""))

    def _log(self, text: str):
        self.log_text.config(state="normal")
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"{ts} [GUI] {text}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


# ============================================================
# 入口
# ============================================================
def main():
    # 支持 --service 参数直接启动服务（打包成单 exe 时）
    if len(sys.argv) > 1 and sys.argv[1] == "--service":
        from service import run_server
        run_server()
        return

    try:
        root = tk.Tk()
    except Exception as e:
        print("Tk init failed:", e)
        return
    app = MT5App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
