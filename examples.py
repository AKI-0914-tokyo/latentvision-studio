#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BabyTrack 基础调用示例
演示如何在你自己的 Python 代码中导入视频并添加不同风格的 BabyTrack 视觉增效。
"""

import os
from baby_track import BabyTrackConfig, generate_demo_video, process_video


def main() -> None:
    input_video = "demo_input.mp4"
    if not os.path.exists(input_video):
        print("正在生成测试输入视频 demo_input.mp4 ...")
        generate_demo_video(input_video, width=640, height=360, frames=90, fps=30)

    # 预设 1: 经典 TouchDesigner 赛博战术追踪风 (L-Frame + 动态热成像滤镜 + 直线连线)
    preset_cyber_thermal = BabyTrackConfig(
        shape="square",                  # 追踪框形状: "square", "circle", "rect", "diamond"
        region_style="l_frame",          # L型四角框
        filter_effect="thermal",         # 框内叠加动态热成像 Shader 滤镜
        line_style="straight",           # 直线连接 ("straight"=直线, "curve"=曲线, "snake"=S线, "lightning"=闪电)
        line_num=0.5,                    # 连线比例
        color="#00FFCC",                 # 青色荧光主题
        stroke_width=2,
        show_text=True,
        text_position="top",
        text_content_type="position",    # 显示 X/Y 实时坐标
    )
    process_video(input_video, "preset_1_cyber_thermal.mp4", preset_cyber_thermal)

    # 预设 2: 雷达瞄准镜 + 医学 X-Ray 透视 + 中心星型虚线拓扑
    preset_radar_xray = BabyTrackConfig(
        shape="circle",
        region_style="scope",            # 雷达刻度瞄准镜
        filter_effect="xray",            # 框内 X-Ray 骨骼透视高对比效果
        use_center_as_hub=True,          # 从屏幕中心向所有斑点发射连线
        is_dashed_line=True,             # 虚线连接
        dashed_line_size=6,
        dashed_line_gap=4,
        color="#FFCC00",
        stroke_width=2,
        show_text=True,
        text_position="bottom",
        text_content_type="random",      # 伪随机浮点识别码 ((i+1)/7)
    )
    process_video(input_video, "preset_2_radar_xray.mp4", preset_radar_xray)

    # 预设 3: 自适应外接矩形 + 目标检测标签栏 + Glitch 故障错位 + 闪电连线
    preset_glitch_label = BabyTrackConfig(
        shape="rect",                    # 使用连通域真实外接矩形 [minX, minY, maxX, maxY]
        region_style="label",            # 顶部 Object N 反色标签栏
        filter_effect="glitch",          # 框内水平扫描线位移 + RGB 色散故障
        line_style="lightning",          # 6段抖动闪电折线
        line_num=0.75,
        crazy_color=True,                # 随机高饱和 HSL 炫彩
        region_blink=True,               # 300ms 周期伪随机闪烁
        blob_threshold_by_count=True,    # 按数量排序取前 N 大斑点
        blob_count_threshold=8,
    )
    process_video(input_video, "preset_3_glitch_label.mp4", preset_glitch_label)


if __name__ == "__main__":
    main()
