# -*- coding: utf-8 -*-
"""BTCUSDT Perpetual 실시간 가격 - 작업표시줄 오버레이 + 트레이 아이콘
데이터: Binance USDT-M 선물 REST 폴링 (~1.2s)
기능: 표시요소 on/off, 배경 투명/팔레트, 시작프로그램 자동실행, 설정 저장
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import winreg
from ctypes import wintypes

import tkinter as tk
import pystray
from PIL import Image, ImageDraw, ImageFont

# ---------- 설정 ----------
MARK_URL = ("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
            )  # Binance USDT-M perpetual mark price
PCT_URL = ("https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT"
           )  # 24h 등락률 (premiumIndex에는 없어서 별도 조회)
POLL_SEC = 1.2        # 가격 폴링 주기(초)
EDGE_GAP = 6          # 우측 점유 영역(트레이/모니터 도구) 왼쪽과의 간격 px
FONT = ("Segoe UI", 10)
POLL_MS = 150         # 화면 갱신 주기
ICON_MS = 1000        # 트레이 아이콘 갱신 주기
MAGIC = "#010203"     # 투명 배경용 컬러키 (실제 텍스트색과 겹치지 않는 색)
APP_NAME = "BtcTaskbar"
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "settings.json")

BG_PALETTE = [  # (이름, 색상) - None은 투명
    ("없음 (투명)", None),
    ("검정", "#111111"),
    ("다크그레이", "#2d2d30"),
    ("네이비", "#1f3b57"),
    ("다크그린", "#14532d"),
    ("와인", "#7f1d1d"),
    ("비트코인 오렌지", "#f7931a"),
    ("흰색", "#f5f5f5"),
]
# -------------------------

state = {"price": None, "pct": None, "visible": True, "quit": False,
         "online": False}
cfg = {"show_symbol": True, "show_price": True, "show_pct": True,
       "bg": None, "autostart": False, "offset_x": 0, "offset_y": 0}
taskbar_lum = [40.0]   # 작업표시줄 밝기 샘플 (기본: 어두움 가정)
drag = {"active": False, "x0": 0, "y0": 0, "wx": 0, "wy": 0}


def _single_instance_guard():
    """이미 실행 중이면 종료 (named mutex). 워치독 재기동/중복 실행 방지."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL,
                                    wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    h = kernel32.CreateMutexW(None, False, r"Local\BtcTaskbarSingleton")
    if h and ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    return h  # 프로세스 생존 동안 참조 유지


_MUTEX = _single_instance_guard()


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass


