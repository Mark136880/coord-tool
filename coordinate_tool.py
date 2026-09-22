# -*- coding: utf-8 -*-
"""
坐标距离 / 方位计算悬浮工具
- 悬停在地图坐标上,按 F8 存入自点,F10 存入目标点(可反复覆盖)。
- 自动截屏识别悬停坐标(x/y),实时计算并置顶显示 距离 + 方位。
- 快捷键 / 识别区域 / 比例尺 均可通过"设置"或 config.json 修改。

依赖:pillow, pytesseract + Tesseract OCR 引擎
    (安装:pip install pillow pytesseract;Tesseract 引擎需单独安装)
全局热键使用 Windows 官方 RegisterHotKey,不安装键盘钩子、不读取/注入任何进程。
"""

import os
import sys
import math
import json
import queue
import re
import time
import threading
import ctypes
import ctypes.wintypes as wintypes

# ---- 设置 DPI 感知,保证截屏/光标/窗口坐标一致 ----
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PIL import ImageGrab, Image, ImageDraw, ImageEnhance, ImageOps, ImageTk
import pytesseract

if getattr(sys, "frozen", False):
    # 打包成 exe 后,__file__ 指向临时解压目录;配置必须放在 exe 同目录才能持久保存
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 配置优先放 exe/脚本同目录(便携);那目录写不进去(只读/权限)就退回 %APPDATA%,
# 保证校准参数不会每次重启都丢。
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
CONFIG_FALLBACK = os.path.join(_APPDATA, "CoordTool", "config.json")

# L81 迫击炮瞄具刻度尺的实测值(从 6 张不同档位截图逐个 OCR 得到,覆盖 80~684 米)
LADDER_TABLE = [80, 110, 132, 187, 240, 290, 340, 385, 430, 470,
                510, 545, 578, 609, 637, 661, 684]

DEFAULT_CONFIG = {
    "tesseract_path": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    "hotkey_self": "f8",
    "hotkey_target": "f10",
    "hotkey_calibrate": "f9",
    "hotkey_toggle": "f7",         # 显示/隐藏悬浮窗(隐藏后只能靠它叫回来)
    "scale_m_per_unit": 100.0,     # 1 坐标单位 = 多少米
    "region": {"box_left": 110, "box_top": 90, "box_right": 110, "box_bottom": 80},
    "auto_admin": False,           # 默认不提权(热键改用 RegisterHotKey,已不需要管理员)
    "debug_preview": False,        # 为 True 时点击"校准"会弹出预览小窗
    "overlay": {"alpha": 0.92},
    # 屏幕上用于对齐瞄具刻度的参考线 + 幽灵刻度尺
    # tick_px / step_m / step_slope 来自实测:刻度线间距恒为 136px;
    # L81 迫击炮的弹道是曲线,相邻刻度的距离差随距离变小(约 490m 处 40m,635m 处 26m)
    "reticle_line": {"enabled": False, "y": -1, "color": "#00FF66",
                     "ladder": True, "tick_px": 136, "x_offset": -370, "label_gap": 70,
                     "step_m": 35.0, "step_slope": -9.7,
                     "res_height": 0},   # 刻度基准:0=自动(按当前屏高度,不开缩放) / 1440 / 1600 / 1080 …
    # 语音播报:调 Windows 自带 SAPI,零依赖、离线。发音在后台线程,不影响计算/UI。
    "voice": {"enabled": True,           # 总开关
              "style": "soldier",        # 风格:soldier=中文士兵战场口令,empty=原样读
              "result": True,            # 播最终结果(距离/方位)
              "coords": False,           # 播自点/目标坐标
              "error": True,             # 识别失败时播报
              "rate": 0,                 # SAPI 语速(-10~10),默认 0 正常
              "repeat": 1,               # 每条播报重复次数(1=不重复)
              "gap": 0,                  # 重复之间的间隔(秒)
              "volume": 100,             # 音量(0~100),默认 100 最大
              "voice": ""},              # 发音人名(空=系统默认;可选女/男声)
    # 设置窗口显示方式:stack=纵向堆叠(一次看完全部), tabs=面包页(顶部分段,窗口更小)
    "settings": {"style": "stack"},
}


def load_config():
    for p in (CONFIG_PATH, CONFIG_FALLBACK):      # 先找 exe 同目录,再找 %APPDATA%
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                continue
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            merged["region"] = {**DEFAULT_CONFIG["region"], **(cfg.get("region") or {})}
            merged["reticle_line"] = {**DEFAULT_CONFIG["reticle_line"],
                                      **(cfg.get("reticle_line") or {})}
            merged["voice"] = {**DEFAULT_CONFIG["voice"], **(cfg.get("voice") or {})}
            merged["settings"] = {**DEFAULT_CONFIG["settings"], **(cfg.get("settings") or {})}
            return merged
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """保存配置。先写 exe 同目录;失败则写 %APPDATA%,保证不丢。"""
    data = json.dumps(cfg, ensure_ascii=False, indent=2)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(data)
        return CONFIG_PATH
    except Exception:
        pass
    os.makedirs(os.path.dirname(CONFIG_FALLBACK), exist_ok=True)
    with open(CONFIG_FALLBACK, "w", encoding="utf-8") as f:
        f.write(data)
    return CONFIG_FALLBACK


# ============================================================
# 坐标识别(截屏 + OCR)
# ============================================================
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor_pos():
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def capture_region(cx, cy, region, save_preview_to=None):
    """截取鼠标周围区域。region: {"box_left","box_top","box_right","box_bottom"}"""
    box = (
        cx - region["box_left"],
        cy - region["box_top"],
        cx + region["box_right"],
        cy + region["box_bottom"],
    )
    img = ImageGrab.grab(bbox=box)
    if save_preview_to:
        img.save(save_preview_to)
    return img


def parse_coords(text):
    """从 OCR 文本里解析 x / y。返回 (x, y),缺任一项则返回 (None, None)。"""
    text = text.replace(" ", "")
    pat = re.compile(r"([xyXY])\s*(\d+)[.,](\d+)")
    vals = {}
    for m in pat.finditer(text):
        axis = m.group(1).lower()
        if axis not in vals:
            vals[axis] = float("%s.%s" % (m.group(2), m.group(3)))
    x = vals.get("x")
    y = vals.get("y")
    if x is None or y is None:
        return None, None
    return x, y


def ocr_coordinate(img):
    """对截图做 OCR,返回 (x, y) 或 (None, None)。

    坐标文字一般是"白色+深色描边",而背景可能是明暗不一的地图/提示框。
    所以按顺序试几种预处理,哪个能读出完整的 x 和 y 就采用哪个:
      1) 二值化 —— 只留亮像素,能把背景和旁边的干扰文字一起滤掉(最常用、最有效)
      2) 自动对比度 —— 适合深色文字/浅色背景
      3) 对比度增强 —— 兜底
    """
    w, h = img.size
    big = img.resize((w * 3, h * 3), Image.LANCZOS).convert("L")
    cfg = "--psm 11 -c tessedit_char_whitelist=0123456789.xyXY"
    variants = (
        big.point(lambda p: 255 if p > 175 else 0),
        ImageOps.autocontrast(big),
        ImageEnhance.Contrast(big).enhance(2.0),
    )
    for v in variants:
        try:
            x, y = parse_coords(pytesseract.image_to_string(v, config=cfg))
        except Exception:
            continue
        if x is not None and y is not None:
            return x, y
    return None, None


# ============================================================
# 读瞄具刻度尺:把屏幕上那条刻度尺的数字读出来
# ============================================================
# 抓屏幕上刻度尺时,只取屏幕中心上下这个范围(少处理一半像素)
LADDER_BAND = 620


