#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BabyTrack Web 视频增效平台 (Flask 服务端)
=========================================
支持上传视频 -> 配置方框数值/样式/滤镜/直线连线 -> 后台异步渲染并实时推送进度 -> 在线预览与下载 MP4。
"""

import base64
import os
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from baby_track import BabyTrackConfig, generate_demo_video, process_video

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 最大支持 500MB 视频上传
app.config["TEMPLATES_AUTO_RELOAD"] = True

# 内存任务状态表
JOBS: Dict[str, Dict[str, Any]] = {}


def _run_video_job(job_id: str, input_path: str, output_filename: str, cfg: BabyTrackConfig) -> None:
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    JOBS[job_id]["status"] = "processing"
    start_ts = time.time()

    def on_progress(done: int, total: int) -> None:
        pct = int(round((done / max(1, total)) * 100))
        JOBS[job_id]["done_frames"] = done
        JOBS[job_id]["total_frames"] = total
        JOBS[job_id]["progress"] = min(99, max(1, pct))

    try:
        process_video(input_path, output_path, cfg, progress_callback=on_progress)
        downloads_dir = os.path.expanduser("~/Downloads")
        if os.path.isdir(downloads_dir):
            try:
                shutil.copyfile(output_path, os.path.join(downloads_dir, output_filename))
            except Exception:
                pass
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["elapsed"] = round(time.time() - start_ts, 2)
        JOBS[job_id]["output_url"] = f"/outputs/{output_filename}"
        JOBS[job_id]["download_url"] = f"/download/{output_filename}"
        JOBS[job_id]["filename"] = output_filename
    except Exception as exc:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(exc)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/demo_input.mp4")
def serve_demo_input():
    return send_from_directory(BASE_DIR, "demo_input.mp4")


@app.route("/LatentVision_AppIcon.png")
def serve_app_icon():
    return send_from_directory(BASE_DIR, "LatentVision_AppIcon.png")


@app.route("/api/process", methods=["POST"])
def api_process():
    use_demo = request.form.get("use_demo", "false") == "true"
    uploaded_file = request.files.get("video")
    uploaded_audio = request.files.get("audio")

    job_id = uuid.uuid4().hex[:10]
    audio_path = None
    if uploaded_audio and uploaded_audio.filename:
        a_ext = os.path.splitext(secure_filename(uploaded_audio.filename))[1] or ".mp3"
        audio_filename = f"aud_{job_id}{a_ext}"
        audio_path = os.path.join(UPLOAD_DIR, audio_filename)
        uploaded_audio.save(audio_path)

    if uploaded_file and uploaded_file.filename:
        ext = os.path.splitext(secure_filename(uploaded_file.filename))[1] or ".mp4"
        input_filename = f"in_{job_id}{ext}"
        input_path = os.path.join(UPLOAD_DIR, input_filename)
        uploaded_file.save(input_path)
        input_url = f"/uploads/{input_filename}"
    elif use_demo:
        demo_path = os.path.join(BASE_DIR, "demo_input.mp4")
        if not os.path.exists(demo_path):
            generate_demo_video(demo_path)
        input_filename = f"demo_{job_id}.mp4"
        input_path = os.path.join(UPLOAD_DIR, input_filename)
        if not os.path.exists(input_path):
            import shutil
            shutil.copyfile(demo_path, input_path)
        input_url = f"/uploads/{input_filename}"
    else:
        return jsonify({"error": "请先选择要上传的视频文件，或点击使用内置演示视频"}), 400

    # 解析前端配置参数
    def get_bool(key: str, default: bool = False) -> bool:
        val = request.form.get(key)
        if val is None:
            return default
        return val.lower() in ("true", "1", "yes", "on")

    roi_raw = request.form.get("roi_rect", "").strip()
    roi_tuple = None
    if roi_raw:
        try:
            parts = [float(x) for x in roi_raw.split(",")]
            if len(parts) == 4:
                roi_tuple = (parts[0], parts[1], parts[2], parts[3])
        except ValueError:
            roi_tuple = None

    cfg = BabyTrackConfig(
        threshold=int(float(request.form.get("threshold", 128))),
        blob_size=float(request.form.get("blob_size", 128)),
        blob_size_threshold=float(request.form.get("blob_size_threshold", 64)),
        blob_count_threshold=int(float(request.form.get("blob_count_threshold", 15))),
        blob_threshold_by_count=get_bool("blob_threshold_by_count", False),
        same_size_enabled=get_bool("same_size_enabled", False),
        frame_skip=int(float(request.form.get("frame_skip", 0))),
        single_tracking_mode=get_bool("single_tracking_mode", False),
        show_box=get_bool("show_box", True),
        shape=request.form.get("shape", "square"),
        region_style=request.form.get("region_style", "l_frame"),
        filter_effect=request.form.get("filter_effect", "none"),
        invert_mask=get_bool("invert_mask", False),
        region_blink=get_bool("region_blink", False),
        line_style=request.form.get("line_style", "straight"),
        line_num=float(request.form.get("line_num", 0.5)),
        use_center_as_hub=get_bool("use_center_as_hub", False),
        is_dashed_line=get_bool("is_dashed_line", False),
        color=request.form.get("color", "#FFFFFF"),
        crazy_color=get_bool("crazy_color", False),
        stroke_width=max(1, int(round(float(request.form.get("stroke_width", 2))))),
        show_text=get_bool("show_text", True),
        text_position=request.form.get("text_position", "center"),
        text_content_type=request.form.get("text_content_type", "dynamic"),
        font_size=int(float(request.form.get("font_size", 13))),
        roi_rect=roi_tuple,
        audio_path=audio_path,
        audio_reactive=get_bool("audio_reactive", False),
        audio_gain=float(request.form.get("audio_gain", 1.0)),
        audio_flash=get_bool("audio_flash", True),
        audio_chromatic=get_bool("audio_chromatic", True),
        audio_bass_zoom=get_bool("audio_bass_zoom", True),
        audio_box_bounce=get_bool("audio_box_bounce", True),
        audio_line_wave=get_bool("audio_line_wave", True),
        audio_spectrum_hud=get_bool("audio_spectrum_hud", True),
    )

    output_filename = f"babytrack_{job_id}.mp4"
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "done_frames": 0,
        "total_frames": 0,
        "input_url": input_url,
        "output_url": None,
        "download_url": None,
        "error": None,
    }

    worker = threading.Thread(
        target=_run_video_job,
        args=(job_id, input_path, output_filename, cfg),
        daemon=True,
    )
    worker.start()

    return jsonify({"job_id": job_id, "input_url": input_url})


@app.route("/api/status/<job_id>")
def api_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(job)


@app.route("/uploads/<path:filename>")
def serve_upload(filename: str):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/outputs/<path:filename>")
def serve_output(filename: str):
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/download/<path:filename>")
def download_output(filename: str):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"✨ LatentVision Studio (潜像视觉工作台) 已启动: http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