def save_settings():
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- Win32 ----------
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, wintypes.LPVOID]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.EnumChildWindows.argtypes = [wintypes.HWND, wintypes.LPVOID,
                                    wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetPixel.restype = wintypes.DWORD


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def taskbar_hwnd():
    return user32.FindWindowW("Shell_TrayWnd", None)


def taskbar_rect():
    hwnd = taskbar_hwnd()
    r = RECT()
    if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return r
    return None


_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def right_edge_occupied(tb, tb_r):
    """작업표시줄 오른쪽 절반의 점유 영역(트레이/모니터 도구) 중 가장 왼쪽 경계 x"""
    boundary = [10 ** 9]
    # 해상도 독립적으로 작업표시줄 오른쪽 절반만 스캔
    threshold = tb_r.left + (tb_r.right - tb_r.left) // 2

    def cb(h, lp):
        if not user32.IsWindowVisible(h):
            return True
        r = RECT()
        if user32.GetWindowRect(h, ctypes.byref(r)) and r.left > threshold:
            boundary[0] = min(boundary[0], r.left)
        return True

    user32.EnumChildWindows(tb, _ENUMPROC(cb), 0)
    return boundary[0]


def sample_taskbar_luminance():
    """작업표시줄 중앙(보통 빈 공간) 픽셀의 밝기를 읽어 텍스트 대비 결정"""
    tb = taskbar_hwnd()
    if not tb:
        return
    dc = user32.GetDC(tb)
    if not dc:
        return
    r = taskbar_rect()
    try:
        w = r.right - r.left
        total = cnt = 0
        for frac in (0.5, 0.45, 0.55):
            px = gdi32.GetPixel(dc, int(w * frac), 4)
            if px != 0xFFFFFFFF:  # CLR_NONE
                total += (0.299 * (px & 0xFF) + 0.587 * ((px >> 8) & 0xFF)
                          + 0.114 * ((px >> 16) & 0xFF))
                cnt += 1
        if cnt:
            taskbar_lum[0] = total / cnt
    finally:
        user32.ReleaseDC(tb, dc)


def hex_lum(hx):
    r, g, b = int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


# ---------- 자동 실행 ----------
def autostart_is_set():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run"
                            ) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def launch_cmd():
    """자동실행/재기동용 명령. PyInstaller exe(frozen)면 exe만, 아니면 pythonw+스크립트."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe_dir = os.path.dirname(sys.executable)
    pyw = os.path.join(exe_dir, "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return [exe, os.path.abspath(__file__)]


def autostart_apply(on):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run",
                         0, winreg.KEY_SET_VALUE)
    with key:
        if on:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ,
                              " ".join(f'"{c}"' for c in launch_cmd()))
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


# ---------- 가격 스레드 ----------
def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "btc-taskbar"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def price_loop():
    while not state["quit"]:
        try:
            state["price"] = float(_fetch(MARK_URL)["markPrice"])
            state["pct"] = float(_fetch(PCT_URL)["priceChangePercent"])
            state["online"] = True
        except Exception:
            state["online"] = False
        time.sleep(POLL_SEC)


def make_icon_image():
    pct = state["pct"] or 0.0
    bg = (22, 163, 74) if pct >= 0 else (220, 38, 38)
    if state["price"] is None:
        bg = (60, 60, 60)
    img = Image.new("RGB", (64, 64), bg)
    d = ImageDraw.Draw(img)
    p = state["price"]
    if p is None:
        text = "--"
    elif p >= 10000:
        text = f"{p / 1000:.0f}K"
    elif p >= 1000:
        text = f"{p / 1000:.1f}K"
    else:
        text = f"{p:.0f}"
    try:
        f = ImageFont.truetype("arialbd.ttf", 34)
    except OSError:
        f = ImageFont.load_default()
    d.text((32, 32), text, font=f, fill="white", anchor="mm")
    return img


# ---------- 오버레이 창 ----------
root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg=MAGIC)

lbl_sym = tk.Label(root, font=FONT, padx=0, pady=3)
lbl_price = tk.Label(root, font=FONT, padx=0, pady=3)
lbl_pct = tk.Label(root, font=FONT, padx=0, pady=3)
for wdg in (lbl_sym, lbl_price, lbl_pct):
    wdg.pack(side="left")

root.update_idletasks()
hwnd = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT


def fix_styles():
    """백그라운드 전용 창 스타일 유지: Alt+Tab 제외(TOOLWINDOW+APPWINDOW 제거),
    클릭-통과, 포커스 안 뺏음. tkinter가 deiconify 등으로 스타일을 되돌릴 수
    있어 매번 확인."""
    ex = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
    want = (ex | 0x00080000 | 0x00000080 | 0x08000000) & ~0x00040000
    # 드래그 모드에선 클릭-통과(TRANSPARENT)를 끈 상태로 유지해야 마우스가 잡힘
    want = (want & ~0x00000020) if drag["active"] else (want | 0x00000020)
    #  +WS_EX_LAYERED +WS_EX_TOOLWINDOW +WS_EX_NOACTIVATE
    #  -WS_EX_APPWINDOW (APPWINDOW가 있으면 TOOLWINDOW여도 Alt+Tab에 노출)
    if want != ex:
        user32.SetWindowLongW(hwnd, -20, want)


fix_styles()


def bg_color_now():
    return MAGIC if cfg["bg"] is None else cfg["bg"]


def text_base_color():
    lum = taskbar_lum[0] if cfg["bg"] is None else hex_lum(cfg["bg"])
    return "#111827" if lum > 140 else "#f3f4f6"


def pct_color():
    dark_bg = (taskbar_lum[0] if cfg["bg"] is None
               else hex_lum(cfg["bg"])) <= 140
    up = "#4ade80" if dark_bg else "#16a34a"
    dn = "#f87171" if dark_bg else "#dc2626"
    return up if (state["pct"] or 0) >= 0 else dn


def apply_bg():
    bg = bg_color_now()
    root.configure(bg=bg)
    for wdg in (lbl_sym, lbl_price, lbl_pct):
        wdg.configure(bg=bg)
    try:
        root.attributes("-transparentcolor", MAGIC if cfg["bg"] is None
                        else "")
    except tk.TclError:
        pass


_last_pos = 0.0
_last_icon = 0.0
_applied_visibility = True
_needs_repos = False


def auto_anchor():
    """수동 오프셋 적용 전의 기준 위치 (x, y) 또는 None"""
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    r = taskbar_rect()
    if r and (r.bottom - r.top) < 100 and (r.right - r.left) > 400:
        edge = right_edge_occupied(taskbar_hwnd(), r)
        if edge > 10 ** 8:
            edge = r.right
        return (edge - EDGE_GAP - w,
                r.top + (r.bottom - r.top - h) // 2)
    return (root.winfo_screenwidth() - w - 12,
            root.winfo_screenheight() - h - 60)


def reposition():
    """작업표시줄 위 우측 점유 영역 왼쪽 + 사용자 오프셋 적용 (화면 좌표)"""
    a = auto_anchor()
    if a is None:
        return
    root.geometry(f"+{a[0] + cfg['offset_x']}+{a[1] + cfg['offset_y']}")
    # 작업표시줄이 가끔 위로 올라오는 경우 대비 topmost 재주장
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0,
                        0x0001 | 0x0002 | 0x0010)  # TOPMOST+NOMOVE+NOSIZE+NOACTIVATE


# ---------- 드래그 위치 조정 ----------
def _win_rect(h):
    r = RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return r


def _drag_start(e):
    drag["x0"], drag["y0"] = e.x_root, e.y_root
    r = _win_rect(hwnd)
    drag["wx"], drag["wy"] = r.left, r.top


def _drag_move(e):
    root.geometry(f"+{drag['wx'] + e.x_root - drag['x0']}"
                  f"+{drag['wy'] + e.y_root - drag['y0']}")


def _drag_end(e):
    a = auto_anchor()
    if a:
        r = _win_rect(hwnd)
        cfg["offset_x"] = r.left - a[0]
        cfg["offset_y"] = r.top - a[1]
        save_settings()


def set_drag_mode(on):
    """드래그 조정 모드: 클릭-통과를 잠시 해제하고 배경을 보이게 함.
    반드시 메인(tkinter) 스레드에서 호출할 것."""
    drag["active"] = on
    if on:
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, ex & ~0x00000020)  # TRANSPARENT 해제
        root.configure(bg="#1f3b57")
        for wdg in (lbl_sym, lbl_price, lbl_pct):
            wdg.configure(bg="#1f3b57")
        # 창 전체가 label이라 bind_all 사용 (label 위 클릭도 잡힘)
        root.bind_all("<Button-1>", _drag_start, add="+")
        root.bind_all("<B1-Motion>", _drag_move, add="+")
        root.bind_all("<ButtonRelease-1>", _drag_end, add="+")
    else:
        root.unbind_all("<Button-1>")
        root.unbind_all("<B1-Motion>")
        root.unbind_all("<ButtonRelease-1>")
        apply_bg()
        fix_styles()  # TRANSPARENT 복구
        save_settings()


def watchdog():
    if state["quit"]:
        return
    if not user32.IsWindow(hwnd):
        subprocess.Popen(launch_cmd(),
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
        os._exit(0)


def tick():
    global _last_pos, _last_icon, _applied_visibility, _needs_repos
    if state["quit"]:
        root.destroy()
        return
    watchdog()
    if "drag_req" in state:
        set_drag_mode(state.pop("drag_req"))
    p, pct = state["price"], state["pct"]
    base = text_base_color()
    if p is None:
        lbl_sym.config(text="BTC " if cfg["show_symbol"] else "",
                       fg="#f7931a")
        lbl_price.config(text="---" if cfg["show_price"] else "",
                         fg="#9ca3af")
        lbl_pct.config(text="")
    else:
        lbl_sym.config(text="BTC " if cfg["show_symbol"] else "",
                       fg="#f7931a")
        lbl_price.config(text=f"{p:,.1f}" if cfg["show_price"] else "",
                         fg=base if state["online"] else "#6b7280")
        lbl_pct.config(text=(f"  {pct:+.2f}%"
                             if cfg["show_pct"] and pct is not None else ""),
                       fg=pct_color())
    if state["visible"]:
        # Win+D/바탕화면 보기로 최소화되면 복구 (overrideredirect 창은 자동 복귀 안 함)
        if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            try:
                root.deiconify()
            except tk.TclError:
                pass
        fix_styles()
        # 작업표시줄이 위로 올라오는 경우 대비 매 틱 topmost 재주장
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0,
                            0x0001 | 0x0002 | 0x0010)
        _applied_visibility = True
    elif _applied_visibility:
        _applied_visibility = False
        root.withdraw()
    now = time.time()
    if (_needs_repos or now - _last_pos > 2) and not drag["active"]:
        _needs_repos = False
        _last_pos = now
        sample_taskbar_luminance()
        if not drag["active"]:
            apply_bg()
        reposition()
    if now - _last_icon > ICON_MS / 1000:
        _last_icon = now
        try:
            tray.icon = make_icon_image()
            if p is None:
                tray.title = "BTCUSDT Perp  연결 중..."
            elif pct is None:
                tray.title = f"BTCUSDT Perp (Mark)  {p:,.1f}"
            else:
                tray.title = (f"BTCUSDT Perp (Mark)  {p:,.1f}"
                              f"  ({pct:+.2f}%)")
        except Exception:
            pass
    root.after(POLL_MS, tick)


# ---------- 트레이 아이콘 ----------
def toggle(key):
    def _f(icon, item):
        global _needs_repos
        cfg[key] = not cfg[key]
        _needs_repos = True
        save_settings()
    return _f


def set_bg(color):
    def _f(icon, item):
        global _needs_repos
        cfg["bg"] = color
        _needs_repos = True
        save_settings()
        apply_bg()
    return _f


def nudge(dx, dy):
    def _f(icon, item):
        global _needs_repos
        cfg["offset_x"] = cfg.get("offset_x", 0) + dx
        cfg["offset_y"] = cfg.get("offset_y", 0) + dy
        _needs_repos = True
        save_settings()
    return _f


def reset_pos(icon, item):
    global _needs_repos
    cfg["offset_x"] = cfg["offset_y"] = 0
    _needs_repos = True
    save_settings()


def toggle_drag(icon, item):
    # pystray 콜백은 별도 스레드 -> tkinter 조작은 메인 루프에 위임
    state["drag_req"] = not drag["active"]


def toggle_autostart(icon, item):
    cfg["autostart"] = not cfg["autostart"]
    try:
        autostart_apply(cfg["autostart"])
    except OSError:
        cfg["autostart"] = not cfg["autostart"]  # 실패 시 롤백
    save_settings()


def on_quit(icon, item):
    state["quit"] = True
    icon.stop()


def bg_checked(color):
    return lambda item: cfg["bg"] == color


load_settings()
cfg["autostart"] = autostart_is_set()
apply_bg()

tray = pystray.Icon(
    "btc_taskbar",
    make_icon_image(),
    "BTCUSDT Perp",
    menu=pystray.Menu(
        pystray.MenuItem("표시 옵션", pystray.Menu(
            pystray.MenuItem("심볼 (BTC)", toggle("show_symbol"),
                             checked=lambda i: cfg["show_symbol"]),
            pystray.MenuItem("가격", toggle("show_price"),
                             checked=lambda i: cfg["show_price"]),
            pystray.MenuItem("등락률", toggle("show_pct"),
                             checked=lambda i: cfg["show_pct"]),
        )),
        pystray.MenuItem("배경색", pystray.Menu(
            *[pystray.MenuItem(name, set_bg(c), checked=bg_checked(c),
                               radio=True) for name, c in BG_PALETTE]
        )),
        pystray.MenuItem("위치 조정", pystray.Menu(
            pystray.MenuItem("◀ 10px 왼쪽", nudge(-10, 0)),
            pystray.MenuItem("◀ 1px 왼쪽", nudge(-1, 0)),
            pystray.MenuItem("1px 오른쪽 ▶", nudge(1, 0)),
            pystray.MenuItem("10px 오른쪽 ▶", nudge(10, 0)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("▲ 1px 위로", nudge(0, -1)),
            pystray.MenuItem("▼ 1px 아래로", nudge(0, 1)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("드래그로 조정", toggle_drag,
                             checked=lambda i: drag["active"]),
            pystray.MenuItem("위치 초기화", reset_pos),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("표시/숨기기", lambda i, it: state.update(
            visible=not state["visible"]),
            checked=lambda i: state["visible"]),
        pystray.MenuItem("시작 프로그램으로 자동 실행", toggle_autostart,
                         checked=lambda i: cfg["autostart"]),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", on_quit),
    ),
)

threading.Thread(target=price_loop, daemon=True).start()
threading.Thread(target=tray.run, daemon=True).start()

reposition()
root.after(POLL_MS, tick)
root.mainloop()