def res_scale(cfg):
    """按屏幕高度对刻度做分辨率适配。

    刻度参数(tick_px/ x_offset / label_gap)是在 2560×1440 上实测的,属于 2K 带鱼屏基准。
    换到别的分辨率时,游戏 UI 会整体随屏幕高度缩放,所以这些像素值按 高度÷基准高度 等比缩放。
    res_height: 0=自动(以当前屏为基准,缩放=1,不开缩放);其它(1440/1600/1080…)则按其作基准换算。
    """
    rh = (cfg.get("reticle_line") or {}).get("res_height", 0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    if not rh:
        return 1.0
    return sh / float(rh)


def grab_ladder_strip(cfg):
    """抓一条含刻度数字列的竖条,给后面慢慢识别用。返回 (图像, 图像顶部的屏幕y)。"""
    lac = cfg.get("reticle_line") or {}
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    sc = res_scale(cfg)
    xc = sw // 2 + int((lac.get("x_offset", -370) or 0) * sc)
    # 刻度数字在刻度线左侧约 45~90 像素处,窗口必须恰好框住这一小列数字。
    # 窗口太窄会切掉最左一位(470M→70M);太宽会把左边大片深色结构带进来,导致行检测失效。
    x0, x1 = max(0, xc - 80), min(sw, xc + 15)
    cy = sh // 2
    y0, y1 = max(0, cy - LADDER_BAND), min(sh, cy + LADDER_BAND)
    try:
        return ImageGrab.grab(bbox=(x0, y0, x1, y1)).convert("L"), y0
    except Exception:
        return None, 0


def parse_ladder_strip(img, y0=0, max_rows=4):
    """从竖条里识别刻度数字,返回 [(屏幕y, 值), ...] 按 y 升序。

    只对最靠近屏幕中心的若干行做 OCR —— 每调一次 tesseract 要 100ms 以上,
    全行都识别会让识别耗时到两秒级;而内置刻度表已经覆盖 80~684 米,
    读数只是用来印证/兼容换武器,远处那几行没必要认。
    """
    if img is None:
        return []
    w, h = img.size
    if w < 10 or h < 50:
        return []
    px = img.load()

    # 每行最暗像素 -> 找出数字所在的行段
    mins = [min(px[x, y] for x in range(w)) for y in range(h)]
    med = sorted(mins)[len(mins) // 2]
    thr = med - 25
    runs, run = [], []
    for y, m in enumerate(mins):
        if m <= thr:
            run.append(y)
        else:
            if len(run) >= 5:
                runs.append((run[0] + run[-1]) // 2)
            run = []
    if len(run) >= 5:
        runs.append((run[0] + run[-1]) // 2)

    # 靠近中心的优先(中心那几格才是要用的),最多识别 max_rows 行
    mid = h // 2
    runs.sort(key=lambda c: abs(c - mid))
    cand = sorted(runs[:max_rows])

    out = []
    for cy in cand:
        crop = img.crop((0, max(0, cy - 13), w, min(h, cy + 13)))
        big = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
        try:
            t = pytesseract.image_to_string(
                ImageOps.invert(big),
                config="--psm 7 -c tessedit_char_whitelist=0123456789M")
        except Exception:
            continue
        t = t.strip().upper().replace(" ", "").replace(".", "")
        m = re.search(r"(\d{2,5})", t)      # 窗口里可能混有 RNG 等字母,取其中的数字
        if m:
            out.append((cy + y0, int(m.group(1))))
    out.sort()
    # 校验:刻度越往下数值越小,且相邻两格差距不应离谱;不合规的丢掉
    clean = []
    for y, v in out:
        if clean:
            py, pv = clean[-1]
            if v >= pv:                 # 应严格递减
                continue
            if not (3 <= pv - v <= 120):  # 差距离谱 -> 当作识别错误
                continue
        clean.append((y, v))
    # 只认到 1 个数说明基本是误读(屏幕上没有刻度尺时也会凑出一个数),
    # 这时宁可当作没读到 —— 光靠内置刻度表也能正常画
    return clean if len(clean) >= 2 else []


def read_ladder_labels(cfg):
    """同步读一次刻度尺(主要给调试用;程序里走后台线程那条路)。"""
    img, y0 = grab_ladder_strip(cfg)
    return parse_ladder_strip(img, y0)


def read_coordinate(cfg, save_preview_to=None):
    """读取当前鼠标悬停坐标。返回 (x, y, ok)。"""
    cx, cy = get_cursor_pos()
    if save_preview_to:
        img = capture_region(cx, cy, cfg["region"], save_preview_to)
    else:
        img = capture_region(cx, cy, cfg["region"])
    x, y = ocr_coordinate(img)
    return x, y, (x is not None and y is not None)


# ============================================================
# 计算
# ============================================================
def calc(p1, p2, scale):
    """p1=(x1,y1) 自点, p2=(x2,y2) 目标。返回 (d_unit, d_meter, bearing_deg)。"""
    x1, y1 = p1
    x2, y2 = p2
    de = x2 - x1   # ΔE 东西
    dn = y2 - y1   # ΔN 南北
    d_unit = math.sqrt(de * de + dn * dn)
    d_meter = scale * d_unit
    bearing = math.degrees(math.atan2(de, dn))
    if bearing < 0:
        bearing += 360
    return d_unit, d_meter, bearing


# ============================================================
# 语音播报:调 Windows 自带语音(零 Python 依赖、离线、打包体积不变)
# ------------------------------------------------------------
# speak() 只往队列塞一条消息,真正发音在后台线程做(SAPI/System.Speech),
# 绝不阻塞 UI 线程,也不增加坐标/距离计算耗时(计算是同步的,语音是异步的)。
# ============================================================
class VoiceAnnouncer:
    def __init__(self, text_fn=None, voice_name="", rate=0, repeat=1, gap=0.0, volume=100):
        """text_fn(text) -> 最终播报词(可加风格前缀)。不给则原样播。
        voice_name: 指定发音人(System.Speech 名称,空=系统默认);rate: 语速(-10~10)。
        repeat: 每条消息重复播报次数(>=1);gap: 重复之间的间隔秒;volume: 音量(0~100)。"""
        self.q = queue.Queue()
        self._text_fn = text_fn or (lambda t: t)
        self._voice_name = voice_name or ""
        self._rate = rate
        self._repeat = max(1, int(repeat))
        self._gap = max(0.0, float(gap))
        self._volume = max(0, min(100, int(volume)))
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        """后台线程:队列里逐条取出,按重复次数与间隔用系统语音发声。不进 UI 线程,不影响计算。"""
        while True:
            text = self.q.get()
            if text is None:
                break
            for _ in range(self._repeat):
                try:
                    self._say(text)
                except Exception:
                    pass
                if self._gap > 0 and self._repeat > 1:
                    time.sleep(self._gap)

    def _say(self, text):
        if not text:
            return
        import subprocess
        # 通过环境变量传文本,避开引号/中文转义问题;用 System.Speech 离线发声。
        # 只占一个后台 daemon 线程,计算/UI 完全不受影响。
        sel = ""
        if self._voice_name:
            # 语音名含引号/空格,用 SelectVoice 前先按名称匹配,失败自动回落默认
            sel = ("try { $s.SelectVoice('%s') } catch {} ;" % self._voice_name.replace("'", "''"))
        rate = "try { $s.Rate = %d } catch {} ;" % int(self._rate)
        vol = "try { $s.Volume = %d } catch {} ;" % int(self._volume)
        ps = ("Add-Type -AssemblyName System.Speech;"
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              + sel + rate + vol +
              "$s.Speak($env:COORDVOICE)")
        enc = os.environ.copy()
        enc["COORDVOICE"] = self._text_fn(text)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       env=enc, creationflags=0x08000000,  # CREATE_NO_WINDOW
                       timeout=20, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def speak(self, text):
        """非阻塞:只入队。返回立即,计算/UI 不受影响。"""
        try:
            self.q.put_nowait(text)
        except Exception:
            pass


def list_install_voices():
    """枚举系统已安装的语音,返回 [(发音人名, 性别 女/男/未知), ...]。
    离线、单次调用;失败时返回空列表(仅影响发音人下拉,不影响其它功能)。"""
    import subprocess
    ps = ("Add-Type -AssemblyName System.Speech;"
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "$s.GetInstalledVoices() | ForEach-Object { $a=$_.VoiceInfo; "
          "Write-Output ($a.Name + '|' + $a.Gender) }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, creationflags=0x08000000, timeout=15)
    except Exception:
        return []
    # 中文系统控制台默认 GBK,直接 text=True 会 UnicodeDecodeError,这里先解码再容错
    try:
        raw = r.stdout.decode("utf-8")
    except Exception:
        try:
            raw = r.stdout.decode("gbk")
        except Exception:
            raw = r.stdout.decode("utf-8", "ignore")
    res = []
    for line in raw.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        nm, g = line.rsplit("|", 1)
        nm = nm.strip()
        if not nm:
            continue
        if "emale" in g:
            gender = "女"
        elif "ale" in g:
            gender = "男"
        else:
            gender = "未知"
        res.append((nm, gender))
    return res


def title_voice(text):
    """中文士兵战场风格:用军事术语精简整句,去除单位罗嗦,醒目简洁。"""
    if not text:
        return text
    s = text.strip()
    # 常见句式的口水词去掉,换成战场口令口吻
    s = s.replace("米，方位", "米,方位")
    s = s.replace(" 米", "米").replace(" 度", "度")
    return s


# ============================================================
# 全局快捷键:用 Windows 官方 RegisterHotKey
# ------------------------------------------------------------
# 由操作系统主动通知本程序,不安装任何键盘钩子(SetWindowsHookEx),
# 也不注入、不读取其它进程,最大限度降低被反作弊误判的可能。
# ============================================================
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_APP_RELOAD = 0x8000 + 1

# 虚拟键码 <-> 名称
_VK_TO_NAME = {}
for _i in range(1, 25):
    _VK_TO_NAME[0x70 + _i - 1] = "f%d" % _i
for _c in "0123456789":
    _VK_TO_NAME[ord(_c)] = _c
for _c in "abcdefghijklmnopqrstuvwxyz":
    _VK_TO_NAME[ord(_c.upper())] = _c
_VK_TO_NAME.update({
    0x20: "space", 0x09: "tab", 0x0D: "enter", 0x1B: "esc", 0x08: "backspace",
    0x2E: "delete", 0x2D: "insert", 0x24: "home", 0x23: "end",
    0x21: "pageup", 0x22: "pagedown", 0x26: "up", 0x28: "down", 0x25: "left", 0x27: "right",
    0x60: "numpad0", 0x61: "numpad1", 0x62: "numpad2", 0x63: "numpad3", 0x64: "numpad4",
    0x65: "numpad5", 0x66: "numpad6", 0x67: "numpad7", 0x68: "numpad8", 0x69: "numpad9",
    0x6A: "numpad*", 0x6B: "numpad+", 0x6D: "numpad-", 0x6E: "numpad.", 0x6F: "numpad/",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
})
_NAME_TO_VK = {v: k for k, v in _VK_TO_NAME.items()}
_NAME_TO_VK.update({"escape": 0x1B, "return": 0x0D, "del": 0x2E, "ins": 0x2D,
                    "pgup": 0x21, "pgdn": 0x22, "plus": 0xBB, "minus": 0xBD})

_MOD_ALIASES = (("ctrl", MOD_CONTROL), ("control", MOD_CONTROL),
                ("alt", MOD_ALT), ("shift", MOD_SHIFT),
                ("win", MOD_WIN), ("windows", MOD_WIN), ("super", MOD_WIN), ("meta", MOD_WIN))

_MODE_ORDER = ("ctrl", "alt", "shift", "win")


def vk_to_name(vk):
    return _VK_TO_NAME.get(vk) or "vk:0x%02X" % vk


def parse_hotkey(spec):
    """把 'ctrl+f9' 解析为 (modifiers, vk, 错误原因)。"""
    if not spec:
        return None, None, "为空"
    mods, vk = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if not part:
            continue
        alias = next((m for n, m in _MOD_ALIASES if part == n), None)
        if alias is not None:
            mods |= alias
            continue
        if vk is not None:
            return None, None, "只能有一个主键"
        if part.startswith("vk:"):
            try:
                vk = int(part[3:], 16)
            except ValueError:
                return None, None, "vk: 写法不正确"
        elif part in _NAME_TO_VK:
            vk = _NAME_TO_VK[part]
        else:
            return None, None, "不认识的按键 %s" % part
    if vk is None:
        return None, None, "缺少主键(如 f8)"
    return mods, vk, None


class HotkeyManager:
    """在独立线程里注册并接收 WM_HOTKEY。全程只用系统提供的热键接口。"""

    def __init__(self, on_hotkey):
        self.on_hotkey = on_hotkey
        self._specs = []
        self._id_to_slot = {}
        self._thread_id = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._errors = []
        self._result_ready = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(3)

    def update(self, specs):
        """specs: [(slot, spec), ...]。返回 (是否全部成功, 错误列表)。"""
        with self._lock:
            self._specs = list(specs)
            self._errors = ["快捷键服务未就绪"]
        self._result_ready.clear()
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_APP_RELOAD, 0, 0)
            self._result_ready.wait(3)
        with self._lock:
            errors = list(self._errors)
        return (len(errors) == 0), errors

    def _run(self):
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        # 触发一次消息队列创建,保证 PostThreadMessage 不会丢
        peek = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(peek), None, 0, 0, 0)
        self._ready.set()
        self._apply()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                slot = self._id_to_slot.get(int(msg.wParam))
                if slot:
                    try:
                        self.on_hotkey(slot)
                    except Exception:
                        pass
            elif msg.message == WM_APP_RELOAD:
                self._apply()
        for hid in list(self._id_to_slot):
            user32.UnregisterHotKey(None, hid)
        self._id_to_slot.clear()

    def _apply(self):
        user32 = ctypes.windll.user32
        for hid in list(self._id_to_slot):
            user32.UnregisterHotKey(None, hid)
        self._id_to_slot = {}
        with self._lock:
            specs = list(self._specs)
        errors = []
        for idx, (slot, spec) in enumerate(specs, start=1):
            label = {"self": "自点", "target": "目标", "calibrate": "校准",
                     "toggle": "显隐"}.get(slot, str(slot))
            mods, vk, err = parse_hotkey(spec)
            if err:
                errors.append("%s键 [%s] %s" % (label, spec or "空", err))
                continue
            if not user32.RegisterHotKey(None, idx, mods | MOD_NOREPEAT, vk):
                errors.append("%s键 [%s] 注册失败(可能已被其它程序占用)" % (label, spec))
                continue
            self._id_to_slot[idx] = slot
        with self._lock:
            self._errors = errors
        self._result_ready.set()


# ============================================================
# 悬浮窗口
# ============================================================
import tkinter as tk
from tkinter import ttk, messagebox


# ============================================================
# 屏幕参考线(用于对齐瞄具刻度)
# ------------------------------------------------------------
# 一条横贯屏幕的透明内容窗,只在屏幕上画一条线 + 距离值。
# 窗口做了"点击穿透",不会挡住游戏里的鼠标操作。
# ============================================================
class ReferenceLine:
    TRANSPARENT = "#010203"

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.win = None
        self.canvas = None
        self.value = None          # 计算出的发射距离(米),None 表示还没算出来
        self.above = None          # 刻度尺上它上面那格的值(读屏得到)
        self.below = None          # 刻度尺上它下面那格的值(读屏得到)
        self.visible = False

    def _line_cfg(self):
        c = self.cfg.get("reticle_line") or {}
        sc = res_scale(self.cfg)          # 分辨率适配缩放系数(args按屏幕高度等比缩放)
        return {
            "y": int(c.get("y", -1)),              # -1 = 屏幕中心
            "color": c.get("color", "#00FF66"),
            "ladder": bool(c.get("ladder", True)),  # 是否画出幽灵刻度尺
            "tick_px": max(20, int(round((c.get("tick_px", 136) or 136) * sc))),  # 相邻刻度线间距
            "x_offset": int(round((c.get("x_offset", -370) or -370) * sc)),  # 刻度线位置
            "label_gap": int(round((c.get("label_gap", 70) or 70) * sc)),  # 数字左让距离
            "step_m": float(c.get("step_m", 35.0)),    # 550 米处相邻刻度的距离差
            "step_slope": float(c.get("step_slope", -9.7)),  # 每远离 550 米 100 米,该差值变化多少米
            # L81 迫击炮刻度尺的实测刻度值表(80~684 米),读不到屏幕时用它算上下格
            "table": c.get("table", LADDER_TABLE),
        }

    def _step_at(self, v, lc):
        """某个距离处,相邻两条刻度线之间相差多少米(弹道非线性,越远差越小)。"""
        return max(5.0, lc["step_m"] + lc["step_slope"] * (v - 550.0) / 100.0)

    def _resolve_y(self):
        y = self._line_cfg()["y"]
        if y < 0:
            y = ctypes.windll.user32.GetSystemMetrics(1) // 2
        return y

    def _create(self):
        w = tk.Toplevel(self.root)
        self.win = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=self.TRANSPARENT)
        try:
            w.attributes("-transparentcolor", self.TRANSPARENT)
        except Exception:
            pass
        self.canvas = tk.Canvas(w, bg=self.TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._geo = None            # 窗口是新造的,下次重画时要重新设几何
        self._make_click_through(w)

    def _make_click_through(self, win):
        """让参考线窗口鼠标穿透,避免挡住游戏操作。

        注意:只给窗口自己的句柄加 WS_EX_TRANSPARENT。
        绝不能加 WS_EX_LAYERED —— Tk 的 -transparentcolor 已经设过了,
        重复设置(尤其是加到父级包装窗口上)会让窗口整个变透明而看不见。
        """
        try:
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            u = ctypes.windll.user32
            u.GetWindowLongW.restype = ctypes.c_long
            u.SetWindowLongW.restype = ctypes.c_long
            hwnd = win.winfo_id()
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def _redraw(self):
        if self.win is None:
            return
        lc = self._line_cfg()
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        line_y = self._resolve_y()

        # 只画目标距离上下各一条刻度,窗口不用太高
        n = 1 if lc["ladder"] and self.value is not None else 0
        need = 2 * n * max(20, lc["tick_px"]) + 120
        h = min(sh, max(120, need))
        top = max(0, min(sh - h, line_y - h // 2))
        self._top = top

        # 只有尺寸真变了才设 geometry —— 反复设置同样的几何会让窗口重绘,
        # 而重绘时透明色键还没生效,就会闪一下黑条
        geo = "%dx%d+0+%d" % (sw, h, top)
        if geo != getattr(self, "_geo", None):
            self.win.geometry(geo)
            self._geo = geo
        c = self.canvas
        c.delete("all")
        c.configure(width=sw, height=h)

        cx = sw // 2
        cy = line_y - top               # 目标距离那条刻度的位置
        col = lc["color"]

        # ---- 幽灵刻度尺:只画目标距离 + 上下最近的两条,样式模仿原刻度尺 ----
        if lc["ladder"]:
            self._draw_ladder(c, cx, cy, col, lc, h)

    def _tick_values(self, d, lc):
        """算出要画的三格:中间显示值、上格值、下格值,以及相对准星的像素偏移。

        刻度值 = 屏幕上读到的实时刻度 + 内置实测刻度表 的并集,
        所以不论目标距离在不在当前画面里,都能给出准确的上下两格。

        返回 (中间值, 上格值, 下格值, 上偏移, 下偏移)。
        """
        tick_px = max(20, lc["tick_px"])
        step = self._step_at(d, lc)
        vals = set(v for _, v in self.ladder)
        vals.update(lc.get("table") or [])
        vals = sorted(vals)

        if not vals:
            return d, d + step, max(1.0, d - step), tick_px, tick_px
        # 表两端的实际格差,用于超出表范围时的外推(比曲线模型更贴合实测)
        lo_step = vals[1] - vals[0] if len(vals) >= 2 else step
        hi_step = vals[-1] - vals[-2] if len(vals) >= 2 else step

        def neighbors_of(v):
            i = vals.index(v)
            up = vals[i + 1] if i + 1 < len(vals) else v + hi_step
            dn = vals[i - 1] if i - 1 >= 0 else max(1.0, v - lo_step)
            return up, dn

        # 目标离最近那格有多远?按"紧挨着该格的那一段实际格差"换算成像素。
        # 这里必须用局部格差,不能用跨两格的平均值 —— 否则会把间距算歪。
        near = min(vals, key=lambda v: abs(v - d))
        i = vals.index(near)
        if near <= d:
            up_v = vals[i + 1] if i + 1 < len(vals) else near + hi_step
            px = (d - near) / max(1.0, up_v - near) * tick_px
        else:
            dn_v = vals[i - 1] if i - 1 >= 0 else near - lo_step
            px = (near - d) / max(1.0, near - dn_v) * tick_px
        # 几乎贴在某一格上 -> 直接用那一格,三条线间距都等于整格
        # (截断成 16px 会把间距撑大几像素,就是 470/510 间距对不上的原因)
        if px < 16.0:
            up_s, dn_s = neighbors_of(near)
            return near, up_s, dn_s, tick_px, tick_px

        above = [v for v in vals if v > d]
        below = [v for v in vals if v < d]
        if above and below:                     # 目标夹在中间 -> 按比例摆
            hi, lo = above[0], below[-1]
            frac = (d - lo) / max(1.0, hi - lo)   # 目标位于 lo→hi 之间的比例
            # 到上格(hi)的距离是 (1-frac) 格,到下格(lo)的距离是 frac 格
            return d, hi, lo, (1.0 - frac) * tick_px, frac * tick_px
        if above:                               # 比表里最近一格还近
            hi = above[0]
            return d, hi, max(1.0, hi - lo_step), tick_px, tick_px
        lo = below[-1]                          # 比表里最远一格还远
        return d, lo + hi_step, lo, tick_px, tick_px

    def _draw_ladder(self, c, cx, cy, col, lc, h):
        """画出目标距离那格 + 上下最近的刻度。

        中间那条 = 目标距离所在的位置(它必须落在准星中心);
        上下两条 = 刻度尺上离它最近的两个真实刻度值,按实际比例摆位。
        """
        if self.value is None:
            return
        x = cx + lc["x_offset"]          # 和游戏刻度线同一位置
        tick_len = 30
        gap = lc["label_gap"]
        mid, hi, lo, up_off, dn_off = self._tick_values(self.value, lc)

        self._draw_tick(c, x, cy, tick_len, mid, col, h, gap, highlight=True)
        self._draw_tick(c, x, cy - up_off, tick_len, hi, col, h, gap)
        self._draw_tick(c, x, cy + dn_off, tick_len, lo, col, h, gap)

    def _draw_tick(self, c, x, y, tick_len, value, col, h, gap, highlight=False):
        if y < 12 or y > h - 12:          # 超出窗口就不画
            return
        c.create_line(x, y, x + tick_len, y, fill=col,
                      width=3 if highlight else 2)
        # 数字放在刻度线左侧更远一点,避开游戏自己的数字
        c.create_text(x - gap, y, text="%dM" % round(value), fill=col, anchor="e",
                      font=("Microsoft YaHei UI", 11, "bold" if highlight else "normal"))

    def set_target(self, meters, ladder=None):
        """设置要滚动到的发射距离(米)。ladder = 从屏幕上读到的刻度尺 [(y, 值), ...]。"""
        self.value = meters
        self.ladder = ladder or []
        if self.visible:
            self._redraw()

    def clear_target(self):
        self.value = None
        self.ladder = []
        if self.visible:
            self._redraw()

    def show(self):
        if self.win is None:
            self._create()
        self.visible = True
        self._redraw()
        try:
            self.win.deiconify()
            self.win.attributes("-topmost", True)
        except Exception:
            pass

    def hide(self):
        self.visible = False
        if self.win is not None:
            try:
                self.win.withdraw()
            except Exception:
                pass

    def refresh_geometry(self):
        """参考线的 Y / 长度改了之后重画。"""
        if self.visible:
            self._redraw()


class OverlayApp:
    def __init__(self, cfg):
        self.cfg = cfg
        self.self_pos = None      # (x, y)
        self.target_pos = None    # (x, y)
        self.wake_queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title("坐标距离计算")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", cfg["overlay"].get("alpha", 0.92))

        self._build_ui()
        self.root.after(60, self._drain_queue)

        # 校准用的缓存截图(按下校准键时抓取,之后调偏移值都基于它重新识别,不需要再动鼠标)
        self._calib_img = None
        self._calib_cursor = None
        self._calib_win = None
        self.ladder = []            # 从屏幕上读到的刻度尺 [(y, 值), ...]

        # 全局快捷键:RegisterHotKey(无键盘钩子),回调统一转给 Tk 主线程
        self.hotkeys = HotkeyManager(lambda slot: self.wake_queue.put(("hotkey", slot)))
        self.hotkeys.start()
        self.register_hotkeys()

        # 屏幕参考线(用于对齐瞄具刻度)
        self.ref_line = ReferenceLine(self.root, cfg)
        if (cfg.get("reticle_line") or {}).get("enabled"):
            self.ref_line.show()

        # 语音播报:后台线程发音,不阻塞计算/UI。只在总开关开启时才建队列。
        self._last_spoken = ()      # 去重:同一 (距离,方位) 不重复播
        self._init_voice()

        self._on_close = False

    # ---------- UI ----------
    def _build_ui(self):
        bg = "#171a1f"
        fg = "#e8eaed"
        border = "#2a2f38"
        button_bg = "#23272e"
        button_fg = "#c7ccd4"
        button_active = "#31363e"
        accent_green = "#34d399"

        self.frame = tk.Frame(self.root, bg=bg, bd=1, relief="solid",
                              highlightbackground=border, highlightthickness=1)
        self.frame.pack(fill="both", expand=True)

        # 可变内容区(普通/超简共用一层,切换超简时整体销毁重建,避免 pack 顺序错乱)
        self._content = tk.Frame(self.frame, bg=bg)
        self._content.pack(fill="both", expand=True)
        self.simple_mode = False
        self.v_simple = tk.BooleanVar(value=False)
        self._build_content(self.simple_mode)

        # 按钮(常驻,超简模式勾选放在这行;置顶已移除)
        btns = tk.Frame(self.frame, bg=bg)
        btns.pack(fill="x", padx=6, pady=(4, 6))
        self._btns_frame = btns
        tk.Checkbutton(btns, text="简化", variable=self.v_simple, command=self.toggle_simple,
                       bg=bg, fg="#bec4cc", activebackground=bg,
                       activeforeground=accent_green, selectcolor=bg,
                       font=("Microsoft YaHei UI", 9)).pack(side="left", padx=4)
        self._mk_btn(btns, "设置", self.open_settings).pack(side="left", padx=1)
        self._mk_btn(btns, "校准", self.calibrate).pack(side="left", padx=1)
        self._mk_btn(btns, "参考线", self.toggle_ref_line).pack(side="left", padx=1)
        self._mk_btn(btns, "隐藏", self.toggle_hide).pack(side="left", padx=1)
        self._mk_btn(btns, "退出", self.quit_app).pack(side="right", padx=1)

        # 初始位置:右上角
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"+{sw - 360}+120")

        # 拖拽:对除按钮外的所有控件都绑定,支持在窗口任意处拖动
        self._bind_drag_with_children(self.frame)

    def _bind_drag_with_children(self, widget):
        def skip(w):
            return isinstance(w, tk.Button)
        for child in widget.winfo_children():
            if not skip(child):
                child.bind("<Button-1>", self._start_move)
                child.bind("<B1-Motion>", self._on_move)
                self._bind_drag_with_children(child)

    def _mk_btn(self, parent, txt, cmd):
        return tk.Button(parent, text=txt, command=cmd, bg="#23272e", fg="#c7ccd4",
                         activebackground="#31363e", activeforeground="#fff",
                         relief="flat", font=("Microsoft YaHei UI", 9), bd=0, padx=8, pady=1,
                         cursor="hand2")

    def _start_move(self, e):
        self._drag = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _on_move(self, e):
        try:
            x, y = self._drag
            self.root.geometry(f"+{e.x_root - x}+{e.y_root - y}")
        except Exception:
            pass

    # ---------- 超简模式 ----------
    def _build_content(self, simple):
        """重建内容区(普通/超简)。always 创建全部 label 引用(超简时隐藏的不 pack,避免 refresh 崩)。"""
        bg = "#171a1f"
        fg = "#e8eaed"
        sub = "#8a939e"
        divider = "#262b33"
        badge_bg = "#23272e"
        green = "#34d399"
        blue = "#60a5fa"
        orange = "#f59e0b"
        title_green = "#dcfce7"
        mono_small = ("Consolas", 9)
        mono_big = ("Consolas", 14, "bold")
        for c in self._content.winfo_children():
            c.destroy()
        if simple:
            # KPI 单行:距离+方位并排靠左,间距约26px,去掉中间竖线;其余行隐藏
            r = tk.Frame(self._content, bg=bg)
            r.pack(fill="x", padx=10, pady=(8, 4))
            self.lbl_dist = tk.Label(r, text="距离: --", bg=bg, fg=green, anchor="w",
                                     font=mono_big)
            self.lbl_dist.pack(side="left")
            self.lbl_bear = tk.Label(r, text="方位: --", bg=bg, fg=blue, anchor="w",
                                     font=mono_big)
            self.lbl_bear.pack(side="left", padx=(26, 0))
            # 隐藏的行不参与布局,但对象仍创建(不 pack),让 refresh/_log 直接 config 也不会崩
            self._row_title = None
            self.lbl_hotkey = None
            self._row_pts = None
            self.lbl_self = tk.Label(self._content, text="自点", bg=bg, fg=fg)
            self.lbl_target = tk.Label(self._content, text="目标", bg=bg, fg=fg)
            self.lbl_status = tk.Label(self._content, text="就绪", bg=bg, fg=sub)
        else:
            # ---------- 标题行:标题 + 右侧快捷键 ----------
            self._row_title = tk.Frame(self._content, bg=bg)
            self._row_title.pack(fill="x", padx=10, pady=(6, 4))
            tk.Label(self._row_title, text="坐标距离 / 方位", bg=bg, fg=title_green,
                     font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(side="left")
            self.lbl_hotkey = tk.Label(self._row_title, text="", bg=bg, fg=sub,
                                       font=mono_small, anchor="e")
            self.lbl_hotkey.pack(side="right")

            # ---------- KPI 单行:左距离 右方位,中间细竖线分隔 ----------
            self._row_kpi = tk.Frame(self._content, bg=bg)
            self._row_kpi.pack(fill="x", padx=10, pady=(2, 2))
            self._row_kpi.columnconfigure(0, weight=1)
            self._row_kpi.columnconfigure(2, weight=0)
            tk.Label(self._row_kpi, text="距离 DIST", bg=bg, fg=green,
                     font=("Microsoft YaHei UI", 8)).grid(row=0, column=0, sticky="w")
            self.lbl_dist = tk.Label(self._row_kpi, text="--", bg=bg, fg=green, anchor="w",
                                     font=mono_big)
            self.lbl_dist.grid(row=1, column=0, sticky="w")
            tk.Label(self._row_kpi, text="方位 BEAR", bg=bg, fg=blue,
                     font=("Microsoft YaHei UI", 8)).grid(row=0, column=2, sticky="w")
            self.lbl_bear = tk.Label(self._row_kpi, text="--", bg=bg, fg=blue, anchor="w",
                                     font=mono_big)
            self.lbl_bear.grid(row=1, column=2, sticky="w")
            tk.Frame(self._row_kpi, bg=divider, width=1, height=48).grid(
                row=0, column=1, rowspan=2, padx=14, sticky="ns")

            # ---------- 自点 / 目标(等宽小字,同排) ----------
            self._row_pts = tk.Frame(self._content, bg=bg)
            self._row_pts.pack(fill="x", padx=10, pady=(2, 2))
            # 两列(自点/目标)均分整行,保证相邻两段间距一致
            self._row_pts.columnconfigure(0, weight=1)
            self._row_pts.columnconfigure(1, weight=1)
            self.lbl_self = tk.Label(self._row_pts, text="自点 --", bg=bg, fg=fg,
                                     font=mono_small, anchor="center")
            self.lbl_self.grid(row=0, column=0, sticky="w", ipadx=0)
            self.lbl_target = tk.Label(self._row_pts, text="目标 --", bg=bg, fg=fg,
                                       font=mono_small, anchor="center")
            self.lbl_target.grid(row=0, column=1)

            # ---------- 状态行(仅状态文字) ----------
            self._row_status = tk.Frame(self._content, bg=bg)
            self._row_status.pack(fill="x", padx=10, pady=(2, 2))
            self.lbl_status = tk.Label(self._row_status, text="就绪", bg=bg, fg=sub, anchor="w",
                                       justify="left")
            self.lbl_status.pack(fill="x")

    def toggle_simple(self):
        """超简模式:只显示 距离/方位(并排一行);隐藏标题等其余行;刻度尺不受影响。"""
        self.simple_mode = self.v_simple.get()
        self._build_content(self.simple_mode)
        self._bind_drag_with_children(self._content)
        self.root.update_idletasks()
        if self.simple_mode:
            self._log("超简模式:仅显示距离/方位(同排);刻度尺不受影响")
        else:
            self._log("已退出超简模式")
        self.refresh()

    # ---------- 快捷键 ----------
    def hotkey_specs(self):
        return [("self", self.cfg["hotkey_self"]),
                ("target", self.cfg["hotkey_target"]),
                ("calibrate", self.cfg.get("hotkey_calibrate", "f9")),
                ("toggle", self.cfg.get("hotkey_toggle", "f7"))]

    def register_hotkeys(self):
        """重新注册全局热键。返回 (是否全部成功, 错误说明列表)"""
        ok, errors = self.hotkeys.update(self.hotkey_specs())
        hk = ", ".join("%s=%s" % (n, self.cfg.get(k, d).upper()) for n, k, d in
                       (("自点", "hotkey_self", "f8"), ("目标", "hotkey_target", "f10"),
                        ("校准", "hotkey_calibrate", "f9"), ("显隐", "hotkey_toggle", "f7")))
        # 把当前生效的快捷键显示在悬浮窗标题处,便于确认修改是否生效
        try:
            self.lbl_hotkey.config(text=hk.replace(", ", " "))
        except Exception:
            pass
        self._log("热键注册异常: " + ";".join(errors) if errors else hk)
        # 显隐键是隐藏后唯一的恢复入口,注册不上必须提醒,否则一隐藏就找不回来
        if any("显隐" in e for e in errors):
            self._toast("警告:显隐快捷键 %s 注册失败(可能被其它程序占用)\n"
                        "请不要点\"隐藏\",或先在设置里换一个键"
                        % self.cfg.get("hotkey_toggle", "f7").upper(), 9000)
        return ok, errors

    # ---------- 录制新快捷键 ----------
    # 不用键盘钩子:录制期间设置窗获得焦点,直接由 Tk 读取按键即可
    _MOD_KEYSYMS = {"Control_L": "ctrl", "Control_R": "ctrl",
                    "Shift_L": "shift", "Shift_R": "shift",
                    "Alt_L": "alt", "Alt_R": "alt",
                    "Win_L": "win", "Win_R": "win", "Super_L": "win", "Super_R": "win"}

    def start_record(self, win, var, btn):
        """进入录制状态:临时注销热键,等用户按键组合。"""
        if getattr(self, "_rec", None) and self._rec.get("active"):
            self.stop_record()
            return
        self.hotkeys.update([])          # 录制时先注销,避免按下旧热键触发动作
        self._rec = {"active": True, "mods": set(), "key": None, "var": var, "btn": btn,
                     "win": win, "text": btn.cget("text")}
        btn.config(text="按键盘…(点此取消)")
        self._log("请按下快捷键组合,例如 F9 或 Ctrl+F9")
        win.focus_force()
        win.bind("<KeyPress>", self._rec_key_down)
        win.bind("<KeyRelease>", self._rec_key_up)

    def stop_record(self):
        rec = getattr(self, "_rec", None)
        if not rec or not rec.get("active"):
            return
        rec["active"] = False
        try:
            rec["win"].unbind("<KeyPress>")
            rec["win"].unbind("<KeyRelease>")
        except Exception:
            pass
        try:
            rec["btn"].config(text=rec.get("text") or "录制")
        except Exception:
            pass
        self._rec = None
        self.register_hotkeys()          # 恢复用当前配置的热键

    def _rec_key_down(self, e):
        rec = getattr(self, "_rec", None)
        if not rec or not rec.get("active"):
            return
        name = self._MOD_KEYSYMS.get(e.keysym)
        if name:
            rec["mods"].add(name)
        else:
            rec["key"] = vk_to_name(e.keycode)
            rec["mods_at_press"] = set(rec["mods"])
        return "break"

    def _rec_key_up(self, e):
        rec = getattr(self, "_rec", None)
        if not rec or not rec.get("active"):
            return
        name = self._MOD_KEYSYMS.get(e.keysym)
        if name:
            rec["mods"].discard(name)
            return "break"
        if rec.get("key"):
            mods = rec.get("mods_at_press", set())
            parts = [m for m in _MODE_ORDER if m in mods] + [rec["key"]]
            spec = "+".join(parts)
            var = rec["var"]
            self.stop_record()
            var.set(spec)
            self._log("已录制: " + spec + "(点保存后生效)")
        return "break"

    # ---------- 主循环 ----------
    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.wake_queue.get_nowait()
                if kind == "hotkey":
                    self._on_hotkey(payload)
                elif kind == "ladder":
                    # 后台读到的刻度尺回来了,补进显示
                    self.ladder = payload or []
                    self.refresh()
                    if self.ladder:
                        self._log("刻度尺读到: " + " ".join("%dM" % v for _, v in self.ladder))
                elif kind == "calibrate_result":
                    cx, cy = payload
                    self._log(f"校验: 该区域抓到 x={cx}, y={cy}")
        except queue.Empty:
            pass
        if not self._on_close:
            self.root.after(60, self._drain_queue)

    def _on_hotkey(self, slot):
        if slot == "calibrate":
            # 此刻鼠标正悬停在游戏里的坐标文字上,抓图后才好校准
            self.open_calibration()
            return
        if slot == "toggle":
            # 显示/隐藏悬浮窗。这是隐藏后唯一能把它叫回来的入口
            self.toggle_panel()
            return
        self._log("读取坐标中……")
        preview = os.path.join(APP_DIR, "_preview.png") if self.cfg["debug_preview"] else None
        x, y, ok = read_coordinate(self.cfg, save_preview_to=preview)
        if not ok:
            self._log("识别失败")
            if self.announcer is not None and self.cfg["voice"].get("error"):
                self.announcer.speak("识别失败,请重试")
            return
        self._log(f"识别到 x={x} y={y}")
        if slot == "self":
            self.self_pos = (x, y)
            if self.announcer is not None and self.cfg["voice"].get("coords"):
                self.announcer.speak("自点,东%.2f北%.2f" % (x, y))
        else:
            self.target_pos = (x, y)
        # 距离/方位和刻度尺线立刻画出来(用的是内置实测刻度表,不用等识别),
        # 刻度尺读数丢到后台线程慢慢做,读完再补上 —— 这样按键是瞬间响应的
        self.refresh()
        self.start_ladder_read()

    def start_ladder_read(self):
        """后台读一次屏幕上的刻度尺。

        不需要把自己的覆盖层藏起来:刻度数字在 x848~897,而我的数字画在 799~840、
        刻度线画在 910~940,两侧都让开了,不会压住游戏数字。
        (以前先隐藏再显示会闪一下黑条 —— 窗口重新映射时透明色键还没生效。)
        """
        try:
            img, y0 = grab_ladder_strip(self.cfg)
        except Exception:
            return
        if img is None:
            return
        threading.Thread(target=self._ladder_worker, args=(img, y0), daemon=True).start()

    def _ladder_worker(self, img, y0):
        try:
            labels = parse_ladder_strip(img, y0)
        except Exception:
            labels = []
        self.wake_queue.put(("ladder", labels))

    def read_ladder_now(self):
        """同步读一次(调试/一次性用),返回 [(y, 值), ...]。"""
        try:
            self.ladder = read_ladder_labels(self.cfg)
        except Exception:
            self.ladder = []
        return self.ladder

    def refresh(self):
        s = self.self_pos
        t = self.target_pos
        self.lbl_self.config(text=("自点: E%.2f N%.2f" % s) if s else "自点: --")
        self.lbl_target.config(text=("目标: E%.2f N%.2f" % t) if t else "目标: --")
        if s and t:
            d_unit, d_meter, bearing = calc(s, t, self.cfg["scale_m_per_unit"])
            self.lbl_bear.config(text="方位: %.1f°" % bearing)

            eff = d_meter                       # 平面距离

            self.lbl_dist.config(text="发射距离: %.0f 米" % eff)
            # 刻度尺上画出目标发射距离那格 + 上下最近两格(用实测刻度值)
            self.ref_line.set_target(eff, self.ladder)
            mid, hi, lo, _, _ = self.ref_line._tick_values(eff, self.ref_line._line_cfg())
            if mid == round(eff):
                self._log("目标 %.0f 米 -> 用 %dM 那格对准准星中心" % (eff, mid))
            else:
                self._log("目标 %.0f 米 -> 在 %dM 和 %dM 之间,按绿线位置对准准星中心(约 %dM)"
                          % (eff, lo, hi, mid))

            # 语音播报最终结果(非阻塞、同值去重):"距离X米方位X度"
            if self.announcer is not None and (self.cfg["voice"].get("result")):
                key = (round(eff), round(bearing * 10))
                if key != self._last_spoken:
                    self._last_spoken = key
                    self.announcer.speak("距离%d米,方位%.1f度" % (round(eff), bearing))
        else:
            self.lbl_dist.config(text="距离: --")
            self.lbl_bear.config(text="方位: --")
            self.ref_line.clear_target()

    def _log(self, msg):
        try:
            self.lbl_status.config(text=msg)
        except Exception:
            pass          # 窗口可能已销毁(定时器里调用时)

    # ---------- 按钮功能 ----------
    def toggle_topmost(self):
        cur = self.root.attributes("-topmost")
        self.root.attributes("-topmost", not cur)
        self._log("置顶 -> ON" if not cur else "置顶 -> OFF")

    def toggle_hide(self):
        """显示/隐藏悬浮窗。隐藏后靠全局热键(默认 F12)叫回来。"""
        self.toggle_panel()

    def toggle_panel(self):
        key = self.cfg.get("hotkey_toggle", "f7").upper()
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self._log("已显示 —— 按 %s 可再次隐藏" % key)
        else:
            self.root.withdraw()
            # 窗口是没边框的,隐藏后不会有任务栏图标,所以必须提示恢复键
            self._toast("悬浮窗已隐藏\n按 %s 可重新显示并关闭程序" % key)

    def _toast(self, text, ms=5000):
        """在屏幕角落弹一条会自动消失的提示(隐藏窗口后唯一的反馈)。"""
        try:
            t = tk.Toplevel(self.root)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            t.configure(bg="#202124")
            tk.Label(t, text=text, bg="#202124", fg="#8ab4f8", justify="left",
                     font=("Microsoft YaHei UI", 11, "bold"), padx=16, pady=10).pack()
            t.update_idletasks()
            sw = t.winfo_screenwidth()
            sh = t.winfo_screenheight()
            t.geometry("+%d+%d" % (sw - t.winfo_reqwidth() - 30,
                                   sh - t.winfo_reqheight() - 80))
            t.after(ms, t.destroy)
        except Exception:
            pass

    def _init_voice(self):
        """按当前配置创建/停用语音播报器(开关从设置保存后调用)。"""
        vc = self.cfg.get("voice") or {}
        self.announcer = None           # 先停旧的(旧线程是 daemon,自然回收)
        if vc.get("enabled"):
            fn = None   # 目前只保留系统女声原样读(去掉了士兵口令风格)
            self.announcer = VoiceAnnouncer(text_fn=fn,
                                            voice_name=vc.get("voice", "") or "",
                                            rate=vc.get("rate", 0) or 0,
                                            repeat=vc.get("repeat", 1) or 1,
                                            gap=vc.get("gap", 0) or 0,
                                            volume=vc.get("volume", 100) or 100)

    def toggle_ref_line(self):
        rl = self.cfg.setdefault("reticle_line", {})
        rl["enabled"] = not rl.get("enabled", False)
        if rl["enabled"]:
            self.ref_line.show()
            self.refresh()
            self._log("参考线已开启 —— 把瞄具刻度滚到线上")
        else:
            self.ref_line.hide()
            self._log("参考线已关闭")
        try:
            save_config(self.cfg)
        except Exception:
            pass

    def quit_app(self):
        self._on_close = True
        try:
            self.hotkeys.update([])      # 注销热键
        except Exception:
            pass
        self.root.destroy()

    # ---------- 校准 ----------
    def calibrate(self):
        """点按钮校准:给 3 秒把鼠标移回游戏坐标处,再抓图。"""
        self._log("3 秒后抓图,请把鼠标移到游戏里的坐标文字上……")
        self.root.after(1000, lambda: self._log("2 秒……"))
        self.root.after(2000, lambda: self._log("1 秒……"))
        self.root.after(3000, self.open_calibration)

    def open_calibration(self):
        """抓取鼠标周围较大一块画面并打开校准窗。之后调偏移都基于这张图重识别,无需再动鼠标。"""
        cx, cy = get_cursor_pos()
        reg = self.cfg["region"]
        margin = 110
        hw = max(reg["box_left"], reg["box_right"]) + margin
        hh = max(reg["box_top"], reg["box_bottom"]) + margin
        try:
            self._calib_img = ImageGrab.grab(bbox=(cx - hw, cy - hh, cx + hw, cy + hh))
        except Exception as e:
            self._log("抓图失败: %s" % e)
            return
        self._calib_cursor = (self._calib_img.width // 2, self._calib_img.height // 2)
        self._show_calibration_window()

    def _show_calibration_window(self):
        if self._calib_img is None:
            self._log("请先在游戏里把准星放到坐标文字上,再按校准键")
            return
        if self._calib_win is not None and self._calib_win.winfo_exists():
            self._calib_win.lift()
            self._calib_win.focus_force()
            return

        bg, fg = "#171a1f", "#e8eaed"
        win = tk.Toplevel(self.root)
        self._calib_win = win
        win.title("校准 - 让红框套住坐标文字")
        win.attributes("-topmost", True)
        win.configure(bg=bg)

        self._calib_canvas = tk.Canvas(win, bg="#111", highlightthickness=0)
        self._calib_canvas.pack(padx=10, pady=(10, 4))

        self._calib_result = tk.Label(win, text="", bg=bg, fg=fg, font=("Microsoft YaHei UI", 10, "bold"))
        self._calib_result.pack(pady=2)

        tk.Label(win, text="红框=识别区域,绿十字=鼠标位置;改下面数值红框会移动并立即重新识别",
                 bg=bg, fg="#9aa0a6", font=("Microsoft YaHei UI", 8)).pack()

        f = tk.Frame(win, bg=bg)
        f.pack(pady=6)
        self._calib_vars = {}
        for i, (label, key) in enumerate((("左", "box_left"), ("上", "box_top"),
                                          ("右", "box_right"), ("下", "box_bottom"))):
            tk.Label(f, text=label, bg=bg, fg=fg, font=("Microsoft YaHei UI", 9)).grid(row=0, column=i * 3, padx=(6, 0))
            var = tk.StringVar(value=str(self.cfg["region"][key]))
            self._calib_vars[key] = var
            tk.Entry(f, textvariable=var, width=4, bg="#1c2026", fg=fg, insertbackground="#fff",
                     relief="flat", justify="center").grid(row=1, column=i * 3, padx=(6, 0))
            tk.Button(f, text="+", width=2, bg="#31363e", fg="#cbd0d6", relief="flat",
                      command=lambda k=key: self._calib_nudge(k, 10)).grid(row=1, column=i * 3 + 1)
            tk.Button(f, text="-", width=2, bg="#31363e", fg="#cbd0d6", relief="flat",
                      command=lambda k=key: self._calib_nudge(k, -10)).grid(row=1, column=i * 3 + 2)

        btns = tk.Frame(win, bg=bg)
        btns.pack(pady=(2, 10))
        tk.Button(btns, text="保存区域", command=self._calib_save, bg="#34d399", fg="#07170f",
                  relief="flat", font=("Microsoft YaHei UI", 10, "bold"), padx=14).pack(side="left", padx=4)
        tk.Button(btns, text="重新抓图", command=self._calib_recapture, bg="#31363e", fg="#cbd0d6",
                  relief="flat", font=("Microsoft YaHei UI", 10), padx=14).pack(side="left", padx=4)
        tk.Button(btns, text="关闭", command=win.destroy, bg="#31363e", fg="#cbd0d6",
                  relief="flat", font=("Microsoft YaHei UI", 10), padx=14).pack(side="left", padx=4)

        # 数值一变就重画红框并重新识别
        self._calib_ready = False
        for var in self._calib_vars.values():
            var.trace_add("write", lambda *a: self._calib_redraw())
        self._calib_ready = True

        self._calib_redraw()
        self.root.after(120, lambda: self._safe_focus(win))

    def _safe_focus(self, win):
        """窗口可能已经被关掉,延迟聚焦前先确认它还活着。"""
        try:
            if win.winfo_exists():
                win.focus_force()
        except Exception:
            pass

    def _calib_nudge(self, key, delta):
        var = self._calib_vars[key]
        try:
            cur = int(var.get())
        except ValueError:
            cur = self.cfg["region"][key]
        var.set(str(max(0, cur + delta)))

    def _calib_recapture(self):
        self._log("3 秒后重新抓图,请把鼠标移到坐标文字上……")
        self.root.after(3000, self.open_calibration)

    def _calib_read_region(self):
        reg = {}
        try:
            for key in ("box_left", "box_top", "box_right", "box_bottom"):
                reg[key] = max(0, int(self._calib_vars[key].get()))
        except (ValueError, KeyError):
            return None
        return reg

    def _calib_redraw(self):
        if getattr(self, "_calib_ready", False) is not True:
            return
        if self._calib_win is None or not self._calib_win.winfo_exists():
            return
        img = self._calib_img
        reg = self._calib_read_region()
        if img is None or reg is None:
            return
        cx, cy = self._calib_cursor
        l, t = cx - reg["box_left"], cy - reg["box_top"]
        r, b = cx + reg["box_right"], cy + reg["box_bottom"]

        # 只把框内的部分送去识别
        x = y = None
        cl, ct = max(0, l), max(0, t)
        cr, cb = min(img.width, r), min(img.height, b)
        if cr - cl > 3 and cb - ct > 3:
            x, y = ocr_coordinate(img.crop((cl, ct, cr, cb)))

        scale = min(1.0, 470.0 / img.width, 430.0 / img.height)
        disp = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                          Image.LANCZOS).convert("RGB")
        d = ImageDraw.Draw(disp)
        d.rectangle([l * scale, t * scale, r * scale, b * scale], outline=(255, 70, 70), width=2)
        dcx, dcy = cx * scale, cy * scale
        d.line([dcx - 8, dcy, dcx + 8, dcy], fill=(0, 255, 120), width=1)
        d.line([dcx, dcy - 8, dcx, dcy + 8], fill=(0, 255, 120), width=1)

        photo = ImageTk.PhotoImage(disp)
        self._calib_canvas.config(width=disp.width, height=disp.height)
        self._calib_canvas.delete("all")
        self._calib_canvas.create_image(0, 0, anchor="nw", image=photo)
        self._calib_canvas.image = photo

        if x is not None and y is not None:
            self._calib_result.config(text="识别成功:  x=%.2f   y=%.2f" % (x, y), fg="#7ee787")
        else:
            self._calib_result.config(text="识别失败:红框里没找到坐标文字 → 调整数值让红框套住文字", fg="#ff7b72")

    def _calib_save(self):
        reg = self._calib_read_region()
        if reg is None:
            self._log("偏移值必须填整数")
            return
        self.cfg["region"] = reg
        save_config(self.cfg)
        self._log("校准已保存: " + " ".join("%s=%d" % (k, v) for k, v in reg.items()))
        if self._calib_win is not None and self._calib_win.winfo_exists():
            self._calib_win.destroy()

    # ---------- 设置 ----------
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.attributes("-topmost", True)
        win.configure(bg="#171a1f")
        win.protocol("WM_DELETE_WINDOW", lambda: (self.stop_record(), win.destroy()))
        pad = dict(padx=10, pady=4)

        # 显示方式:纵向堆叠(一次看完全部) / 面包页(顶部分段,窗口更小)
        set_style = self.cfg.setdefault("settings", {}).get("style", "stack")

        def set_style_save(st):
            self.cfg["settings"]["style"] = st
            try: save_config(self.cfg)
            except Exception: pass
            self.stop_record()
            win.destroy()
            self.open_settings()          # 重建窗口以生效

        # 顶部:显示方式切换
        topctl = tk.Frame(win, bg="#171a1f")
        topctl.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(topctl, text="显示方式", bg="#171a1f", fg="#e8eaed",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        for lab, st in (("纵向堆叠", "stack"), ("面包页(小窗口)", "tabs")):
            tk.Button(topctl, text=lab, command=lambda s=st: set_style_save(s),
                      bg="#2e6655" if set_style == st else "#31363e",
                      fg="#dcfce7" if set_style == st else "#cbd0d6",
                      relief="flat", font=("Microsoft YaHei UI", 9),
                      padx=10).pack(side="left", padx=4)

        # 内容区(body) + 面包页分段容器
        body = tk.Frame(win, bg="#171a1f")
        pages = []                                   # [(标题, Frame)]
        tabbar = tk.Frame(win, bg="#15181d")         # 面包页顶部按钮条

        def seg(title):
            """每个分组的容器;面包页模式下是一个页签。"""
            f = tk.Frame(body, bg="#171a1f")
            pages.append((title, f))
            return f

        def apply_layout(mode):
            # 先重建面包页按钮条
            for w in tabbar.winfo_children():
                w.destroy()
            if mode == "tabs":
                for title, f in pages:
                    tk.Button(tabbar, text=title, relief="flat",
                              bg="#31363e", fg="#cbd0d6", activebackground="#2e6655",
                              activeforeground="#dcfce7",
                              font=("Microsoft YaHei UI", 9), padx=8, pady=1,
                              command=lambda ff=f: show_page(ff)).pack(side="left")
                tabbar.pack(fill="x", padx=10, pady=(6, 2))
                # 默认显示第一页
                if pages:
                    show_page(pages[0][1])
                body.pack(fill="both", expand=True)
            else:
                for _, f in pages:
                    f.pack(fill="x", **pad)
                body.pack(fill="both", expand=True)
            win.update_idletasks()
            win.geometry("")

        def show_page(cur):
            for _, f in pages:
                f.pack_forget()
            cur.pack(fill="x", **pad)
            win.geometry("")

        def row(parent, label, var, hint):
            f = tk.Frame(parent, bg="#171a1f")
            f.pack(fill="x", **pad)
            tk.Label(f, text=label, bg="#171a1f", fg="#e8eaed", width=14, anchor="w",
                     font=("Microsoft YaHei UI", 10)).pack(side="left")
            tk.Entry(f, textvariable=var, bg="#1c2026", fg="#e8eaed", insertbackground="#fff",
                     relief="flat", width=22).pack(side="left")
            return f

        def hotkey_row(parent, label, var):
            f = tk.Frame(parent, bg="#171a1f")
            f.pack(fill="x", **pad)
            tk.Label(f, text=label, bg="#171a1f", fg="#e8eaed", width=14, anchor="w",
                     font=("Microsoft YaHei UI", 10)).pack(side="left")
            tk.Entry(f, textvariable=var, bg="#1c2026", fg="#e8eaed", insertbackground="#fff",
                     relief="flat", width=16).pack(side="left")
            btn = tk.Button(f, text="录制", bg="#31363e", fg="#cbd0d6", relief="flat",
                            font=("Microsoft YaHei UI", 9), padx=8)
            btn.config(command=lambda: self.start_record(win, var, btn))
            btn.pack(side="left", padx=4)
            return f

        v_self = tk.StringVar(value=self.cfg["hotkey_self"])
        v_target = tk.StringVar(value=self.cfg["hotkey_target"])
        v_calib = tk.StringVar(value=self.cfg.get("hotkey_calibrate", "f9"))
        v_toggle = tk.StringVar(value=self.cfg.get("hotkey_toggle", "f7"))
        v_scale = tk.StringVar(value=str(self.cfg["scale_m_per_unit"]))
        v_left = tk.StringVar(value=str(self.cfg["region"]["box_left"]))
        v_top = tk.StringVar(value=str(self.cfg["region"]["box_top"]))
        v_right = tk.StringVar(value=str(self.cfg["region"]["box_right"]))
        v_bottom = tk.StringVar(value=str(self.cfg["region"]["box_bottom"]))
        v_debug = tk.BooleanVar(value=self.cfg["debug_preview"])

        # ---- 第1组: 快捷键与比例尺 ----
        f_hot = seg("快捷键与比例尺")
        hotkey_row(f_hot, "自点快捷键", v_self)
        hotkey_row(f_hot, "目标快捷键", v_target)
        hotkey_row(f_hot, "校准快捷键", v_calib)
        hotkey_row(f_hot, "显隐快捷键", v_toggle)
        tk.Label(f_hot, text="点\"录制\"后直接按键盘组合键(如 F9 或 Ctrl+F9),再点\"保存\"生效", bg="#171a1f", fg="#9aa0a6",
                 font=("Microsoft YaHei UI", 8)).pack(**pad)
        tk.Label(f_hot, text="也可手动输入 f8 / ctrl+f8;若与游戏冲突,建议用带 Ctrl 的组合键", bg="#171a1f", fg="#9aa0a6",
                 font=("Microsoft YaHei UI", 8)).pack(**pad)
        tk.Label(f_hot, text="显隐键用于显示/隐藏悬浮窗 —— 隐藏后只有它能叫回来,别改成会和游戏冲突的键",
                 bg="#171a1f", fg="#ffb86c", font=("Microsoft YaHei UI", 8)).pack(**pad)

        row(f_hot, "1单位=多少米", v_scale, "")

        # ---- 语音播报 ----
        vg = self.cfg.setdefault("voice", {})
        f_voice = tk.LabelFrame(seg("语音"), text="语音播报", bg="#171a1f", fg="#34d399",
                                font=("Microsoft YaHei UI", 9), bd=0)
        f_voice.pack(fill="x", padx=8, pady=6)
        v_v_en = tk.BooleanVar(value=bool(vg.get("enabled", True)))
        v_v_result = tk.BooleanVar(value=bool(vg.get("result", True)))
        v_v_coords = tk.BooleanVar(value=bool(vg.get("coords", False)))
        v_v_error = tk.BooleanVar(value=bool(vg.get("error", True)))
        v_v_rate = tk.StringVar(value=str(vg.get("rate", 0)))
        tk.Checkbutton(f_voice, text="启用语音播报(离线·调系统语音)", variable=v_v_en, bg="#171a1f",
                       fg="#e8eaed", activebackground="#171a1f", activeforeground="#e8eaed",
                       selectcolor="#171a1f", font=("Microsoft YaHei UI", 9)).pack(anchor="w", **pad)
        tk.Checkbutton(f_voice, text="播距离/方位(两点就绪自动播)", variable=v_v_result, bg="#171a1f",
                       fg="#e8eaed", activebackground="#171a1f", activeforeground="#e8eaed",
                       selectcolor="#171a1f", font=("Microsoft YaHei UI", 9)).pack(anchor="w", **pad)
        tk.Checkbutton(f_voice, text="播自点/目标坐标", variable=v_v_coords, bg="#171a1f",
                       fg="#e8eaed", activebackground="#171a1f", activeforeground="#e8eaed",
                       selectcolor="#171a1f", font=("Microsoft YaHei UI", 9)).pack(anchor="w", **pad)
        tk.Checkbutton(f_voice, text="识别失败时播报", variable=v_v_error, bg="#171a1f",
                       fg="#e8eaed", activebackground="#171a1f", activeforeground="#e8eaed",
                       selectcolor="#171a1f", font=("Microsoft YaHei UI", 9)).pack(anchor="w", **pad)
        rate_f = tk.Frame(f_voice, bg="#171a1f")
        rate_f.pack(fill="x", **pad)
        tk.Label(rate_f, text="语速(-10~10)", bg="#171a1f", fg="#e8eaed", width=14, anchor="w",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        tk.Entry(rate_f, textvariable=v_v_rate, bg="#1c2026", fg="#e8eaed", insertbackground="#fff",
                 relief="flat", width=22).pack(side="left")
        # 重复次数 / 间隔:用于结果播报多念叨几遍,避免误听
        rep_gap_f = tk.Frame(f_voice, bg="#171a1f")
        rep_gap_f.pack(fill="x", **pad)
        v_v_repeat = tk.StringVar(value=str(vg.get("repeat", 1) or 1))
        v_v_gap = tk.StringVar(value=str(vg.get("gap", 0) or 0))
        tk.Label(rep_gap_f, text="重复次数", bg="#171a1f", fg="#e8eaed", width=14, anchor="w",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        tk.Entry(rep_gap_f, textvariable=v_v_repeat, bg="#1c2026", fg="#e8eaed",
                 insertbackground="#fff", relief="flat", width=6).pack(side="left")
        tk.Label(rep_gap_f, text="间隔(秒)", bg="#171a1f", fg="#e8eaed",
                 font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(10, 0))
        tk.Entry(rep_gap_f, textvariable=v_v_gap, bg="#1c2026", fg="#e8eaed",
                 insertbackground="#fff", relief="flat", width=6).pack(side="left")
        # 音量(0~100):增益/减小
        vol_f = tk.Frame(f_voice, bg="#171a1f")
        vol_f.pack(fill="x", **pad)
        v_v_volume = tk.StringVar(value=str(vg.get("volume", 100) or 100))
        tk.Label(vol_f, text="音量(0~100)", bg="#171a1f", fg="#e8eaed", width=14, anchor="w",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        tk.Entry(vol_f, textvariable=v_v_volume, bg="#1c2026", fg="#e8eaed",
                 insertbackground="#fff", relief="flat", width=6).pack(side="left")
        voice_f = tk.Frame(f_voice, bg="#171a1f")
        voice_f.pack(fill="x", **pad)
        tk.Label(voice_f, text="系统女声", bg="#171a1f", fg="#e8eaed", width=14, anchor="w",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        # 只保留系统已装的女声;无则用系统默认。风格固定原样读
        lang_map = {}
        lang_labels = []
        try:
            for nm, g in list_install_voices():
                if g != "女":
                    continue
                lab = "系统女声 · %s" % nm
                lang_map[lab] = ("normal", nm)
                lang_labels.append(lab)
        except Exception:
            pass
        if not lang_labels:
            lang_labels.append("系统默认")
            lang_map["系统默认"] = ("normal", "")
        def _lang_selected():
            for lab, (_st, nm) in lang_map.items():
                if nm == (vg.get("voice", "") or ""):
                    return lab
            return lang_labels[0]
        v_v_lang = tk.StringVar(value=_lang_selected())
        v_voice_cb = ttk.Combobox(voice_f, textvariable=v_v_lang, state="readonly",
                                  values=lang_labels, width=26)
        v_voice_cb.pack(side="left")
        def try_voice():
            def _play():
                try:
                    st, nm = lang_map.get(v_v_lang.get(), ("normal", ""))
                    fn = title_voice if st == "soldier" else None
                    try:
                        rep = max(1, int(v_v_repeat.get() or 1))
                    except Exception:
                        rep = 1
                    try:
                        gp = float(v_v_gap.get() or 0)
                    except Exception:
                        gp = 0.0
                    try:
                        vv = max(0, min(100, int(v_v_volume.get() or 100)))
                    except Exception:
                        vv = 100
                    _av = VoiceAnnouncer(text_fn=fn,
                                         voice_name=nm,
                                         rate=v_v_rate.get() or 0,
                                         repeat=rep, gap=gp, volume=vv)
                    _av.speak("距离302米,方位12.5度")
                except Exception as e:
                    messagebox.showerror("语音", "语音不可用: %s" % e)
            threading.Thread(target=_play, daemon=True).start()
        tk.Button(f_voice, text="试听", bg="#34d399", fg="#07170f", relief="flat",
                  font=("Microsoft YaHei UI", 9), command=try_voice).pack(**pad)
        tk.Label(f_voice, text="发音在后台线程异步进行,不影响坐标/距离计算速度。",
                 bg="#171a1f", fg="#9aa0a6", font=("Microsoft YaHei UI", 8)).pack(**pad)

        # ---- 瞄具参考线 ----
        rl = self.cfg.setdefault("reticle_line", {})
        f_line = tk.LabelFrame(seg("瞄具参考线"), text="瞄具参考线(用于对齐刻度)", bg="#171a1f", fg="#34d399",
                               font=("Microsoft YaHei UI", 9), bd=0)
        f_line.pack(fill="x", padx=8, pady=6)
        v_line_y = tk.StringVar(value=str(rl.get("y", -1)))
        v_tick_px = tk.StringVar(value=str(rl.get("tick_px", 136)))
        v_xoff = tk.StringVar(value=str(rl.get("x_offset", -420)))
        v_step = tk.StringVar(value=str(rl.get("step_m", 35.0)))
        row(f_line, "刻度线间距", v_tick_px, "")
        row(f_line, "刻度尺横向偏移", v_xoff, "")
        row(f_line, "目标距离Y坐标", v_line_y, "")
        row(f_line, "550米处格差", v_step, "")
        # 分辨率基准选择:刻度参数在 1440 高度(2K带鱼)上实测,其它分辨率按高度等比缩放
        v_res = tk.StringVar(value=str(rl.get("res_height", 0)))
        res_map = {"0": "自动(当前屏,不缩放)", "1440": "3440×1440 带鱼2K", "1600": "2560×1600 笔记本2K",
                   "1080": "1920×1080 FHD", "2160": "3840×2160 4K"}
        f_res = tk.Frame(f_line, bg="#171a1f")
        f_res.pack(fill="x", **pad)
        tk.Label(f_res, text="分辨率基准", bg="#171a1f", fg="#e8eaed", width=14, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        keys = [k for k in res_map if k == v_res.get()] + [k for k in res_map if k != v_res.get()]
        v_res_cb = ttk.Combobox(f_res, textvariable=v_res, state="readonly", width=16,
                                values=[f"{k}: {res_map[k]}" for k in keys])
        v_res_cb.pack(side="left")

        f_ocr = seg("OCR识别")
        f_reg = tk.LabelFrame(f_ocr, text="OCR识别区域(相对鼠标的偏移像素)", bg="#171a1f", fg="#34d399",
                              font=("Microsoft YaHei UI", 9), bd=0)
        f_reg.pack(fill="x", padx=8, pady=6)
        row(f_reg, "向左偏移 box_left", v_left, "")
        row(f_reg, "向上偏移 box_top", v_top, "")
        row(f_reg, "向右偏移 box_right", v_right, "")
        row(f_reg, "向下偏移 box_bottom", v_bottom, "")
        tk.Label(f_reg, text="建议直接按校准键(默认F9)在弹出的窗口里拖数值套住文字,比手填省事",
                 bg="#171a1f", fg="#9aa0a6", font=("Microsoft YaHei UI", 8)).pack(**pad)

        tk.Checkbutton(f_ocr, text="校准时的识别区域保存为预览图片 (_calib_preview.png)",
                       variable=v_debug, bg="#171a1f", fg="#e8eaed",
                       activebackground="#171a1f", activeforeground="#e8eaed",
                       selectcolor="#171a1f", font=("Microsoft YaHei UI", 9)).pack(anchor="w", **pad)

        # 按所选显示方式排布内容
        apply_layout(set_style)

        def save():
            try:
                self.stop_record()          # 若正在录制,先退出录制状态
                self.cfg["hotkey_self"] = v_self.get().strip().lower()
                self.cfg["hotkey_target"] = v_target.get().strip().lower()
                self.cfg["hotkey_calibrate"] = v_calib.get().strip().lower()
                self.cfg["hotkey_toggle"] = v_toggle.get().strip().lower()
                self.cfg["scale_m_per_unit"] = float(v_scale.get())
                self.cfg["region"]["box_left"] = int(v_left.get())
                self.cfg["region"]["box_top"] = int(v_top.get())
                self.cfg["region"]["box_right"] = int(v_right.get())
                self.cfg["region"]["box_bottom"] = int(v_bottom.get())
                self.cfg["debug_preview"] = v_debug.get()
                rl2 = self.cfg.setdefault("reticle_line", {})
                rl2["y"] = int(v_line_y.get())
                rl2["tick_px"] = max(20, int(v_tick_px.get()))
                rl2["x_offset"] = int(v_xoff.get())
                rl2["step_m"] = float(v_step.get())
                rl2["res_height"] = int(v_res.get().split(":")[0])  # 下拉框值形如 "1600: 1600 (...)"
                vg2 = self.cfg.setdefault("voice", {})
                vg2["enabled"] = bool(v_v_en.get())
                vg2["result"] = bool(v_v_result.get())
                vg2["coords"] = bool(v_v_coords.get())
                vg2["error"] = bool(v_v_error.get())
                st, vm = lang_map.get(v_v_lang.get(), ("normal", ""))
                vg2["style"] = st
                vg2["voice"] = vm
                try:
                    vg2["rate"] = int(v_v_rate.get())
                except Exception:
                    vg2["rate"] = 0
                try:
                    vg2["repeat"] = max(1, int(v_v_repeat.get() or 1))
                except Exception:
                    vg2["repeat"] = 1
                try:
                    vg2["gap"] = max(0.0, float(v_v_gap.get() or 0))
                except Exception:
                    vg2["gap"] = 0
                try:
                    vg2["volume"] = max(0, min(100, int(v_v_volume.get() or 100)))
                except Exception:
                    vg2["volume"] = 100
                self._init_voice()          # 应用语音开/关
                save_config(self.cfg)
                self.ref_line.refresh_geometry()
                ok, errors = self.register_hotkeys()
                self.refresh()
                if ok:
                    self._log("设置已保存: %s=自点 %s=目标 %s=校准" % (
                        self.cfg["hotkey_self"].upper(), self.cfg["hotkey_target"].upper(),
                        self.cfg["hotkey_calibrate"].upper()))
                    win.destroy()
                else:
                    self._log("已保存,但快捷键有问题: " + ";".join(errors))
                    messagebox.showwarning(
                        "快捷键未能生效",
                        "配置已保存,但以下快捷键无法绑定:\n\n" + "\n".join(errors) +
                        "\n\n请改填有效按键(如 f8、ctrl+f8),或换一个不与游戏冲突的键。")
                    win.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"保存失败: {e}")

        tk.Button(win, text="保存", command=save, bg="#34d399", fg="#07170f", relief="flat",
                  font=("Microsoft YaHei UI", 10, "bold"), padx=18, pady=4).pack(pady=10)


def check_admin_and_relaunch(cfg):
    try:
        import ctypes as _c
        if not _c.windll.shell32.IsUserAnAdmin() and cfg.get("auto_admin", False):
            # 仅当用户在 config.json 里显式打开 auto_admin 时才提权(默认不需要)
            params = " ".join(['"%s"' % sys.executable] + ['"%s"' % sys.argv[0]])
            _c.windll.shell32.ShellExecuteW(None, "runas", sys.executable, sys.argv[0], os.path.dirname(sys.argv[0]), 1)
            sys.exit(0)
    except Exception:
        pass


if __name__ == "__main__":
    cfg = load_config()

    # 首次运行生成默认配置
    if not os.path.exists(CONFIG_PATH):
        save_config(cfg)

    check_admin_and_relaunch(cfg)

    # 优先使用 exe 同目录下的 tesseract(便携),否则用配置的绝对路径
    local_ts = os.path.join(APP_DIR, "tesseract", "tesseract.exe")
    tesseract_path = None
    if os.path.exists(local_ts):
        tesseract_path = local_ts
    elif os.path.exists(cfg["tesseract_path"]):
        tesseract_path = cfg["tesseract_path"]
    if tesseract_path is None:
        print("找不到 Tesseract:")
        print("  请在程序目录放一个 tesseract 文件夹(或用安装版),")
        print("  或修改 config.json 里的 tesseract_path")
        input("按回车退出……")
        sys.exit(1)
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

    app = OverlayApp(cfg)
    app.root.mainloop()