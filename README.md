# LatentVision Studio（潜像视觉工作台）

**实时拓扑追踪与声学共鸣系统 · Real-Time Topology & Synesthetic Audio Engine**

本项目包含：
- **纯前端 60FPS WebGL + Web Audio 实时交互工作台**（[`index.html`](./index.html) / [`templates/index.html`](./templates/index.html)）：无需后端即可直接在 **GitHub Pages** 或现代浏览器中以 60FPS 运行，支持内置花朵演示视频、本地视频/音频拖拽导入、摄像头实时追踪与 WebM 录制。
- **Python 离线高清 MP4 渲染引擎与 Flask 服务端**（[`baby_track.py`](./baby_track.py) / [`app.py`](./app.py)）：支持逐帧高精度合成与 FFmpeg H.264 + AAC 音画同步导出。
- **After Effects 自动化脚本桥接**（[`LatentVision_AE.jsx`](./LatentVision_AE.jsx) / [`ae_bridge.py`](./ae_bridge.py)）。

---

## 一、核心功能特性

1. **5 种几何包络形态（支持一键关闭追踪框）**：
   - `方框 (square)`、`自适应框 (rect)`、`圆形 (circle)`、生成式 **`粒子场 (particle)`**（中心微十字发射源 + 24 颗径向旋涡流线拖尾粒子）、精密光学 **`十字 (cross)`**。
   - 顶部内置 **「显示追踪框」** 独立开关，关闭后可隐藏所有边框仅保留拓扑连线、遥测数值或局部区域滤镜。
2. **13 种矢量边框样式 & 14 种 WebGL 局部区域滤镜**：
   - 边框样式：四角折线框、极简实线框、顶点锚点框、对角交叉框、顶部/悬浮信息标签、雷达准星刻度、扫描网格线、动态流动虚线、复古系统弹窗、霓虹辉光外框、环绕粒子轨迹、中心十字准星。
   - 区域着色器：热成像感应、负片反色、故障错位色散、像素马赛克、有序矩阵抖动、骨骼透视显影、边缘轮廓发光、曲面扫描线、水波纹折射、主题色调染色、纯色块遮罩、柔和模糊、二倍光学放大。
3. **高灵敏度多频段声学共鸣引擎（Web Audio FFT + Python FFT）**：
   - 实时分离低频（Bass）、中频（Mid）、高频（High）瞬态能量。
   - 支持 **追踪框内与周围局部色差（Localized Box & Halo RGB Chromatic Split）**、重低音画面闪光、低音镜头推拉、方框节奏弹跳、琴弦声波共振连线及底部频谱 HUD。
4. **纯净三语界面切换（中文 / English / 日本語）**：
   - 采用深色磨砂玻璃三栏工作台布局与统一自定义下拉菜单组件。

---

## 二、快速启动

### 方式 A：GitHub Pages 在线即开即用
将本仓库推送至 GitHub 并在 **Settings → Pages** 中将 Source 设为 `Deploy from a branch (main / root)`，即可直接在线访问完整的 60FPS 实时工作台。

### 方式 B：本地 Python 服务端运行（含 FFmpeg 高清 MP4 导出）
```bash
pip install -r requirements.txt
python app.py
```
启动后浏览器访问 `http://127.0.0.1:5050`。
