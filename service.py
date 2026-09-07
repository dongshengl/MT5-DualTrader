# service.py - MT5 自动交易后台服务 v3
# 独立进程运行，不依赖 GUI / 桌面会话
# GUI 通过本地 socket（localhost:19527）与本进程通信
#
# 通信协议（JSON over TCP，每条消息以 \n 结尾）：
#   GUI → Service:
#     {"cmd": "start",  "cfg": {...}}
#     {"cmd": "stop"}
#     {"cmd": "close_all"}
#     {"cmd": "get_status"}
#     {"cmd": "get_logs"}
#   Service → GUI:
#     {"ok": true/false, "msg": "...", "data": {...}}
# ============================================================
import json
import os
import sys
import socket
import threading
import time
import logging
import collections
import datetime
import traceback

# ---- 路径修正（PyInstaller frozen 模式） ----
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

# ---- 日志写入文件（服务进程无 GUI，需持久化日志） ----
LOG_PATH = os.path.join(BASE_DIR, "service.log")
_log_queue = collections.deque(maxlen=1000)

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("service")

# 文件日志
_file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
)
_logger.addHandler(_file_handler)


class _QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_queue.append(self.format(record))
        except Exception:
            pass


_qh = _QueueHandler()
_qh.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
)
_logger.addHandler(_qh)
_logger.propagate = False

# ---- 引入交易逻辑 ----
try:
    from trader_dual import (
        load_cfg, save_cfg, run as trader_run,
        request_stop, get_stats, get_logs,
        init_mt5, close_all_smart,
    )
    _import_ok = True
except Exception as e:
    _logger.error(f"导入 trader_dual 失败: {e}")
    _import_ok = False

# ============================================================
# 服务状态
# ============================================================
SERVICE_PORT = 19527
_trade_thread = None
_running = False
_current_cfg = {}
_status_lock = threading.Lock()

# ---- PID 文件（防止重复启动） ----
PID_FILE = os.path.join(BASE_DIR, "service.pid")


def _write_pid():
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _remove_pid():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def is_service_running_on_port() -> bool:
    """检查服务是否已经在监听端口（用于防重复）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        result = s.connect_ex(("127.0.0.1", SERVICE_PORT))
        s.close()
        return result == 0
    except Exception:
        return False


# ============================================================
# 交易线程控制
# ============================================================
def _start_trade(cfg: dict):
    global _trade_thread, _running, _current_cfg

    with _status_lock:
        if _running:
            return False, "交易已在运行中"

    _current_cfg = dict(cfg)
    save_cfg(cfg)

    def _run():
        global _running
        _running = True
        _logger.info("===== 后台服务：交易线程启动 =====")
        try:
            trader_run(dict(cfg))
        except Exception as e:
            _logger.error(f"交易线程异常: {e}\n{traceback.format_exc()}")
        finally:
            _running = False
            _logger.info("===== 后台服务：交易线程结束 =====")

    _trade_thread = threading.Thread(target=_run, daemon=True, name="TradeThread")
    _trade_thread.start()
    return True, "交易已启动"


def _stop_trade():
    global _running
    if not _running:
        return False, "当前未在交易"
    request_stop()
    return True, "已发送停止信号"


def _do_close_all():
    cfg = dict(_current_cfg) if _current_cfg else load_cfg()
    ok = close_all_smart(cfg)
    return ok, "平仓完成" if ok else "平仓失败（部分单可能未成交）"


def _get_status() -> dict:
    stats = get_stats()
    stats["running"] = _running
    stats["import_ok"] = _import_ok
    return stats


# ============================================================
# Socket 服务器
# ============================================================
def _handle_client(conn: socket.socket, addr):
    """处理单个 GUI 连接。"""
    buf = b""
    try:
        conn.settimeout(30)
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except Exception:
                    _send(conn, {"ok": False, "msg": "JSON 解析失败"})
                    continue

                cmd = msg.get("cmd", "")
                _logger.info(f"收到命令: {cmd} from {addr}")

                if cmd == "start":
                    cfg = msg.get("cfg", {})
                    if not cfg:
                        cfg = load_cfg()
                    ok, m = _start_trade(cfg)
                    _send(conn, {"ok": ok, "msg": m})

                elif cmd == "stop":
                    ok, m = _stop_trade()
                    _send(conn, {"ok": ok, "msg": m})

                elif cmd == "close_all":
                    # 在独立线程中执行平仓，立即返回确认
                    threading.Thread(
                        target=_do_close_all, daemon=True
                    ).start()
                    _send(conn, {"ok": True, "msg": "平仓指令已发送"})

                elif cmd == "get_status":
                    _send(conn, {"ok": True, "data": _get_status()})

                elif cmd == "get_logs":
                    logs_from_trader = get_logs(clear=True)
                    own_logs = list(_log_queue)
                    _log_queue.clear()
                    all_logs = own_logs + logs_from_trader
                    _send(conn, {"ok": True, "data": {"logs": all_logs}})

                elif cmd == "ping":
                    _send(conn, {"ok": True, "msg": "pong"})

                else:
                    _send(conn, {"ok": False, "msg": f"未知命令: {cmd}"})
    except socket.timeout:
        pass
    except Exception as e:
        _logger.warning(f"客户端 {addr} 连接异常: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _send(conn: socket.socket, data: dict):
    try:
        payload = json.dumps(data, ensure_ascii=False) + "\n"
        conn.sendall(payload.encode("utf-8"))
    except Exception as e:
        _logger.warning(f"发送响应失败: {e}")


def run_server():
    """启动 TCP 监听服务器（主线程）。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", SERVICE_PORT))
    except OSError as e:
        _logger.error(f"端口 {SERVICE_PORT} 绑定失败（是否已有实例在运行？）: {e}")
        sys.exit(1)

    server.listen(5)
    _logger.info(f"MT5 自动交易服务已启动，监听 127.0.0.1:{SERVICE_PORT}")
    _write_pid()

    try:
        while True:
            try:
                conn, addr = server.accept()
                t = threading.Thread(
                    target=_handle_client, args=(conn, addr),
                    daemon=True, name=f"Client-{addr[1]}"
                )
                t.start()
            except Exception as e:
                _logger.error(f"accept 异常: {e}")
                time.sleep(0.5)
    except KeyboardInterrupt:
        _logger.info("服务收到中断信号，正在退出...")
    finally:
        server.close()
        _remove_pid()
        try:
            request_stop()
        except Exception:
            pass
        time.sleep(0.5)
        _logger.info("服务已停止。")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    if not _import_ok:
        print("导入 trader_dual 失败，请检查文件是否完整。")
        sys.exit(1)

    # 检查是否已有实例
    if is_service_running_on_port():
        print(f"端口 {SERVICE_PORT} 已被占用，可能已有服务实例在运行。")
        sys.exit(0)

    # 开始监听
    run_server()
