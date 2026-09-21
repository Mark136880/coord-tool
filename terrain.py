# -*- coding: utf-8 -*-
"""
高程地形数据包(.wdt)读取模块 —— wardogs-terrain-pack-v1

每张地图的 .wdt 里存了一张 511x511 的 u16 高度网格(每顶点间距 2 米,整图约 1020 米),
zstd 压缩 + lorenzo 预测,头 16 字节小头后跟 256 个 zstd 帧,拼起来正好是整张网格。

高度换算:海拔(米) = 原始值 * heightStepMeters(a米) + heightBaseDecimeters(十分之一米)。

用法:
    t = Terrain("bakurani.wdt")
    m = t.height_at(game_e, game_n)      # 返回该点海拔(米)

坐标映射(来自包内 headerme 的 coverage):
    col = (e - gameXMin) / (gameXMax - gameXMin) * (N-1)
    row = (n - gameYMin) / (gameYMax - gameYMin) * (N-1)  (游戏 Y 与网格行通常反向)
"""
import json
import array
import os
import threading

import zstandard

__all__ = ["Terrain", "resolve_pack"]


# LADDER 最大射程(dM用):恳求不必依赖。这里仅用于说明。
def resolve_pack(packs_dir, map_id):
    """在 pack 目录里找一个地图的高度包。优先精确文件名,其次前缀匹配。"""
    if not packs_dir or not os.path.isdir(packs_dir):
        return None
    map_id = (map_id or "").strip().lower()
    exact = os.path.join(packs_dir, map_id + ".wdt")
    if os.path.isfile(exact):
        return exact
    for name in sorted(os.listdir(packs_dir)):
        if name.lower().startswith(map_id + ".") and name.lower().endswith(".wdt"):
            return os.path.join(packs_dir, name)
    return None


class Terrain:
    def __init__(self, path):
        with open(path, "rb") as f:
            data = f.read()

        # ---- 1) 解析头部的 JSON 元数据(从第一个 '{' 做括号配对) ----
        si = data.index(b"{")
        depth = 0
        end = -1
        for i in range(si, len(data)):
            c = data[i]
            if c == 0x7B:
                depth += 1
            elif c == 0x7D:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        self.header = json.loads(data[si:end].decode("utf-8"))

        # ---- 2) 解压高度网格 ----
        body = data[end:]
        dctx = zstandard.ZstdDecompressor()
        blob = dctx.decompress(body[16:], max_output_size=200_000_000)

        h = self.header
        self.n = int(h["verticesPerSide"])                 # 511
        expect = self.n * self.n * 2
        if len(blob) != expect:
            raise ValueError("高度网格尺寸不符: 期望 %d 字节, 实际 %d" % (expect, len(blob)))
        self.grid = array.array("H", blob)                 # u16

        cov = h["coverage"]
        self.gx0, self.gx1 = float(cov["gameXMin"]), float(cov["gameXMax"])
        self.gy0, self.gy1 = float(cov["gameYMin"]), float(cov["gameYMax"])
        self.base_m = float(h.get("heightBaseDecimeters", 0)) / 10.0
        self.step = float(h.get("heightStepMeters", 0.1))

    def height_at(self, e, n, y_flip=True):
        """给定游戏世界坐标(e 东西, n 南北),返回该点海拔(米)。坐标越界则取最近的网格点。"""
        n1 = float(self.n - 1)
        c = (e - self.gx0) / max(1e-9, (self.gx1 - self.gx0)) * n1
        r = (n - self.gy0) / max(1e-9, (self.gy1 - self.gy0)) * n1
        if y_flip:
            r = n1 - r
        j = max(0, min(int(c + 0.5), self.n - 1))
        i = max(0, min(int(r + 0.5), self.n - 1))
        v = self.grid[i * self.n + j]
        return v * self.step + self.base_m


class TerrainLoader:
    """后台线程载入高度包,避免阻塞界面。载入结果用 is_ready()/height_at() 取用。"""

    def __init__(self, packs_dir, map_id):
        self._path = resolve_pack(packs_dir, map_id)
        self._terrain = None
        self._error = None
        if self._path:
            threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            self._terrain = Terrain(self._path)
        except Exception as e:              # noqa: BLE001 —— 载入失败仅记录,不影响主功能
            self._error = str(e)

    def is_ready(self):
        return self._terrain is not None

    @property
    def path(self):
        return self._path

    @property
    def error(self):
        return self._error

    def height_at(self, e, n, y_flip=True):
        """就绪时才有效;未就绪/失败/无路径返回 None。"""
        t = self._terrain
        if t is None:
            return None
        try:
            return t.height_at(e, n, y_flip)
        except Exception:                   # noqa: BLE001
            return None