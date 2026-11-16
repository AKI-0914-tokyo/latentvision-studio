#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BabyTrack After Effects 桥接追踪分析器 (ae_bridge.py)
====================================================
供 After Effects 脚本插件 (BabyTrack_AE.jsx) 调用：
1. 模式 A (--mode keyframes):
   对 AE 中选中的视频图层执行 4-连通域斑点检测 + 跨帧最近邻轨迹关联 (Trajectory Linking)，
   输出 JSON 关键帧数据，供 AE 自动生成原生可编辑的 Shape Layer 追踪框、Text Layer 动态数值与直线拓扑连线。
2. 模式 B (--mode render):
   直接调用 BabyTrack 渲染引擎输出带局部 Shader 滤镜的 H.264 MP4 并自动导入 AE 合成。
"""

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Any

import cv2
from baby_track import BabyTrackConfig, detect_blobs, process_video


def extract_ae_trajectories(
    video_path: str,
    threshold: int = 140,
    min_area: float = 65.0,
    max_targets: int = 8,
    max_box_size: float = 120.0,
    step_frames: int = 3,
    max_seconds: float = 12.0,
) -> Dict[str, Any]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频文件: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 608
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or int(fps * 10)
    max_frame_idx = min(total_frames, int(fps * max_seconds))

    cfg = BabyTrackConfig(
        threshold=threshold,
        blob_size=max_box_size,
        blob_size_threshold=min_area,
        blob_threshold_by_count=True,
        blob_count_threshold=max_targets * 2,
    )

    # 初始化固定数量的轨迹槽位，使 AE 中的每个 Shape Layer 拥有连续平滑的运动轨迹
    tracks: List[Dict[str, Any]] = [
        {
            "id": i + 1,
            "last_x": None,
            "last_y": None,
            "last_w": max_box_size * 0.7,
            "last_h": max_box_size * 0.7,
            "last_area": 100.0,
            "times": [],
            "positions": [],
            "sizes": [],
            "areas": [],
        }
        for i in range(max_targets)
    ]

    frame_idx = 0
    while frame_idx < max_frame_idx:
        ret, bgr = cap.read()
        if not ret:
            break

        if frame_idx % step_frames == 0:
            t_sec = round(frame_idx / fps, 4)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            blobs = detect_blobs(rgb, cfg)
            blobs.sort(key=lambda b: b.size, reverse=True)
            candidates = blobs[: max_targets * 2]
            used = [False] * len(candidates)

            for tr in tracks:
                best_j = -1
                if tr["last_x"] is not None and candidates:
                    best_dist = 1e9
                    for j, b in enumerate(candidates):
                        if used[j]:
                            continue
                        dist = math.hypot(b.x - tr["last_x"], b.y - tr["last_y"])
                        if dist < best_dist:
                            best_dist = dist
                            best_j = j
                    if best_dist > max(width, height) * 0.35:
                        best_j = -1

                if best_j == -1:
                    for j, b in enumerate(candidates):
                        if not used[j]:
                            best_j = j
                            break

                if best_j != -1:
                    used[best_j] = True
                    b = candidates[best_j]
                    side = min(b.size, max_box_size)
                    raw_w = max(24.0, b.max_x - b.min_x)
                    raw_h = max(24.0, b.max_y - b.min_y)
                    scale = min(1.0, (max_box_size * 1.25) / max(raw_w, raw_h, 1.0))
                    bw = round(max(28.0, min(side, raw_w * scale)), 1)
                    bh = round(max(28.0, min(side, raw_h * scale)), 1)

                    # 指数平滑过滤抖动，使 AE 关键帧丝滑自然
                    if tr["last_x"] is None:
                        sx, sy = b.x, b.y
                    else:
                        sx = tr["last_x"] * 0.35 + b.x * 0.65
                        sy = tr["last_y"] * 0.35 + b.y * 0.65

                    tr["last_x"], tr["last_y"] = sx, sy
                    tr["last_w"], tr["last_h"] = bw, bh
                    tr["last_area"] = round(b.size, 1)
                else:
                    if tr["last_x"] is None:
                        tr["last_x"] = width * (0.2 + 0.6 * ((tr["id"] * 37) % 100) / 100.0)
                        tr["last_y"] = height * (0.2 + 0.6 * ((tr["id"] * 73) % 100) / 100.0)

                tr["times"].append(t_sec)
                tr["positions"].append([round(tr["last_x"], 1), round(tr["last_y"], 1)])
                tr["sizes"].append([round(tr["last_w"], 1), round(tr["last_h"], 1)])
                tr["areas"].append(tr["last_area"])

        frame_idx += 1

    cap.release()

    return {
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "duration": round(frame_idx / fps, 3),
        "tracks": [
            {
                "id": tr["id"],
                "times": tr["times"],
                "positions": tr["positions"],
                "sizes": tr["sizes"],
                "areas": tr["areas"],
            }
            for tr in tracks
            if len(tr["times"]) > 0
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BabyTrack After Effects Bridge")
    parser.add_argument("--config", help="JSON 配置文件路径 (避免命令行中文路径编码问题)")
    parser.add_argument("--mode", choices=["keyframes", "render"], default="keyframes")
    parser.add_argument("--input", help="输入视频路径")
    parser.add_argument("--output", help="输出 JSON 或 MP4 路径")
    parser.add_argument("--threshold", type=int, default=140)
    parser.add_argument("--min-area", type=float, default=65.0)
    parser.add_argument("--max-targets", type=int, default=8)
    parser.add_argument("--max-size", type=float, default=120.0)
    parser.add_argument("--step-frames", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=12.0)
    parser.add_argument("--filter-effect", default="thermal")
    parser.add_argument("--region-style", default="l_frame")
    parser.add_argument("--shape", default="square")
    parser.add_argument("--text-type", default="dynamic")
    parser.add_argument("--text-pos", default="center")
    parser.add_argument("--color", default="#FFFFFF")
    args = parser.parse_args()

    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as cf:
            c_data = json.load(cf)
        mode = c_data.get("mode", args.mode)
        input_path = c_data.get("input", args.input)
        output_path = c_data.get("output", args.output)
        threshold = int(c_data.get("threshold", args.threshold))
        min_area = float(c_data.get("min_area", args.min_area))
        max_targets = int(c_data.get("max_targets", args.max_targets))
        max_size = float(c_data.get("max_size", args.max_size))
        step_frames = int(c_data.get("step_frames", args.step_frames))
        max_seconds = float(c_data.get("max_seconds", args.max_seconds))
        filter_effect = c_data.get("filter_effect", args.filter_effect)
        region_style = c_data.get("region_style", args.region_style)
        shape = c_data.get("shape", args.shape)
        text_type = c_data.get("text_type", args.text_type)
        text_pos = c_data.get("text_pos", args.text_pos)
        color = c_data.get("color", args.color)
    else:
        mode = args.mode
        input_path = args.input
        output_path = args.output
        threshold = args.threshold
        min_area = args.min_area
        max_targets = args.max_targets
        max_size = args.max_size
        step_frames = args.step_frames
        max_seconds = args.max_seconds
        filter_effect = args.filter_effect
        region_style = args.region_style
        shape = args.shape
        text_type = args.text_type
        text_pos = args.text_pos
        color = args.color

    if not input_path or not os.path.exists(input_path):
        fallback = "/Users/snivk/Documents/google/baby_track/demo_input.mp4"
        if os.path.exists(fallback):
            input_path = fallback

    if mode == "keyframes":
        data = extract_ae_trajectories(
            video_path=input_path,
            threshold=threshold,
            min_area=min_area,
            max_targets=max_targets,
            max_box_size=max_size,
            step_frames=step_frames,
            max_seconds=max_seconds,
        )
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print("OK_KEYFRAMES")
    else:
        cfg = BabyTrackConfig(
            threshold=threshold,
            blob_size=max_size,
            blob_size_threshold=min_area,
            blob_threshold_by_count=True,
            blob_count_threshold=max_targets,
            shape=shape,
            region_style=region_style,
            filter_effect=filter_effect,
            line_style="straight",
            line_num=0.5,
            color=color,
            show_text=True,
            text_position=text_pos,
            text_content_type=text_type,
        )
        process_video(input_path, output_path, cfg)
        print("OK_RENDER")


if __name__ == "__main__":
    main()
