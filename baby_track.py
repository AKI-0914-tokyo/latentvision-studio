#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BabyTrack 视频增效复刻引擎 (Python 版)
=====================================
完整复刻 https://artkit.cc/baby-track 的核心视觉算法管线：
1. 降采样连通域斑点检测 (Downscaled 4-Connected Blob Detection & Flood-Fill)
2. 区域几何与掩膜生成 (Square / Circle / Rect / Diamond)
3. 区域滤镜特效层 (Invert / Thermal / Pixel / Glitch / Dither / X-Ray / Edge / Blur / Zoom / Flip)
4. 矢量 HUD 图形样式层 (Basic / Cross / Label / Frame / L-Frame / X-Frame / Grid / Scope / Glow / Particle)
5. 拓扑连线路由与方框数值显示 (Straight / Curve / Snake / Lightning + 多种方框内/外数值显示模式)
"""

from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy import ndimage

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# ============================================================================
# 1. 数据结构与配置参数
# ============================================================================


@dataclass
class Blob:
    """连通域斑点数据结构（坐标与尺寸均已映射回原视频分辨率）"""

    x: float          # 质心 X 坐标
    y: float          # 质心 Y 坐标
    size: float       # 缩放后的斑点面积 (pixel_count * (1 / scale))
    min_x: float      # 外接矩形左边界
    min_y: float      # 外接矩形上边界
    max_x: float      # 外接矩形右边界
    max_y: float      # 外接矩形下边界


@dataclass
class BabyTrackConfig:
    """BabyTrack 核心配置参数"""

    # --- 斑点检测参数 ---
    threshold: int = 128                 # 亮度阈值: (R+G+B)/3 > 128
    max_detect_dim: int = 512            # 检测画布最大边长
    blob_size: float = 128.0             # 追踪框最大包络尺寸 (0, 32, 64, 128, 256, 512)
    blob_size_threshold: float = 64.0    # 斑点最小面积阈值 (16, 32, 64, 128, 256, 512)
    blob_count_threshold: int = 20       # 按数量过滤时保留的最大斑点数
    blob_threshold_by_count: bool = False  # True: 按数量取前N大; False: 按面积阈值过滤
    same_size_enabled: bool = False      # True: 所有框统一使用 blob_size / 2
    frame_skip: int = 0                  # 跳帧检测间隔 (0 = 每帧检测)
    single_tracking_mode: bool = False   # 单目标平滑游走追踪模式

    # --- 形状与区域样式 ---
    # shape 可选: "square"(1), "circle"(2), "rect"(3), "particle"(4, 粒子发射场), "cross"(5, 精密十字准星)
    show_box: bool = True                # 是否显示追踪框几何包络与边框
    shape: str = "square"
    # region_style 可选:
    #   矢量 HUD 类: "basic", "cross", "label", "label2", "frame", "l_frame",
    #               "x_frame", "grid", "particle", "scope", "glow", "flip"
    #   Shader 滤镜类: "invert", "glitch", "thermal", "pixel", "tone", "blur",
    #                 "dither", "zoom", "xray", "edge"
    region_style: str = "l_frame"
    filter_effect: str = "none"          # 可独立叠加在框内的区域滤镜
    invert_mask: bool = False            # False: 框内应用滤镜; True: 框外背景应用滤镜
    region_blink: bool = False           # 300ms 周期伪随机闪烁

    # --- 连线路由参数 ---
    line_num: float = 0.25               # 连线密度比例 (0, 0.25, 0.5, 0.75, 1.0)
    # line_style 可选: "straight", "curve", "snake", "lightning"
    line_style: str = "straight"
    use_center_as_hub: bool = False      # True: 画面中心星型辐射连线; False: 斑点间随机配对连线
    connection_point_pos: str = "center" # "center" 或 "edge"
    is_dashed_line: bool = False         # 是否使用虚线连接
    dashed_line_size: int = 5
    dashed_line_gap: int = 5

    # --- 颜色与方框数值显示参数 ---
    color: str = "#FFFFFF"               # 主色调 ("#RRGGBB")
    use_separate_colors: bool = False
    frame_color: str = "#FFFFFF"
    connection_color: str = "#FFFFFF"
    text_color: str = "#FFFFFF"
    crazy_color: bool = False            # 随机高饱和度 HSL 色彩模式
    stroke_width: int = 2                # 线条粗细 (px)
    show_text: bool = True               # 是否在方框上/内显示数值
    # text_position 可选:
    #   "center" (方框正中心), "inside_tl" (方框内部左上角), "top" (方框外部上方), "bottom" (方框外部下方)
    text_position: str = "center"
    # text_content_type 可选:
    #   "random"   : 原版固定伪随机数值 ((i+1)/7, 如 0.1429)
    #   "dynamic"  : 随时间动态跳动的传感器数值 (如 0.8421)
    #   "size"     : 斑点实时面积/能量数值 (如 248.5)
    #   "position" : 实时坐标数值 (如 X:320 Y:180)
    #   "id_score" : 目标编号+置信度数值 (如 #01 96.4%)
    #   "count"    : 画面斑点总数 (如 Count 12)
    text_content_type: str = "random"
    font_size: int = 12
    # --- ROI 局部追踪区域 (归一化 0~1: x0, y0, x1, y1) ---
    roi_rect: Optional[Tuple[float, float, float, float]] = None

    # --- 音频反应 (Audio-Reactive) 参数 ---
    audio_path: Optional[str] = None          # 外部导入的音乐文件路径 (.mp3/.wav/.m4a/.ogg)
    audio_reactive: bool = False              # 是否启用声音驱动视觉效果
    audio_gain: float = 1.0                   # 声音灵敏度增益 (0.2 ~ 4.0)
    audio_flash: bool = True                  # 低音鼓点触发全屏屏闪/曝光脉冲 (Bass Strobe)
    audio_chromatic: bool = True              # 高低音驱动 RGB 色差通道分离 (Chromatic Aberration)
    audio_bass_zoom: bool = True              # 重低音驱动镜头推拉呼吸缩放 (Bass Pump Zoom)
    audio_box_bounce: bool = True             # 声音驱动追踪框尺寸跳动 + 高频抖动 (Box Bounce & Jitter)
    audio_line_wave: bool = True              # 声音驱动连接线条声波振荡弯曲 (Waveform Lines)
    audio_spectrum_hud: bool = True           # 底部/框内绘制实时频谱柱 HUD (Spectrum Bars)


def extract_audio_envelope(
    media_path: Optional[str], fps: float, total_frames: int, gain: float = 1.0
) -> List[Tuple[float, float, float]]:
    """
    使用 FFmpeg + NumPy FFT 提取音频的逐帧 (Bass 低音, Mid 中音, High 高音) 瞬态动态对比度包络 [0.0 ~ 1.0]。
    采用：100% 局部均值基线扣除 (Baseline Subtraction) + 帧间正通量 (Spectral Flux) +
    向下动态扩展器 (Downward Expander pow 1.75) + 快速起音/快速释放包络，
    确保平稳伴奏回落至 0%~10%，鼓点瞬态爆发至 85%~100%，彻底拉开强弱差距。
    """
    n_frames = max(1, total_frames)
    ffmpeg_bin = shutil.which("ffmpeg")
    sr = 22050

    if media_path and os.path.exists(media_path) and ffmpeg_bin:
        try:
            cmd = [
                ffmpeg_bin,
                "-v", "error",
                "-i", media_path,
                "-vn",
                "-ac", "1",
                "-ar", str(sr),
                "-f", "f32le",
                "-",
            ]
            raw_bytes = subprocess.check_output(cmd, timeout=25)
            samples = np.frombuffer(raw_bytes, dtype=np.float32)
            if samples.size > 1024:
                win_size = 1024
                hann = np.hanning(win_size).astype(np.float32)
                raw_bands = []
                for idx in range(n_frames):
                    center = int((idx / max(1.0, fps)) * sr)
                    # 若音频比视频短，支持循环取样
                    center = center % max(1, samples.size - win_size)
                    chunk = samples[center : center + win_size]
                    if chunk.size < win_size:
                        chunk = np.pad(chunk, (0, win_size - chunk.size))
                    spec = np.abs(np.fft.rfft(chunk * hann))
                    # 22050Hz / 1024 = 21.5Hz/bin -> Bass: 20~200Hz (1:10), Mid: 200~2200Hz (10:102), High: 2.2k~10kHz (102:465)
                    b_seg = spec[1:10] if spec.size > 10 else spec[:1]
                    m_seg = spec[10:102] if spec.size > 102 else spec[:1]
                    h_seg = spec[102:465] if spec.size > 465 else spec[:1]
                    # 线性声学功率特征：峰值功率与均方根结合
                    b_val = float(np.max(b_seg) * 0.65 + np.sqrt(np.mean(b_seg ** 2)) * 0.45)
                    m_val = float(np.max(m_seg) * 0.65 + np.sqrt(np.mean(m_seg ** 2)) * 0.45)
                    h_val = float(np.max(h_seg) * 0.65 + np.sqrt(np.mean(h_seg ** 2)) * 0.45)
                    raw_bands.append((b_val, m_val, h_val))

                arr = np.array(raw_bands, dtype=np.float32)
                if float(np.max(arr)) > 1e-4:
                    out_env = np.zeros_like(arr)
                    for ch in range(3):
                        series = arr[:, ch]
                        ema = float(series[0])
                        peak = max(1e-4, float(np.percentile(series, 95)) * 0.35)
                        prev = float(series[0])
                        env = 0.0
                        for i in range(n_frames):
                            raw = float(series[i])
                            # 1. 追踪局部平滑基线并扣除 96% 的持续背景音
                            ema = ema * 0.92 + raw * 0.08
                            excess = max(0.0, raw - ema * 0.96)
                            # 2. 提取帧间正能量跳变 (Onset Flux)
                            flux = max(0.0, raw - prev) * 1.45
                            prev = raw
                            pulse = excess * 0.55 + flux * 0.95
                            # 3. 局部动态峰值归一化
                            if pulse > peak:
                                peak = peak * 0.60 + pulse * 0.40
                            else:
                                peak = max(1e-4, peak * 0.992)
                            norm = min(1.5, pulse / max(1e-4, peak * 0.92))
                            # 4. 噪声门限 (0.12) + 向下动态扩展器 (pow 1.75)
                            if norm < 0.12:
                                shaped = 0.0
                            else:
                                shaped = ((norm - 0.12) / 0.88) ** 1.75
                            target = min(1.5, shaped * gain)
                            # 5. 瞬态快起音 + 快速指数衰减 (0.68)
                            if target > env:
                                env = target
                            else:
                                env = env * 0.68 + target * 0.32
                            out_env[i, ch] = 0.0 if env < 0.015 else env
                    return [(float(r[0]), float(r[1]), float(r[2])) for r in out_env]
        except Exception:
            pass

    # 回退：128 BPM 电子合成节拍包络 (Kick 低音 + Snare 中音 + 16分音符 Hi-Hat 高音)
    result = []
    for idx in range(n_frames):
        t = idx / max(1.0, fps)
        beat_phase = (t * (128.0 / 60.0)) % 1.0
        bass = max(0.0, (1.0 - beat_phase * 3.2)) ** 2 * gain * 0.75
        mid = max(0.0, math.sin(t * math.pi * 4.2)) ** 4 * gain * 0.55
        high = abs(math.sin(t * math.pi * 16.0)) ** 3 * gain * 0.65
        result.append((min(1.5, bass), min(1.5, mid), min(1.5, high)))
    return result


# ============================================================================
# 2. 工具函数：色彩转换与伪随机哈希
# ============================================================================


def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) == 6:
        try:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            pass
    return (255, 255, 255)


def hsl_to_rgb(h_deg: float, s_pct: float, l_pct: float) -> Tuple[int, int, int]:
    h = (h_deg % 360.0) / 60.0
    s = max(0.0, min(1.0, s_pct / 100.0))
    l = max(0.0, min(1.0, l_pct / 100.0))
    c = (1.0 - abs(2.0 * l - 1.0)) * s
    x = c * (1.0 - abs((h % 2.0) - 1.0))
    m = l - c / 2.0
    if h < 1.0:
        r, g, b = c, x, 0.0
    elif h < 2.0:
        r, g, b = x, c, 0.0
    elif h < 3.0:
        r, g, b = 0.0, c, x
    elif h < 4.0:
        r, g, b = 0.0, x, c
    elif h < 5.0:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return (
        int(np.clip((r + m) * 255, 0, 255)),
        int(np.clip((g + m) * 255, 0, 255)),
        int(np.clip((b + m) * 255, 0, 255)),
    )


def random_crazy_color() -> Tuple[int, int, int]:
    return hsl_to_rgb(random.random() * 360.0, 70.0, 50.0)


def mulberry32_hash(seed: int) -> float:
    t = (seed + 0x6D2B79F5) & 0xFFFFFFFF
    t = ((t ^ (t >> 15)) * (1 | t)) & 0xFFFFFFFF
    t = (t ^ ((t + ((t ^ (t >> 7)) * (61 | t))) & 0xFFFFFFFF)) & 0xFFFFFFFF
    t = (t ^ (t >> 14)) & 0xFFFFFFFF
    return t / 4294967296.0


def should_blink_show(region_blink: bool, time_ms: float, blob_idx: int) -> bool:
    if not region_blink:
        return True
    bucket = int(time_ms // 300)
    seed = (1664525 * bucket + 0x3C6EF35F * blob_idx) & 0xFFFFFFFF
    return mulberry32_hash(seed) < 0.5


# ============================================================================
# 3. 核心算法一：连通域斑点检测器 (4-Connected Flood-Fill Blob Detector)
# ============================================================================


def detect_blobs(frame_rgb: np.ndarray, cfg: BabyTrackConfig) -> List[Blob]:
    h_orig, w_orig = frame_rgb.shape[:2]
    if h_orig == 0 or w_orig == 0:
        return []

    max_dim = max(w_orig, h_orig)
    scale = min(1.0, cfg.max_detect_dim / float(max_dim)) if max_dim > 0 else 1.0
    v = max(1, int(math.floor(w_orig * scale)))
    w = max(1, int(math.floor(h_orig * scale)))

    pil_small = Image.fromarray(frame_rgb).resize((v, w), resample=Image.BILINEAR)
    small_arr = np.asarray(pil_small, dtype=np.float32)

    brightness = (small_arr[:, :, 0] + small_arr[:, :, 1] + small_arr[:, :, 2]) / 3.0
    binary_mask = brightness > cfg.threshold

    structure_4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.int32)
    labeled, num_features = ndimage.label(binary_mask, structure=structure_4)
    if num_features == 0:
        return []

    inv_scale = 1.0 / scale
    flat_labels = labeled.ravel()
    counts = np.bincount(flat_labels)[1:]

    y_indices, x_indices = np.nonzero(labeled)
    nonzero_labels = labeled[y_indices, x_indices] - 1

    sum_x = np.bincount(nonzero_labels, weights=x_indices, minlength=num_features)
    sum_y = np.bincount(nonzero_labels, weights=y_indices, minlength=num_features)
    slices = ndimage.find_objects(labeled)

    blobs: List[Blob] = []
    for idx, slc in enumerate(slices):
        if slc is None:
            continue
        n = int(counts[idx])
        if n <= 0:
            continue
        scaled_size = n * inv_scale
        if not cfg.blob_threshold_by_count and scaled_size <= cfg.blob_size_threshold:
            continue

        slice_y, slice_x = slc
        cx = (sum_x[idx] / n) * inv_scale
        cy = (sum_y[idx] / n) * inv_scale
        min_x = slice_x.start * inv_scale
        max_x = slice_x.stop * inv_scale
        min_y = slice_y.start * inv_scale
        max_y = slice_y.stop * inv_scale

        blobs.append(
            Blob(
                x=float(cx),
                y=float(cy),
                size=float(scaled_size),
                min_x=float(min_x),
                min_y=float(min_y),
                max_x=float(max_x),
                max_y=float(max_y),
            )
        )

    if cfg.roi_rect is not None:
        rx0, ry0, rx1, ry1 = cfg.roi_rect
        x_min, x_max = min(rx0, rx1) * w_orig, max(rx0, rx1) * w_orig
        y_min, y_max = min(ry0, ry1) * h_orig, max(ry0, ry1) * h_orig
        blobs = [b for b in blobs if x_min <= b.x <= x_max and y_min <= b.y <= y_max]

    if cfg.blob_threshold_by_count and len(blobs) > 0:
        k = max(1, int(cfg.blob_count_threshold))
        blobs.sort(key=lambda b: b.size, reverse=True)
        blobs = blobs[:k]

    return blobs


# ============================================================================
# 4. 核心算法二：区域滤镜特效 (复刻 WebGL Fragment Shader 效果)
# ============================================================================


def apply_shader_effect(
    frame_rgb: np.ndarray,
    effect_name: str,
    time_sec: float,
    tint_rgb: Tuple[int, int, int],
) -> np.ndarray:
    h, w = frame_rgb.shape[:2]
    img_f = frame_rgb.astype(np.float32) / 255.0

    if effect_name == "invert":
        out = 1.0 - img_f

    elif effect_name == "thermal":
        intensity = (
            0.299 * img_f[:, :, 0] + 0.587 * img_f[:, :, 1] + 0.114 * img_f[:, :, 2]
        )
        time_offset = (time_sec / 3.0) % 1.0
        hue = (intensity * 300.0 + time_offset * 360.0) % 360.0
        h_prime = hue / 60.0
        sat = 0.5 + intensity * 0.5
        light = 0.3 + intensity * 0.5

        c = (1.0 - np.abs(2.0 * light - 1.0)) * sat
        x = c * (1.0 - np.abs((h_prime % 2.0) - 1.0))
        m = light - c / 2.0

        r = np.zeros_like(intensity)
        g = np.zeros_like(intensity)
        b = np.zeros_like(intensity)

        m0 = h_prime < 1.0
        m1 = (h_prime >= 1.0) & (h_prime < 2.0)
        m2 = (h_prime >= 2.0) & (h_prime < 3.0)
        m3 = (h_prime >= 3.0) & (h_prime < 4.0)
        m4 = (h_prime >= 4.0) & (h_prime < 5.0)
        m5 = h_prime >= 5.0

        r[m0], g[m0] = c[m0], x[m0]
        r[m1], g[m1] = x[m1], c[m1]
        g[m2], b[m2] = c[m2], x[m2]
        g[m3], b[m3] = x[m3], c[m3]
        r[m4], b[m4] = x[m4], c[m4]
        r[m5], b[m5] = c[m5], x[m5]

        out = np.stack([r + m, g + m, b + m], axis=-1)

    elif effect_name == "pixel":
        block = 8
        small_w = max(1, w // block)
        small_h = max(1, h // block)
        pil_img = Image.fromarray(frame_rgb)
        pixelated = pil_img.resize((small_w, small_h), Image.NEAREST).resize(
            (w, h), Image.NEAREST
        )
        return np.asarray(pixelated, dtype=np.uint8)

    elif effect_name == "blur":
        pil_img = Image.fromarray(frame_rgb)
        blurred = pil_img.filter(ImageFilter.GaussianBlur(radius=4))
        return np.asarray(blurred, dtype=np.uint8)

    elif effect_name == "tone":
        tint = np.array(tint_rgb, dtype=np.float32) / 255.0
        lum = np.mean(img_f, axis=2, keepdims=True)
        out = lum * tint * 0.7 + img_f * 0.3

    elif effect_name == "dither":
        bayer4 = (
            np.array(
                [
                    [0, 8, 2, 10],
                    [12, 4, 14, 6],
                    [3, 11, 1, 9],
                    [15, 7, 13, 5],
                ],
                dtype=np.float32,
            )
            / 16.0
        )
        tiled_threshold = np.tile(bayer4, (h // 4 + 1, w // 4 + 1))[:h, :w]
        brightness = (
            0.299 * img_f[:, :, 0] + 0.587 * img_f[:, :, 1] + 0.114 * img_f[:, :, 2]
        )
        dither_mask = (brightness >= tiled_threshold).astype(np.float32)[:, :, None]
        tint = np.array(tint_rgb, dtype=np.float32) / 255.0
        out = dither_mask * tint

    elif effect_name == "xray":
        inverted = 1.0 - img_f
        gray = (
            0.299 * inverted[:, :, 0]
            + 0.587 * inverted[:, :, 1]
            + 0.114 * inverted[:, :, 2]
        )
        gray = np.sqrt(np.clip(gray, 0.0, 1.0))
        t = np.clip((gray - 0.3) / (0.7 - 0.3), 0.0, 1.0)
        gray = t * t * (3.0 - 2.0 * t)

        orig_gray = (
            0.299 * img_f[:, :, 0] + 0.587 * img_f[:, :, 1] + 0.114 * img_f[:, :, 2]
        )
        tl = np.roll(np.roll(orig_gray, 1, axis=0), 1, axis=1)
        br = np.roll(np.roll(orig_gray, -1, axis=0), -1, axis=1)
        tr = np.roll(np.roll(orig_gray, 1, axis=0), -1, axis=1)
        bl = np.roll(np.roll(orig_gray, -1, axis=0), 1, axis=1)
        edge = np.abs(tl - br) + np.abs(tr - bl)
        et = np.clip((edge - 0.1) / (0.3 - 0.1), 0.0, 1.0)
        edge_smooth = et * et * (3.0 - 2.0 * et)

        final_gray = np.clip(gray + edge_smooth * 0.5, 0.0, 1.0)
        out = np.stack([final_gray * 0.8, final_gray * 0.9, final_gray], axis=-1)

    elif effect_name == "edge":
        gray = (
            0.299 * img_f[:, :, 0] + 0.587 * img_f[:, :, 1] + 0.114 * img_f[:, :, 2]
        )
        gx = ndimage.sobel(gray, axis=1)
        gy = ndimage.sobel(gray, axis=0)
        edge = np.hypot(gx, gy)
        et = np.clip((edge - 0.2) / (0.6 - 0.2), 0.0, 1.0)
        edge_smooth = (et * et * (3.0 - 2.0 * et))[:, :, None]
        tint = np.array(tint_rgb, dtype=np.float32) / 255.0
        if np.sum(tint) < 0.03:
            tint = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        out = np.clip(img_f + edge_smooth * tint, 0.0, 1.0)

    elif effect_name == "glitch":
        y_norm = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
        t_val = 0.1 * time_sec
        scan1 = np.floor(y_norm * 20.0 + t_val * 5.0)
        rand1 = np.mod(np.sin(scan1 * 12.9898) * 43758.5453, 1.0)
        shift_x = ((rand1 - 0.5) * 0.05 * w).astype(np.int32).ravel()

        displaced = np.empty_like(img_f)
        for row in range(h):
            displaced[row] = np.roll(img_f[row], int(shift_x[row]), axis=0)

        rgb_shift = max(1, int(0.005 * w))
        r_ch = np.roll(displaced[:, :, 0], rgb_shift, axis=1)
        g_ch = displaced[:, :, 1]
        b_ch = np.roll(displaced[:, :, 2], -rgb_shift, axis=1)
        out = np.stack([r_ch, g_ch, b_ch], axis=-1)

    elif effect_name == "crt":
        # CRT 扫描线 + 色散 + 暗角
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        uv_y = yy / max(1.0, float(h))
        uv_x = xx / max(1.0, float(w))
        ca = max(1, int(1.5 * w / 640.0))
        r_ch = np.roll(img_f[:, :, 0], ca, axis=1)
        g_ch = img_f[:, :, 1]
        b_ch = np.roll(img_f[:, :, 2], -ca, axis=1)
        color = np.stack([r_ch, g_ch, b_ch], axis=-1)
        scan = (np.sin(yy * 2.0 + time_sec * 10.0) * 0.12)[:, :, None]
        cx = (uv_x - 0.5) * (w / max(1.0, float(h)))
        cy = uv_y - 0.5
        r2 = cx * cx + cy * cy
        vignette = np.clip((0.85 - r2) / 0.55, 0.2, 1.0)[:, :, None]
        out = np.clip((color - scan) * vignette, 0.0, 1.0)

    elif effect_name == "water":
        # Water 水波正弦重采样
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        t2 = time_sec * 2.0
        X = (xx / w) * 40.0 + t2
        Y = (yy / h) * 40.0 + t2
        dy = (np.cos(X + Y) * 0.025 * np.cos(Y) * h).astype(np.int32)
        dx = (np.sin(X - Y) * 0.025 * np.sin(Y) * w).astype(np.int32)
        ny = np.clip(yy.astype(np.int32) + dy, 0, h - 1)
        nx = np.clip(xx.astype(np.int32) + dx, 0, w - 1)
        out = img_f[ny, nx]

    elif effect_name == "mask":
        # Mask 纯色块遮罩
        tint = np.array(tint_rgb, dtype=np.float32) / 255.0
        out = np.broadcast_to(tint, img_f.shape).copy()

    else:
        return frame_rgb.copy()

    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


# ============================================================================
# 5. 核心算法三：BabyTrack 逐帧渲染器 (掩膜合成 + 矢量 HUD + 方框数值 + 连线)
# ============================================================================


SHADER_STYLES = {
    "invert",
    "glitch",
    "thermal",
    "pixel",
    "tone",
    "blur",
    "dither",
    "xray",
    "edge",
    "crt",
    "water",
    "mask",
}


class BabyTrackProcessor:
    """封装帧间状态并完成逐帧特效渲染"""

    def __init__(self, cfg: BabyTrackConfig):
        self.cfg = cfg
        self.frame_index = 0
        self.cached_blobs: List[Blob] = []
        self.single_blob: Optional[Blob] = None
        self.target_x: float = 0.0
        self.target_y: float = 0.0
        self.is_waiting: bool = False
        self.wait_start_ms: float = 0.0

    def _get_rect_geometry(
        self, blob: Blob, side: float
    ) -> Tuple[float, float, float, float]:
        if (
            self.cfg.shape == "rect"
            and not self.cfg.single_tracking_mode
            and not self.cfg.same_size_enabled
            and blob.max_x > blob.min_x
            and blob.max_y > blob.min_y
        ):
            raw_w = blob.max_x - blob.min_x
            raw_h = blob.max_y - blob.min_y
            max_dim = self.cfg.blob_size * 1.35
            scale = min(1.0, max_dim / max(raw_w, raw_h, 1.0))
            w = max(22.0, raw_w * scale)
            h = max(16.0, raw_h * scale)
            return blob.x - w / 2.0, blob.y - h / 2.0, w, h
        elif self.cfg.shape == "rect":
            w = side * 1.45
            h = side * 0.85
            return blob.x - w / 2.0, blob.y - h / 2.0, w, h
        else:
            return blob.x - side / 2.0, blob.y - side / 2.0, side, side

    @staticmethod
    def _draw_cross_shape(
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        side: float,
        col_rgba: Tuple[int, int, int, int],
        sw: int,
    ) -> None:
        """绘制精密光学十字准星 (中心留白 + 四向主准星臂 + 双级标尺刻度 + 四象限锚点)"""
        r = max(10.0, side * 0.52)
        gap = max(3.5, r * 0.18)
        tick1, tick2 = r * 0.48, r * 0.78
        tick_len = max(2.5, r * 0.11)
        draw.line([(cx - r, cy), (cx - gap, cy)], fill=col_rgba, width=sw)
        draw.line([(cx + gap, cy), (cx + r, cy)], fill=col_rgba, width=sw)
        draw.line([(cx, cy - r), (cx, cy - gap)], fill=col_rgba, width=sw)
        draw.line([(cx, cy + gap), (cx, cy + r)], fill=col_rgba, width=sw)

        sub_rgba = (col_rgba[0], col_rgba[1], col_rgba[2], int(col_rgba[3] * 0.78))
        for off in (-tick1, tick1, -tick2, tick2):
            draw.line([(cx + off, cy - tick_len), (cx + off, cy + tick_len)], fill=sub_rgba, width=1)
            draw.line([(cx - tick_len, cy + off), (cx + tick_len, cy + off)], fill=sub_rgba, width=1)

        d = r * 0.38
        dot_rgba = (col_rgba[0], col_rgba[1], col_rgba[2], int(col_rgba[3] * 0.65))
        for dx, dy in ((-d, -d), (d, -d), (-d, d), (d, d)):
            draw.rectangle([cx + dx - 1, cy + dy - 1, cx + dx + 1, cy + dy + 1], fill=dot_rgba)

    @staticmethod
    def _draw_particle_field(
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        side: float,
        cur_rgb: Tuple[int, int, int],
        sw: int,
        time_sec: float,
        idx: int,
        audio_boost: float,
        flares: List[Tuple[float, float, float, Tuple[int, int, int]]],
    ) -> None:
        """绘制生成式粒子发射场 (中心微准星发射源 + 24颗径向涡流拖尾粒子 + 柔光散景微粒)"""
        r = max(14.0, side * 0.62)
        core_arm = max(3.5, min(7.0, r * 0.14))
        core_rgba = (*cur_rgb, 190)
        draw.line([(cx - core_arm, cy), (cx + core_arm, cy)], fill=core_rgba, width=max(1, sw - 1))
        draw.line([(cx, cy - core_arm), (cx, cy + core_arm)], fill=core_rgba, width=max(1, sw - 1))

        ring_r = r * 0.72
        ring_rgba = (*cur_rgb, 64)
        for a in range(4):
            ang_deg = math.degrees(time_sec * 0.45 + idx * 0.7) + a * 90.0
            draw.arc(
                [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
                start=ang_deg,
                end=ang_deg + 24.0,
                fill=ring_rgba,
                width=1,
            )

        count = 24
        speed_mul = 1.0 + audio_boost * 1.15
        for k in range(count):
            h1 = mulberry32_hash((idx + 1) * 3571 + k * 997)
            h2 = mulberry32_hash((idx + 1) * 7919 + k * 1613)
            h3 = mulberry32_hash((idx + 1) * 2903 + k * 421)

            speed = (0.42 + h1 * 0.78) * speed_mul
            life = (time_sec * speed + h2) % 1.0
            ease_out = 1.0 - (1.0 - life) ** 1.85
            swirl = (h3 - 0.5) * 1.15 * life
            ang = (k / float(count)) * (2.0 * math.pi) + h1 * 1.2 + swirl

            dist = ease_out * r * (0.85 + h2 * 0.45)
            px = cx + math.cos(ang) * dist
            py = cy + math.sin(ang) * dist

            tail_len = max(3.0, (1.0 - life * 0.55) * r * (0.20 + audio_boost * 0.14))
            tail_ang = ang - swirl * 0.35
            tx = px - math.cos(tail_ang) * tail_len
            ty = py - math.sin(tail_ang) * tail_len

            alpha = int(max(15, min(255, math.sin(life * math.pi) * (0.45 + 0.55 * h3) * 255.0)))
            p_rgba = (*cur_rgb, alpha)
            draw.line([(tx, ty), (px, py)], fill=p_rgba, width=max(1, int(round(sw * 0.65))))

            pr = max(1.1, (1.0 + h1 * 2.1) * (1.0 - life * 0.45))
            draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=p_rgba)
            if k % 5 == 0 and alpha > 110:
                flares.append((px, py, max(2.5, pr * 1.9), cur_rgb))

    def _update_single_tracker(self, w: int, h: int, time_ms: float) -> List[Blob]:
        side = self.cfg.blob_size
        if self.single_blob is None:
            cx = random.uniform(side, max(side + 1, w - side))
            cy = random.uniform(side, max(side + 1, h - side))
            self.single_blob = Blob(cx, cy, side, 0, 0, w, h)
            self.target_x = random.uniform(side, max(side + 1, w - side))
            self.target_y = random.uniform(side, max(side + 1, h - side))
        else:
            if self.is_waiting:
                if time_ms - self.wait_start_ms >= 1200.0:
                    self.is_waiting = False
                    for _ in range(10):
                        nx = random.uniform(side, max(side + 1, w - side))
                        ny = random.uniform(side, max(side + 1, h - side))
                        if math.hypot(nx - self.single_blob.x, ny - self.single_blob.y) >= 0.3 * min(w, h):
                            break
                    self.target_x, self.target_y = nx, ny
            else:
                dx = self.target_x - self.single_blob.x
                dy = self.target_y - self.single_blob.y
                if math.hypot(dx, dy) < 15.0:
                    self.is_waiting = True
                    self.wait_start_ms = time_ms
                else:
                    nx = np.clip(self.single_blob.x + dx * 0.08, side, max(side, w - side))
                    ny = np.clip(self.single_blob.y + dy * 0.08, side, max(side, h - side))
                    self.single_blob.x = float(nx)
                    self.single_blob.y = float(ny)
        return [self.single_blob]

    def _format_blob_value(self, idx: int, blob: Blob, total_blobs: int, time_ms: float) -> str:
        """根据配置生成方框内/外要显示的数值字符串"""
        t_type = self.cfg.text_content_type
        if t_type == "position":
            return f"X:{int(round(blob.x))} Y:{int(round(blob.y))}"
        elif t_type == "size":
            return f"{blob.size:.1f}"
        elif t_type == "dynamic":
            # 随时间轻微跳动的仿真传感器数值 (每 150ms 刷新一次)
            bucket = int(time_ms // 150)
            val = mulberry32_hash((idx + 1) * 9973 + bucket * 31337)
            return f"{val:.4f}"
        elif t_type == "id_score":
            bucket = int(time_ms // 300)
            score = 85.0 + 14.9 * mulberry32_hash((idx + 1) * 4099 + bucket * 101)
            return f"#{idx + 1:02d} {score:.1f}%"
        elif t_type == "count":
            return f"Count {total_blobs}"
        else:
            # 默认 random: 原版 ((idx + 1) / 7).toFixed(4)
            return f"{(idx + 1) / 7.0:.4f}"

    def process_frame(
        self,
        frame_rgb: np.ndarray,
        time_sec: float,
        audio_bands: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> np.ndarray:
        cfg = self.cfg
        h, w = frame_rgb.shape[:2]
        time_ms = time_sec * 1000.0
        self.frame_index += 1
        a_bass, a_mid, a_high = audio_bands if cfg.audio_reactive else (0.0, 0.0, 0.0)

        # 1. 获取当前帧斑点列表
        if cfg.single_tracking_mode:
            blobs = self._update_single_tracker(w, h, time_ms)
            self.cached_blobs = blobs
        else:
            should_detect = (cfg.frame_skip == 0) or (
                self.frame_index % (cfg.frame_skip + 1) == 0
            )
            if should_detect or not self.cached_blobs:
                self.cached_blobs = detect_blobs(frame_rgb, cfg)
            blobs = self.cached_blobs

        # 若启用声音驱动方框跳动/高频微颤，按当前帧音频能量调制斑块坐标与尺寸
        if cfg.audio_reactive and cfg.audio_box_bounce and blobs:
            mod_blobs = []
            for idx, b in enumerate(blobs):
                jx = math.sin(time_sec * 36.0 + idx * 2.1) * (a_high * 12.0 + a_mid * 4.0)
                jy = math.cos(time_sec * 31.0 + idx * 1.7) * (a_high * 12.0 + a_mid * 4.0)
                scale_f = 1.0 + a_bass * 0.55
                hw = ((b.max_x - b.min_x) * scale_f) / 2.0
                hh = ((b.max_y - b.min_y) * scale_f) / 2.0
                nx = float(np.clip(b.x + jx, 0, w))
                ny = float(np.clip(b.y + jy, 0, h))
                mod_blobs.append(
                    Blob(
                        x=nx,
                        y=ny,
                        size=b.size * scale_f,
                        min_x=nx - hw,
                        min_y=ny - hh,
                        max_x=nx + hw,
                        max_y=ny + hh,
                    )
                )
            blobs = mod_blobs

        base_rgb = hex_to_rgb(cfg.color)
        frame_rgb_col = hex_to_rgb(cfg.frame_color) if cfg.use_separate_colors else base_rgb
        conn_rgb_col = hex_to_rgb(cfg.connection_color) if cfg.use_separate_colors else base_rgb
        text_rgb_col = hex_to_rgb(cfg.text_color) if cfg.use_separate_colors else base_rgb

        # 2. 区域滤镜层与音频画面级特效 (镜头推拉 / RGB 色差 / 低音屏闪)
        active_filter = (
            cfg.filter_effect
            if cfg.filter_effect != "none"
            else (cfg.region_style if cfg.region_style in SHADER_STYLES else "none")
        )

        canvas_arr = frame_rgb.copy()

        # 2a. 重低音驱动镜头推拉呼吸缩放 (Bass Pump Zoom)
        if cfg.audio_reactive and cfg.audio_bass_zoom and a_bass > 0.05:
            zoom_factor = 1.0 + min(0.16, a_bass * 0.09)
            cw_z = max(2, int(round(w / zoom_factor)))
            ch_z = max(2, int(round(h / zoom_factor)))
            x0_z = max(0, (w - cw_z) // 2)
            y0_z = max(0, (h - ch_z) // 2)
            cropped_z = canvas_arr[y0_z : y0_z + ch_z, x0_z : x0_z + cw_z]
            if HAS_CV2:
                canvas_arr = cv2.resize(cropped_z, (w, h), interpolation=cv2.INTER_LINEAR)
            else:
                canvas_arr = np.asarray(Image.fromarray(cropped_z).resize((w, h), Image.BILINEAR), dtype=np.uint8)

        # 2b. 高低音驱动局部方框及周围光晕 RGB 色差分离 (Localized Box & Halo Chromatic Aberration)
        if cfg.audio_reactive and cfg.audio_chromatic and (a_bass + a_high) > 0.08 and blobs:
            shift_x = max(1, int(round(a_bass * 9.0 + a_high * 14.0)))
            shift_y = int(round(a_high * 5.0 + a_bass * 2.0))
            ca_mask_img = Image.new("L", (w, h), 0)
            ca_mask_draw = ImageDraw.Draw(ca_mask_img)
            halo_pad = 16.0
            for idx, blob in enumerate(blobs):
                if not should_blink_show(cfg.region_blink, time_ms, idx):
                    continue
                side = cfg.blob_size / 2.0 if cfg.same_size_enabled else min(blob.size, cfg.blob_size)
                rx, ry, rw, rh = self._get_rect_geometry(blob, side)
                # 框外周围光晕区域 (权重 180) 与框内区域 (权重 255)
                ca_mask_draw.rectangle(
                    [rx - halo_pad, ry - halo_pad, rx + rw + halo_pad, ry + rh + halo_pad], fill=185
                )
                ca_mask_draw.rectangle([rx, ry, rx + rw, ry + rh], fill=255)
            ca_weight = (np.asarray(ca_mask_img, dtype=np.float32) / 255.0)[:, :, None]
            ca_arr = canvas_arr.copy()
            ca_arr[:, :, 0] = np.roll(np.roll(canvas_arr[:, :, 0], shift_x, axis=1), shift_y, axis=0)
            ca_arr[:, :, 2] = np.roll(np.roll(canvas_arr[:, :, 2], -shift_x, axis=1), -shift_y, axis=0)
            canvas_arr = (canvas_arr * (1.0 - ca_weight) + ca_arr * ca_weight).astype(np.uint8)

        # 2c. 重低音鼓点屏闪 / 曝光脉冲 (Bass Strobe / Flash)
        if cfg.audio_reactive and cfg.audio_flash and a_bass > 0.42:
            flash_p = min(1.0, (a_bass - 0.42) * 1.45)
            f_arr = canvas_arr.astype(np.float32) * (1.0 + flash_p * 0.55) + flash_p * 90.0
            canvas_arr = np.clip(f_arr, 0, 255).astype(np.uint8)

        if cfg.region_style in ("zoom", "flip") or active_filter == "zoom":
            mode = "flip" if cfg.region_style == "flip" else "zoom"
            for idx, blob in enumerate(blobs):
                if not should_blink_show(cfg.region_blink, time_ms, idx):
                    continue
                side = cfg.blob_size / 2.0 if cfg.same_size_enabled else min(blob.size, cfg.blob_size)
                rx, ry, rw, rh = self._get_rect_geometry(blob, side)
                x0, y0 = max(0, int(rx)), max(0, int(ry))
                x1, y1 = min(w, int(rx + rw)), min(h, int(ry + rh))
                if x1 <= x0 + 1 or y1 <= y0 + 1:
                    continue
                patch = canvas_arr[y0:y1, x0:x1]
                if mode == "flip":
                    canvas_arr[y0:y1, x0:x1] = patch[::-1, ::-1]
                else:
                    ph, pw = patch.shape[:2]
                    cy_p, cx_p = ph // 2, pw // 2
                    hy, hx = max(1, ph // 4), max(1, pw // 4)
                    cropped = patch[max(0, cy_p - hy) : min(ph, cy_p + hy), max(0, cx_p - hx) : min(pw, cx_p + hx)]
                    if cropped.size > 0:
                        zoomed = Image.fromarray(cropped).resize((pw, ph), Image.BILINEAR)
                        canvas_arr[y0:y1, x0:x1] = np.asarray(zoomed, dtype=np.uint8)

        if active_filter in SHADER_STYLES:
            effect_arr = apply_shader_effect(canvas_arr, active_filter, time_sec, frame_rgb_col)
            mask_img = Image.new("L", (w, h), 0)
            mask_draw = ImageDraw.Draw(mask_img)

            for idx, blob in enumerate(blobs):
                if not should_blink_show(cfg.region_blink, time_ms, idx):
                    continue
                side = cfg.blob_size / 2.0 if cfg.same_size_enabled else min(blob.size, cfg.blob_size)
                if cfg.shape in ("square", "rect"):
                    rx, ry, rw, rh = self._get_rect_geometry(blob, side)
                    mask_draw.rectangle([rx, ry, rx + rw, ry + rh], fill=255)
                elif cfg.shape in ("circle", "particle", "star", "diamond"):
                    r = side / 2.0
                    mask_draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], fill=255)
                elif cfg.shape == "cross":
                    arm = side * 0.48
                    thick = max(6.0, side * 0.22)
                    mask_draw.rectangle([blob.x - arm, blob.y - thick / 2.0, blob.x + arm, blob.y + thick / 2.0], fill=255)
                    mask_draw.rectangle([blob.x - thick / 2.0, blob.y - arm, blob.x + thick / 2.0, blob.y + arm], fill=255)

            mask_f = (np.asarray(mask_img, dtype=np.float32) / 255.0)[:, :, None]
            if cfg.invert_mask:
                mask_f = 1.0 - mask_f
            canvas_arr = (canvas_arr * (1.0 - mask_f) + effect_arr * mask_f).astype(np.uint8)

        # 3. 转换为 RGBA 绘制矢量图层与数值
        pil_canvas = Image.fromarray(canvas_arr).convert("RGBA")
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", cfg.font_size)
            label_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 12)
        except Exception:
            font = ImageFont.load_default()
            label_font = font

        sw = max(1, int(cfg.stroke_width))
        is_rect = cfg.shape in ("square", "rect")

        if cfg.single_tracking_mode and len(blobs) == 1:
            b0 = blobs[0]
            col_rgba = (*frame_rgb_col, 255)
            draw.line([(0, b0.y), (w, b0.y)], fill=col_rgba, width=sw)
            draw.line([(b0.x, 0), (b0.x, h)], fill=col_rgba, width=sw)

        # 四角星形尖角光斑 (模糊柔光层)
        star_flares: List[Tuple[float, float, float, Tuple[int, int, int]]] = []

        # 4. 逐个斑点绘制 HUD 样式与方框数值
        for idx, blob in enumerate(blobs):
            if not should_blink_show(cfg.region_blink, time_ms, idx):
                continue

            cur_rgb = random_crazy_color() if cfg.crazy_color else frame_rgb_col
            cur_rgba = (*cur_rgb, 255)
            side = cfg.blob_size / 2.0 if cfg.same_size_enabled else min(blob.size, cfg.blob_size)

            # 中心追踪锚点
            draw.ellipse([blob.x - 1.5, blob.y - 1.5, blob.x + 1.5, blob.y + 1.5], fill=cur_rgba)

            if cfg.blob_size <= 0:
                continue

            rx, ry, rw, rh = self._get_rect_geometry(blob, side)
            style = cfg.region_style
            audio_boost = (a_bass * 0.7 + a_high * 0.6) if cfg.audio_reactive else 0.0

            if cfg.show_box:
                # 若开启音频 RGB 色差，在追踪框周围绘制红/青双通道偏移残影外框
                if cfg.audio_reactive and cfg.audio_chromatic and (a_bass + a_high) > 0.12:
                    ca_dx = max(1.5, a_bass * 5.0 + a_high * 8.0)
                    ca_dy = a_high * 3.0
                    ca_alpha = int(min(210, 75 + (a_bass + a_high) * 110))
                    if is_rect:
                        draw.rectangle(
                            [rx - ca_dx, ry - ca_dy, rx + rw - ca_dx, ry + rh - ca_dy],
                            outline=(56, 189, 248, ca_alpha),
                            width=max(1, sw - 1),
                        )
                        draw.rectangle(
                            [rx + ca_dx, ry + ca_dy, rx + rw + ca_dx, ry + rh + ca_dy],
                            outline=(244, 63, 94, ca_alpha),
                            width=max(1, sw - 1),
                        )
                    elif cfg.shape == "cross":
                        self._draw_cross_shape(draw, blob.x - ca_dx, blob.y - ca_dy, side, (56, 189, 248, ca_alpha), max(1, sw - 1))
                        self._draw_cross_shape(draw, blob.x + ca_dx, blob.y + ca_dy, side, (244, 63, 94, ca_alpha), max(1, sw - 1))
                    else:
                        r_ca = (side * 0.36) if cfg.shape in ("particle", "star", "diamond") else (side / 2.0)
                        draw.ellipse(
                            [blob.x - r_ca - ca_dx, blob.y - r_ca - ca_dy, blob.x + r_ca - ca_dx, blob.y + r_ca - ca_dy],
                            outline=(56, 189, 248, ca_alpha),
                            width=max(1, sw - 1),
                        )
                        draw.ellipse(
                            [blob.x - r_ca + ca_dx, blob.y - r_ca + ca_dy, blob.x + r_ca + ca_dx, blob.y + r_ca + ca_dy],
                            outline=(244, 63, 94, ca_alpha),
                            width=max(1, sw - 1),
                        )

                if cfg.shape in ("particle", "star", "diamond"):
                    self._draw_particle_field(draw, blob.x, blob.y, side, cur_rgb, sw, time_sec, idx, audio_boost, star_flares)
                elif cfg.shape == "cross":
                    self._draw_cross_shape(draw, blob.x, blob.y, side, cur_rgba, sw)
                elif style == "l_frame":
                    fill_rgba = (*cur_rgb, 26)
                    if is_rect:
                        draw.rectangle([rx, ry, rx + rw, ry + rh], fill=fill_rgba)
                        cl = 0.25 * min(rw, rh)
                        draw.line([(rx, ry + cl), (rx, ry), (rx + cl, ry)], fill=cur_rgba, width=sw)
                        draw.line([(rx + rw - cl, ry), (rx + rw, ry), (rx + rw, ry + cl)], fill=cur_rgba, width=sw)
                        draw.line([(rx, ry + rh - cl), (rx, ry + rh), (rx + cl, ry + rh)], fill=cur_rgba, width=sw)
                        draw.line([(rx + rw - cl, ry + rh), (rx + rw, ry + rh), (rx + rw, ry + rh - cl)], fill=cur_rgba, width=sw)
                    else:
                        r = side / 2.0
                        draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], fill=fill_rgba)
                        for angle_deg in (-90, 0, 90, 180):
                            draw.arc(
                                [blob.x - r, blob.y - r, blob.x + r, blob.y + r],
                                start=angle_deg - 15,
                                end=angle_deg + 15,
                                fill=cur_rgba,
                                width=sw,
                            )

                elif style == "x_frame":
                    if is_rect:
                        draw.rectangle([rx, ry, rx + rw, ry + rh], outline=cur_rgba, width=sw)
                        draw.line([(rx, ry), (rx + rw, ry + rh)], fill=cur_rgba, width=sw)
                        draw.line([(rx + rw, ry), (rx, ry + rh)], fill=cur_rgba, width=sw)
                    else:
                        r = side / 2.0
                        draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], outline=cur_rgba, width=sw)
                        d = r * math.sqrt(0.5)
                        draw.line([(blob.x - d, blob.y - d), (blob.x + d, blob.y + d)], fill=cur_rgba, width=sw)
                        draw.line([(blob.x + d, blob.y - d), (blob.x - d, blob.y + d)], fill=cur_rgba, width=sw)

                elif style == "frame":
                    if is_rect:
                        draw.rectangle([rx, ry, rx + rw, ry + rh], outline=cur_rgba, width=sw)
                        for vx, vy in [(rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh)]:
                            draw.rectangle([vx - 3, vy - 3, vx + 3, vy + 3], fill=cur_rgba)
                    else:
                        r = side / 2.0
                        draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], outline=cur_rgba, width=sw)
                        for vx, vy in [(blob.x, blob.y - r), (blob.x, blob.y + r), (blob.x - r, blob.y), (blob.x + r, blob.y)]:
                            draw.ellipse([vx - 3, vy - 3, vx + 3, vy + 3], fill=cur_rgba)

                elif style in ("label", "label2"):
                    val_str = self._format_blob_value(idx, blob, len(blobs), time_ms)
                    label_str = f"Obj {idx + 1} | {val_str}" if cfg.show_text else f"Object {idx + 1}"
                    txt_col = (0, 0, 0, 255) if cur_rgb == (255, 255, 255) else (255, 255, 255, 255)
                    if cfg.use_separate_colors:
                        txt_col = (*text_rgb_col, 255)
                    if is_rect:
                        draw.rectangle([rx, ry, rx + rw, ry + rh], outline=cur_rgba, width=sw)
                        if style == "label":
                            bar_w = max(rw, len(label_str) * 6.5 + 8)
                            draw.rectangle([rx, ry, rx + bar_w, ry + 16], fill=cur_rgba)
                            draw.text((rx + 4, ry + 1), label_str, fill=txt_col, font=label_font)
                        else:
                            tag_w = max(56, len(label_str) * 6.5 + 12)
                            tag_y = max(0, ry - 18)
                            draw.rectangle([rx, tag_y, rx + tag_w, tag_y + 18], fill=cur_rgba)
                            draw.text((rx + 5, tag_y + 2), label_str, fill=txt_col, font=label_font)
                    else:
                        r = side / 2.0
                        draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], outline=cur_rgba, width=sw)
                        lx, ly = max(0, blob.x + 8), max(0, blob.y - 8)
                        tag_w = max(56, len(label_str) * 6.5 + 10)
                        draw.rectangle([lx, ly, lx + tag_w, ly + 16], fill=cur_rgba)
                        draw.text((lx + 4, ly + 1), label_str, fill=txt_col, font=label_font)

                elif style == "grid":
                    grid_rgba = (*cur_rgb, 96)
                    if is_rect:
                        draw.rectangle([rx, ry, rx + rw, ry + rh], outline=cur_rgba, width=sw)
                        y_cur = ry
                        while y_cur <= ry + rh:
                            draw.line([(rx, y_cur), (rx + rw, y_cur)], fill=grid_rgba, width=1)
                            y_cur += 6
                    else:
                        r = side / 2.0
                        draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], outline=cur_rgba, width=sw)
                        dy = -r
                        while dy <= r:
                            half_chord = math.sqrt(max(0.0, r * r - dy * dy))
                            draw.line([(blob.x - half_chord, blob.y + dy), (blob.x + half_chord, blob.y + dy)], fill=grid_rgba, width=1)
                            dy += 6

                elif style == "scope":
                    tick_rgba = (*cur_rgb, 140)
                    if is_rect:
                        draw.rectangle([rx, ry, rx + rw, ry + rh], outline=cur_rgba, width=sw)
                        draw.line([(rx, blob.y), (rx + rw, blob.y)], fill=cur_rgba, width=sw)
                        draw.line([(blob.x, ry), (blob.x, ry + rh)], fill=cur_rgba, width=sw)
                        for k in range(1, 8):
                            tx = rx + k * (rw / 8.0)
                            ty = ry + k * (rh / 8.0)
                            draw.line([(tx, blob.y - 4), (tx, blob.y + 4)], fill=tick_rgba, width=1)
                            draw.line([(blob.x - 4, ty), (blob.x + 4, ty)], fill=tick_rgba, width=1)
                    else:
                        r = side / 2.0
                        draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], outline=cur_rgba, width=sw)
                        draw.line([(blob.x - r, blob.y), (blob.x + r, blob.y)], fill=cur_rgba, width=sw)
                        draw.line([(blob.x, blob.y - r), (blob.x, blob.y + r)], fill=cur_rgba, width=sw)
                        for ring in (0.25, 0.5, 0.75):
                            rr = r * ring
                            draw.ellipse([blob.x - rr, blob.y - rr, blob.x + rr, blob.y + rr], outline=tick_rgba, width=1)

                elif style == "cross":
                    self._draw_cross_shape(draw, blob.x, blob.y, side, cur_rgba, sw)

                elif style == "glow":
                    flicker = 0.7 + 0.3 * math.sin(time_sec * 6.66)
                    for glow_w, alpha in [(sw * 4, int(90 * flicker)), (sw * 2, int(160 * flicker)), (sw, 255)]:
                        g_col = (*cur_rgb, min(255, alpha))
                        if is_rect:
                            draw.rectangle([rx, ry, rx + rw, ry + rh], outline=g_col, width=max(1, glow_w))
                        else:
                            r = side / 2.0
                            draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], outline=g_col, width=max(1, glow_w))

                elif style == "particle":
                    self._draw_particle_field(draw, blob.x, blob.y, side, cur_rgb, sw, time_sec, idx, audio_boost, star_flares)

                elif style == "dash":
                    if is_rect:
                        self._draw_dashed_line(draw, (rx, ry), (rx + rw, ry), cur_rgba, sw, 6, 4)
                        self._draw_dashed_line(draw, (rx + rw, ry), (rx + rw, ry + rh), cur_rgba, sw, 6, 4)
                        self._draw_dashed_line(draw, (rx + rw, ry + rh), (rx, ry + rh), cur_rgba, sw, 6, 4)
                        self._draw_dashed_line(draw, (rx, ry + rh), (rx, ry), cur_rgba, sw, 6, 4)
                    else:
                        r = side / 2.0
                        steps = 24
                        for s_i in range(0, steps, 2):
                            a1 = (2.0 * math.pi * s_i) / steps
                            a2 = (2.0 * math.pi * (s_i + 1)) / steps
                            draw.line(
                                [(blob.x + math.cos(a1) * r, blob.y + math.sin(a1) * r),
                                 (blob.x + math.cos(a2) * r, blob.y + math.sin(a2) * r)],
                                fill=cur_rgba,
                                width=sw,
                            )

                elif style == "win2k":
                    bar_h = max(14.0, min(20.0, rh * 0.22))
                    draw.rectangle([rx, ry, rx + rw, ry + rh], outline=(224, 224, 224, 255), width=max(1, sw))
                    draw.rectangle([rx, ry, rx + rw, ry + bar_h], fill=cur_rgba)
                    btn_s = max(8.0, bar_h - 4.0)
                    bx = rx + rw - btn_s - 2.0
                    by = ry + 2.0
                    if rw > 28:
                        draw.rectangle([bx, by, bx + btn_s, by + btn_s], fill=(212, 208, 200, 255))
                        draw.line([(bx + 2, by + 2), (bx + btn_s - 2, by + btn_s - 2)], fill=(0, 0, 0, 255), width=1)
                        draw.line([(bx + btn_s - 2, by + 2), (bx + 2, by + btn_s - 2)], fill=(0, 0, 0, 255), width=1)
                    if rw > 54:
                        txt_col = (0, 0, 0, 255) if (cur_rgb[0] * 0.299 + cur_rgb[1] * 0.587 + cur_rgb[2] * 0.114) > 150 else (255, 255, 255, 255)
                        draw.text((rx + 4, ry + 1), f"blob_{idx}.exe", fill=txt_col, font=self._get_font(10))

                else:
                    if is_rect:
                        draw.rectangle([rx, ry, rx + rw, ry + rh], outline=cur_rgba, width=sw)
                    elif cfg.shape == "circle":
                        r = side / 2.0
                        draw.ellipse([blob.x - r, blob.y - r, blob.x + r, blob.y + r], outline=cur_rgba, width=sw)

            # --- 渲染方框内/外数值文本 ---
            if cfg.show_text and (not cfg.show_box or cfg.shape in ("particle", "cross", "star", "diamond") or style not in ("label", "label2")):
                txt = self._format_blob_value(idx, blob, len(blobs), time_ms)
                txt_rgba = (*cur_rgb, 255) if cfg.crazy_color else (*text_rgb_col, 255)

                # 计算文字像素宽高以精确定位
                try:
                    bbox = draw.textbbox((0, 0), txt, font=font)
                    tw_px = bbox[2] - bbox[0]
                    th_px = bbox[3] - bbox[1]
                except Exception:
                    tw_px = len(txt) * (cfg.font_size * 0.6)
                    th_px = cfg.font_size

                if cfg.text_position == "center":
                    # 方框正中央对齐显示数值
                    tx = blob.x - tw_px / 2.0
                    ty = blob.y - th_px / 2.0 - 1.0
                elif cfg.text_position == "inside_tl":
                    # 方框内部左上角显示数值
                    tx = (rx + 5.0) if is_rect else (blob.x - side / 2.0 + 5.0)
                    ty = (ry + 4.0) if is_rect else (blob.y - side / 2.0 + 4.0)
                elif cfg.text_position == "top":
                    # 方框外部上方
                    tx = rx if is_rect else (blob.x - tw_px / 2.0)
                    ty = max(4.0, (ry if is_rect else blob.y - side / 2.0) - th_px - 5.0)
                else:
                    # bottom: 方框外部下方
                    tx = rx if is_rect else (blob.x - tw_px / 2.0)
                    ty = min(h - th_px - 4.0, (ry + rh if is_rect else blob.y + side / 2.0) + 4.0)

                draw.text((tx, ty), txt, fill=txt_rgba, font=font)

        # 5. 拓扑连线路由
        active_indices = [
            i for i in range(len(blobs)) if should_blink_show(cfg.region_blink, time_ms, i)
        ]

        def get_anchor(b: Blob) -> Tuple[float, float]:
            if cfg.connection_point_pos == "center":
                return (b.x, b.y)
            side = cfg.blob_size / 2.0 if cfg.same_size_enabled else min(b.size, cfg.blob_size)
            ang = random.random() * 2.0 * math.pi
            return (b.x + math.cos(ang) * (side / 2.0), b.y + math.sin(ang) * (side / 2.0))

        pairs: List[Tuple[Tuple[float, float], Tuple[float, float], int]] = []
        if cfg.use_center_as_hub:
            center_pt = (w / 2.0, h / 2.0)
            for k, idx in enumerate(active_indices):
                pairs.append((center_pt, get_anchor(blobs[idx]), k))
        elif len(active_indices) >= 2 and cfg.line_num > 0:
            num_lines = int(math.ceil(len(active_indices) * cfg.line_num))
            for k in range(num_lines):
                i1 = random.choice(active_indices)
                i2 = random.choice(active_indices)
                tries = 0
                while i2 == i1 and tries < 6:
                    i2 = random.choice(active_indices)
                    tries += 1
                if i1 != i2:
                    pairs.append((get_anchor(blobs[i1]), get_anchor(blobs[i2]), k))

        for p1, p2, line_idx in pairs:
            line_rgb = random_crazy_color() if cfg.crazy_color else conn_rgb_col
            line_rgba = (*line_rgb, 230)
            line_w = max(1, int(round(sw + (a_bass * 2.0 if cfg.audio_reactive else 0.0))))
            self._draw_styled_connection(
                draw, p1, p2, line_idx, line_rgba, line_w, time_sec, (a_bass, a_mid, a_high)
            )

        # 6. 音频频谱柱 HUD (底部 TouchDesigner 风格三频段频谱仪)
        if cfg.audio_reactive and cfg.audio_spectrum_hud:
            num_bars = 28
            bar_area_w = min(w * 0.84, 460.0)
            start_x = (w - bar_area_w) / 2.0
            step_x = bar_area_w / num_bars
            bar_w = max(2.0, step_x * 0.62)
            base_y = h - 18.0
            max_bar_h = min(68.0, h * 0.12)
            for b_i in range(num_bars):
                ratio = b_i / float(max(1, num_bars - 1))
                if ratio < 0.33:
                    band_e = a_bass * (0.7 + 0.3 * math.sin(time_sec * 18.0 + b_i))
                    b_col = (56, 189, 248, 210)
                elif ratio < 0.66:
                    band_e = a_mid * (0.7 + 0.3 * math.cos(time_sec * 24.0 + b_i * 1.3))
                    b_col = (52, 211, 153, 210)
                else:
                    band_e = a_high * (0.65 + 0.35 * abs(math.sin(time_sec * 36.0 + b_i * 2.1)))
                    b_col = (251, 191, 36, 210)
                bh = max(3.0, min(max_bar_h, band_e * max_bar_h))
                bx0 = start_x + b_i * step_x
                draw.rectangle([bx0, base_y - bh, bx0 + bar_w, base_y], fill=b_col)

        if star_flares:
            flare_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            f_draw = ImageDraw.Draw(flare_layer)
            for fx, fy, fr, fc in star_flares:
                f_draw.ellipse([fx - fr * 1.8, fy - fr * 1.8, fx + fr * 1.8, fy + fr * 1.8], fill=(*fc, 70))
                f_draw.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=(*fc, 150))
                f_draw.ellipse([fx - fr * 0.35, fy - fr * 0.35, fx + fr * 0.35, fy + fr * 0.35], fill=(255, 255, 255, 230))
            flare_layer = flare_layer.filter(ImageFilter.GaussianBlur(4))
            pil_canvas = Image.alpha_composite(pil_canvas, flare_layer)

        out_rgba = Image.alpha_composite(pil_canvas, overlay)
        return np.asarray(out_rgba.convert("RGB"), dtype=np.uint8)

    def _draw_styled_connection(
        self,
        draw: ImageDraw.ImageDraw,
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        line_idx: int,
        color_rgba: Tuple[int, int, int, int],
        width: int,
        time_sec: float = 0.0,
        audio_bands: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        x1, y1 = p1
        x2, y2 = p2
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return

        nx, ny = -dy / dist, dx / dist
        style = self.cfg.line_style
        a_bass, a_mid, a_high = audio_bands

        if self.cfg.audio_reactive and self.cfg.audio_line_wave and (a_bass + a_mid + a_high) > 0.02:
            # 声波琴弦共振线条：两端锚定在追踪框中心，中间随中高频声波实时正弦/谐波振荡
            amp = (a_bass * 12.0 + a_mid * 20.0 + a_high * 28.0)
            pts = []
            for t in np.linspace(0.0, 1.0, 28):
                env = math.sin(t * math.pi)
                wave = (
                    math.sin(t * math.pi * 5.0 + time_sec * 34.0 + line_idx * 1.7) * 0.65
                    + math.cos(t * math.pi * 11.0 - time_sec * 48.0 + line_idx) * 0.35 * (a_high + 0.2)
                )
                off = env * amp * wave
                pts.append((x1 + dx * t + nx * off, y1 + dy * t + ny * off))
        elif style == "curve":
            offset = 0.3 * dist * (1.0 if line_idx % 2 == 0 else -1.0)
            cx = (x1 + x2) / 2.0 + nx * offset
            cy = (y1 + y2) / 2.0 + ny * offset
            pts = [
                (
                    (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t**2 * x2,
                    (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t**2 * y2,
                )
                for t in np.linspace(0.0, 1.0, 24)
            ]
        elif style == "snake":
            amp = min(80.0, 0.99 * dist)
            c1x, c1y = x1 + dx / 3.0 + nx * amp, y1 + dy / 3.0 + ny * amp
            c2x, c2y = x1 + 2.0 * dx / 3.0 - nx * amp, y1 + 2.0 * dy / 3.0 - ny * amp
            pts = [
                (
                    (1 - t) ** 3 * x1
                    + 3 * (1 - t) ** 2 * t * c1x
                    + 3 * (1 - t) * t**2 * c2x
                    + t**3 * x2,
                    (1 - t) ** 3 * y1
                    + 3 * (1 - t) ** 2 * t * c1y
                    + 3 * (1 - t) * t**2 * c2y
                    + t**3 * y2,
                )
                for t in np.linspace(0.0, 1.0, 28)
            ]
        elif style == "lightning":
            max_jitter = min(50.0, 0.4 * dist)
            pts = [(x1, y1)]
            for step in range(1, 6):
                ratio = step / 6.0
                bx, by = x1 + dx * ratio, y1 + dy * ratio
                jitter = 0.5 * random.random() * max_jitter * (-1.0 if random.random() < 0.5 else 1.0)
                pts.append((bx + nx * jitter, by + ny * jitter))
            pts.append((x2, y2))
        else:
            pts = [(x1, y1), (x2, y2)]

        if not self.cfg.is_dashed_line:
            draw.line(pts, fill=color_rgba, width=width)
        else:
            dash_len = max(1, self.cfg.dashed_line_size)
            gap_len = max(1, self.cfg.dashed_line_gap)
            cycle = dash_len + gap_len
            acc = 0.0
            for i in range(len(pts) - 1):
                sx, sy = pts[i]
                ex, ey = pts[i + 1]
                seg_len = math.hypot(ex - sx, ey - sy)
                if seg_len < 1e-3:
                    continue
                ux, uy = (ex - sx) / seg_len, (ey - sy) / seg_len
                pos = 0.0
                while pos < seg_len:
                    rem_in_cycle = (acc + pos) % cycle
                    if rem_in_cycle < dash_len:
                        step_draw = min(dash_len - rem_in_cycle, seg_len - pos)
                        draw.line(
                            [
                                (sx + ux * pos, sy + uy * pos),
                                (sx + ux * (pos + step_draw), sy + uy * (pos + step_draw)),
                            ],
                            fill=color_rgba,
                            width=width,
                        )
                        pos += step_draw
                    else:
                        pos += min(cycle - rem_in_cycle, seg_len - pos)
                acc += seg_len


# ============================================================================
# 6. 视频导入与导出管线 (生成浏览器原生兼容的 H.264 MP4 并支持进度回调)
# ============================================================================


def process_video(
    input_path: str,
    output_path: str,
    cfg: BabyTrackConfig,
    max_frames: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """
    导入视频 -> 逐帧执行 BabyTrack 增效 -> 导出浏览器可直接播放的 H.264 MP4
    支持外部音乐导入合流与 FFT 频谱驱动，支持 progress_callback(done_frames, total_frames)。
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"找不到输入视频文件: {input_path}")

    processor = BabyTrackProcessor(cfg)
    ffmpeg_bin = shutil.which("ffmpeg")
    audio_src = cfg.audio_path if (cfg.audio_path and os.path.exists(cfg.audio_path)) else input_path

    if HAS_CV2:
        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频: {input_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        if max_frames is not None and total > 0:
            total = min(total, max_frames)

        audio_env: List[Tuple[float, float, float]] = []
        if cfg.audio_reactive:
            audio_env = extract_audio_envelope(audio_src, fps, max(300, total), cfg.audio_gain)

        # 若系统安装了 ffmpeg，优先使用 libx264 管道写入，保证网页 <video> 标签直接播放并合并外部音乐或原音轨
        if ffmpeg_bin:
            out_w = w if w % 2 == 0 else w - 1
            out_h = h if h % 2 == 0 else h - 1
            write_cmd = [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-f",
                "rawvideo",
                "-vcodec",
                "rawvideo",
                "-s",
                f"{w}x{h}",
                "-pix_fmt",
                "rgb24",
                "-r",
                str(fps),
                "-i",
                "-",
                "-stream_loop",
                "-1",
                "-i",
                audio_src,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0?",
                "-vf",
                f"scale={out_w}:{out_h}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "fast",
                "-movflags",
                "+faststart",
                "-c:a",
                "aac",
                "-shortest",
                output_path,
            ]
            writer_proc = subprocess.Popen(write_cmd, stdin=subprocess.PIPE)
            print(f"[BabyTrack] 处理视频 (OpenCV + H.264): {w}x{h} @ {fps:.2f}fps (共 {total} 帧)")
            idx = 0
            try:
                while True:
                    ret, frame_bgr = cap.read()
                    if not ret or (max_frames is not None and idx >= max_frames):
                        break
                    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                    cur_bands = audio_env[idx % len(audio_env)] if audio_env else (0.0, 0.0, 0.0)
                    out_rgb = processor.process_frame(frame_rgb, idx / fps, cur_bands)
                    writer_proc.stdin.write(out_rgb.tobytes())
                    idx += 1
                    if progress_callback:
                        progress_callback(idx, max(idx, total))
                    if idx % 30 == 0:
                        print(f"  -> 已处理 {idx}/{total} 帧...")
            finally:
                cap.release()
                if writer_proc.stdin:
                    writer_proc.stdin.close()
                writer_proc.wait()
            print(f"[BabyTrack] 导出完成: {output_path}")
            return

        # 无 ffmpeg 时回退到 OpenCV VideoWriter
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
        idx = 0
        while True:
            ret, frame_bgr = cap.read()
            if not ret or (max_frames is not None and idx >= max_frames):
                break
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            out_rgb = processor.process_frame(frame_rgb, idx / fps)
            writer.write(cv2.cvtColor(out_rgb, cv2.COLOR_RGB2BGR))
            idx += 1
            if progress_callback:
                progress_callback(idx, max(idx, total))
        cap.release()
        writer.release()
        return

    # 纯 FFmpeg 回退
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        raise RuntimeError("当前环境未安装 opencv-python 且未找到 ffmpeg。")

    probe_cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,nb_frames",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]
    probe_out = subprocess.check_output(probe_cmd, text=True).strip().splitlines()
    w, h = int(probe_out[0]), int(probe_out[1])
    num, den = map(int, probe_out[2].split("/"))
    fps = num / den if den != 0 else 30.0
    total = int(probe_out[3]) if len(probe_out) > 3 and probe_out[3].isdigit() else 100

    read_cmd = [
        ffmpeg_bin, "-v", "error", "-i", input_path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
    ]
    write_cmd = [
        ffmpeg_bin, "-y", "-v", "error", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{w}x{h}", "-pix_fmt", "rgb24", "-r", str(fps), "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-movflags", "+faststart",
        output_path,
    ]

    reader = subprocess.Popen(read_cmd, stdout=subprocess.PIPE)
    writer = subprocess.Popen(write_cmd, stdin=subprocess.PIPE)
    frame_bytes = w * h * 3
    idx = 0
    try:
        while True:
            if max_frames is not None and idx >= max_frames:
                break
            raw = reader.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            frame_rgb = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 3))
            out_rgb = processor.process_frame(frame_rgb, idx / fps)
            writer.stdin.write(out_rgb.tobytes())
            idx += 1
            if progress_callback:
                progress_callback(idx, max(idx, total))
    finally:
        if reader.stdout:
            reader.stdout.close()
        if writer.stdin:
            writer.stdin.close()
        reader.wait()
        writer.wait()


