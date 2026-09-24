# 坐标距离计算（War Dogs · L81 迫击炮辅助）

![license](https://img.shields.io/badge/license-MIT-green)
![platform](https://img.shields.io/badge/platform-Windows-blue)
![python](https://img.shields.io/badge/python-3.14-blue)
![language](https://img.shields.io/badge/language-Python-yellow)
![status](https://img.shields.io/badge/status-stable-brightgreen)

> 🔓 **开源协议：MIT License** —— 免费、开放源代码，可自由查看、修改、二次分发。
> 本项目完全**离线、安全**：不读游戏内存、无键盘钩子、无任何网络上报。

一个离线、安全的战术坐标辅助悬浮工具，为 War Dogs 的 L81 迫击炮提供坐标、距离、方位计算与瞄具刻度对准。

## 📥 下载

| 入口 | 链接 |
|---|---|
| 🏷️ 下载页（全部版本） | https://github.com/Mark136880/coord-tool/releases |
| ⬇️ 最新安装包直链 | [CoordTool-Setup.exe](https://github.com/Mark136880/coord-tool/releases/latest/download/CoordTool-Setup.exe) |

> 安装包内置便携版 OCR，**免装 Tesseract**，可全程离线运行。

## 特性

- **坐标识别**：悬停在地图坐标上，按快捷键自动截屏 OCR 读取 x/y
  - `F8` 存入自点（自己位置），`F9` 存入目标点（可反复覆盖）
  - 实时计算并置顶显示 **距离 + 方位**
- **瞄具刻度尺辅助**：按弹道刻度表在屏幕上画出参考线/幽灵刻度，提示应滚动到的发射刻度
- **分辨率适配**：支持 1440 (带鱼2K) / 1600 (笔记本2K) / 1080 (FHD) / 4K 切换
- **简化模式**：悬浮窗只显示距离/方位，更不挡视野
- **鼠标穿透**：默认穿透不挡游戏（`F6` 或右上角🔒切换，可点按钮/拖拽时切回交互）
- **自适应识别**：白字坐标配合 Otsu 自适应二值化，浅蓝/浅绿等浅色地图也能稳定识别
- **语音播报**：简洁播报"距离X米，方位Y度"，识别后后台语音播报（离线·调系统自带 SAPI，可选系统女声/语速/重复/音量，不影响计算速度）

## 安装

> 无需安装 Tesseract（已内置便携版 OCR），可全程离线运行。

1. 下载下面 **Releases** 里的 `坐标距离计算-安装包.exe`
2. 双击安装（无管理员权限要求），安装到 `%LOCALAPPDATA%\CoordTool`
3. 首次使用请打开一次「设置」确认**分辨率基准**与你屏幕一致

## 使用

1. 把准星放到地图坐标文字上，按 `F8` 记自点，按 `F9` 记目标
2. 悬浮窗显示距离、方位；参考线显示应滚到的迫击炮刻度

## 安全

- 仅使用系统级 `RegisterHotKey` 注册热键，**不读取/注入游戏内存、无键盘钩子、无任何网络上报**
- OCR、计算均在本地完成

## 环境

- Python 3.14 / PyInstaller 打包
- 依赖：Pillow、pytesseract（便携版已内置）

## 常见问题

### 一：播报提示识别失败

1. **检查分辨率**：确认「设置」里的分辨率基准与你显示器分辨率一致（1440 / 1600 / 1080 / 4K）
2. **校准框选**：还是不行的话，鼠标放到需要识别的坐标点上，点击「校准」，看红色框是否完整框选住坐标文字；没框全的话在设置里调整识别区域
3. **靠近背景色**：有时候坐标文字与背景颜色/亮度过于接近，导致识别不准——鼠标先不动，把视角拉远一点再试