# ============================================================================
# 7. 内置测试视频生成器与 CLI 入口
# ============================================================================


def generate_demo_video(path: str, width: int = 640, height: int = 360, frames: int = 90, fps: int = 30) -> None:
    """生成一段带多个高光移动目标的合成测试视频"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    yy, xx = np.mgrid[0:height, 0:width]

    targets = [
        {"rx": 0.22, "ry": 0.28, "fx": 1.2, "fy": 0.9, "px": 0.0, "py": 0.5, "rad": 26},
        {"rx": 0.35, "ry": 0.32, "fx": 0.8, "fy": 1.5, "px": 1.5, "py": 2.1, "rad": 22},
        {"rx": 0.28, "ry": 0.22, "fx": 1.6, "fy": 1.1, "px": 3.1, "py": 1.0, "rad": 18},
        {"rx": 0.38, "ry": 0.35, "fx": 1.1, "fy": 1.8, "px": 4.2, "py": 3.4, "rad": 20},
        {"rx": 0.18, "ry": 0.30, "fx": 2.0, "fy": 1.3, "px": 2.0, "py": 4.8, "rad": 15},
        {"rx": 0.30, "ry": 0.25, "fx": 0.9, "fy": 2.2, "px": 5.0, "py": 0.8, "rad": 16},
    ]

    ffmpeg_bin = shutil.which("ffmpeg")
    cmd = [
        ffmpeg_bin, "-y", "-v", "error", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{width}x{height}", "-pix_fmt", "rgb24", "-r", str(fps), "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(frames):
        t = f / float(fps)
        bg_r = (20 + 20 * np.sin(xx / 90.0 + t)).astype(np.float32)
        bg_g = (25 + 20 * np.cos(yy / 70.0 + t)).astype(np.float32)
        bg_b = np.full((height, width), 45.0, dtype=np.float32)
        img = np.stack([bg_r, bg_g, bg_b], axis=-1)

        for idx, tgt in enumerate(targets):
            cx = width * (0.5 + tgt["rx"] * math.sin(tgt["fx"] * t * 2.0 + tgt["px"]))
            cy = height * (0.5 + tgt["ry"] * math.cos(tgt["fy"] * t * 2.0 + tgt["py"]))
            dist2 = (xx - cx) ** 2 + (yy - cy) ** 2
            glow = np.exp(-dist2 / (2.0 * (tgt["rad"] ** 2)))[:, :, None]
            col = np.array([255.0, 230.0 - idx * 15, 180.0 + idx * 10], dtype=np.float32)
            img = np.clip(img + glow * col, 0, 255)

        proc.stdin.write(img.astype(np.uint8).tobytes())

    proc.stdin.close()
    proc.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BabyTrack 视频斑点追踪增效工具 (Python 复刻版)")
    parser.add_argument("-i", "--input", type=str, default="", help="输入视频路径")
    parser.add_argument("-o", "--output", type=str, default="output_baby_track.mp4", help="输出视频路径")
    parser.add_argument("--shape", type=str, default="square", choices=["square", "circle", "rect", "diamond", "star"])
    parser.add_argument(
        "--style",
        type=str,
        default="l_frame",
        choices=[
            "basic", "cross", "label", "label2", "frame", "l_frame", "x_frame",
            "grid", "particle", "scope", "glow", "flip",
            "invert", "glitch", "thermal", "pixel", "tone", "blur", "dither", "zoom", "xray", "edge",
        ],
    )
    parser.add_argument(
        "--filter",
        type=str,
        default="none",
        choices=["none", "invert", "glitch", "thermal", "pixel", "tone", "blur", "dither", "zoom", "xray", "edge"],
    )
    parser.add_argument("--line-style", type=str, default="straight", choices=["straight", "curve", "snake", "lightning"])
    parser.add_argument("--line-num", type=float, default=0.5)
    parser.add_argument("--center-hub", action="store_true")
    parser.add_argument("--dashed", action="store_true")
    parser.add_argument("--color", type=str, default="#00FFCC")
    parser.add_argument("--crazy-color", action="store_true")
    parser.add_argument("--blink", action="store_true")
    parser.add_argument("--invert-mask", action="store_true")
    parser.add_argument("--by-count", action="store_true")
    parser.add_argument("--max-blobs", type=int, default=15)
    parser.add_argument("--min-size", type=float, default=64.0)
    parser.add_argument("--blob-size", type=float, default=128.0)
    parser.add_argument(
        "--text-pos",
        type=str,
        default="center",
        choices=["center", "inside_tl", "top", "bottom"],
        help="方框数值显示位置: center(框正中), inside_tl(框内左上), top(框上方), bottom(框下方)",
    )
    parser.add_argument(
        "--text-type",
        type=str,
        default="dynamic",
        choices=["random", "dynamic", "size", "position", "id_score", "count"],
        help="方框数值类型",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    in_video = args.input
    if not in_video:
        in_video = "demo_input.mp4"
        if not os.path.exists(in_video):
            generate_demo_video(in_video)

    config = BabyTrackConfig(
        shape=args.shape,
        region_style=args.style,
        filter_effect=args.filter,
        line_style=args.line_style,
        line_num=args.line_num,
        use_center_as_hub=args.center_hub,
        is_dashed_line=args.dashed,
        color=args.color,
        crazy_color=args.crazy_color,
        region_blink=args.blink,
        invert_mask=args.invert_mask,
        blob_threshold_by_count=args.by_count,
        blob_count_threshold=args.max_blobs,
        blob_size_threshold=args.min_size,
        blob_size=args.blob_size,
        text_position=args.text_pos,
        text_content_type=args.text_type,
    )

    process_video(in_video, args.output, config)
