"""Real API probe for the 24 core Omnimodal models.

Raw probe JSON is saved under ~/.omnimodal/probes. The public summary is
written to test-results/probe-summary.json and never contains API keys,
media bodies, or full base64 content.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

from omnimodal import api, audio_processing
from omnimodal.config import api_key, base_url, probe_dir
from omnimodal.http import http_client
from omnimodal.image_processing import prepare_image
from omnimodal.providers.openai_compatible import OpenAICompatibleProvider
from omnimodal.video_processing import analyze_video

IMAGE_PROBE = "extract all visible text from this image; include exact wording and no commentary"
VIDEO_PROBE = "describe what happens in the video, including visible text"
AUDIO_PROBE = "transcribe or describe the audio content"
AUDIO_SAMPLE_URL = "https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3"


def _make_media(tmp: Path) -> dict[str, Path]:
    image_path = tmp / "probe.png"
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "OMNIMODAL 3.0 PROBE", fill="black")
    draw.text((20, 60), "Hello Qwen", fill="black")
    image.save(image_path)

    audio_path = tmp / "probe.mp3"
    try:
        with http_client.stream("GET", AUDIO_SAMPLE_URL, timeout=60) as response:
            response.raise_for_status()
            with audio_path.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
    except Exception:
        audio_path = tmp / "probe.wav"
        sample_rate = 16000
        frames = bytearray()
        for i in range(sample_rate):
            value = int(12000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            frames.extend(struct.pack("<h", value))
        with wave.open(str(audio_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(frames)

    video_path = tmp / "probe.mp4"
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-t",
                "3",
                "-r",
                "25",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except Exception:
        video_path = image_path
    return {"image": image_path, "audio": audio_path, "video": video_path}


def _probe_image(model: str, media: dict[str, Path]) -> dict[str, Any]:
    provider = OpenAICompatibleProvider(base_url(), model)
    image_bytes, mime = prepare_image(str(media["image"]))
    started = time.monotonic()
    result = provider.call_image(image_bytes, IMAGE_PROBE, "quick", mime_type=mime)
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "result_preview": result[:800],
    }


def _probe_video(model: str, media: dict[str, Path]) -> dict[str, Any]:
    provider = OpenAICompatibleProvider(base_url(), model)
    previous = api.default_client
    api.default_client = api.VisionClient(provider=provider)
    started = time.monotonic()
    try:
        result = analyze_video(str(media["video"]), VIDEO_PROBE, "quick")
    finally:
        api.default_client = previous
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "result_preview": result[:800],
    }


def _probe_audio_chat(model: str, media: dict[str, Path]) -> dict[str, Any]:
    provider = OpenAICompatibleProvider(base_url(), model)
    payload = base64.b64encode(media["audio"].read_bytes()).decode("ascii")
    data_url = f"data:audio/wav;base64,{payload}"
    started = time.monotonic()
    result = provider.call_audio(data_url, AUDIO_PROBE, "quick")
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "result_preview": result[:800],
    }


def _probe_asr(model: str, media: dict[str, Path]) -> dict[str, Any]:
    started = time.monotonic()
    result = audio_processing.transcribe_audio(str(media["audio"]), model=model)
    text = result.get("text", "")
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "result_preview": text[:800],
    }


def _probe_image_generation(
    model: str,
    tmp: Path,
    edit_image: Path | None = None,
) -> dict[str, Any]:
    from omnimodal.generation import GenerationSpec
    from omnimodal.mcp.generation_server import T2I_CHAT_ENDPOINT, _generate_image_chat_sync

    spec = GenerationSpec(
        endpoint=T2I_CHAT_ENDPOINT,
        model=model,
        timeout_sec=300,
        price_hint=0.0,
    )
    started = time.monotonic()
    saved = _generate_image_chat_sync(
        "a simple red circle on a white background",
        spec,
        "1024*1024",
        1,
        str(edit_image) if edit_image else None,
    )
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "files": [str(path) for path in saved],
    }


def _probe_tts(model: str, tmp: Path) -> dict[str, Any]:
    from omnimodal.config import generation_output_dir
    from omnimodal.generation import GenerationClient, GenerationSpec
    from omnimodal.http import http_client
    from omnimodal.mcp.generation_server import _create_custom_voice, _tts_request

    spec = GenerationSpec(
        endpoint="https://dashscope.aliyuncs.com/api/v1",
        model=model,
        timeout_sec=300,
        price_hint=0.0,
    )
    voice = "auto"
    if model.lower().startswith("cosyvoice"):
        voice = _create_custom_voice(
            "沉稳的中年男性，音色低沉浑厚",
            model,
        )
    endpoint, payload = _tts_request(
        model,
        "你好，这是一次 Omnimodal 语音生成测试。",
        voice,
    )
    started = time.monotonic()
    response = http_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if response.status_code >= 400:
        return {
            "ok": False,
            "elapsed_sec": round(time.monotonic() - started, 2),
            "error": response.text[:300],
        }
    parsed = response.json()
    audio_url = parsed.get("output", {}).get("audio", {}).get("url")
    if not audio_url:
        return {"ok": False, "error": "missing audio url"}
    client = GenerationClient(spec, output_dir=str(generation_output_dir()))
    saved = client.download_result(audio_url, f"probe-{model}-{int(time.time())}.mp3")
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "files": [str(saved)],
    }


def _probe_video_generation(
    model: str,
    kind: str,
    media: dict[str, Path],
    tmp: Path,
) -> dict[str, Any]:
    from omnimodal.generation import GenerationClient, GenerationSpec
    from omnimodal.mcp.generation_server import (
        T2V_ENDPOINT,
        _download,
        _media_source_for_generation,
    )

    if kind == "video_generation_i2v":
        first_frame = _media_source_for_generation(
            str(media["image"]),
            model,
            "image/png",
        )
        input_payload = {
            "prompt": "让图片中的场景缓缓移动，镜头稳定，画面保持真实自然",
            "media": [{"type": "first_frame", "url": first_frame}],
        }
    elif kind == "video_editing":
        source = _media_source_for_generation(
            str(media["video"]),
            model,
            "video/mp4",
        )
        input_payload = {
            "prompt": "将整个画面转换为黏土动画风格，保持主体和布局不变",
            "media": [{"type": "video", "url": source}],
        }
    else:
        input_payload = {
            "prompt": "一个红色小球在白色桌面上缓缓滚动，镜头稳定，画质清晰",
        }

    spec = GenerationSpec(
        endpoint=T2V_ENDPOINT,
        model=model,
        poll_interval=10,
        timeout_sec=1800,
        price_hint=0.0,
    )
    payload = {
        "model": model,
        "input": input_payload,
        "parameters": {
            "duration": 3,
            "resolution": "720P",
            "ratio": "16:9",
            "prompt_extend": True,
            "watermark": False,
        },
    }
    client = GenerationClient(
        spec,
        output_dir=str(probe_dir().parent / "outputs"),
        extra_headers={"X-DashScope-OssResourceResolve": "enable"},
    )
    started = time.monotonic()
    task_id = client.submit(payload)
    data = client.wait_for_result(task_id)
    video_url = data.get("output", {}).get("video_url")
    if not video_url:
        return {
            "ok": False,
            "elapsed_sec": round(time.monotonic() - started, 2),
            "error": "missing video_url",
        }
    saved = _download(client, video_url)
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "files": [str(saved)],
    }


def _probe_custom_voice(
    kind: str,
    media: dict[str, Path],
    tmp: Path,
) -> dict[str, Any]:
    from omnimodal.mcp.generation_server import (
        QWEN_TTS_VC_MODEL,
        QWEN_TTS_VD_MODEL,
        _create_custom_voice,
        _submit_audio_sync,
        _tts_request,
    )

    started = time.monotonic()
    if kind == "voice_clone":
        target = QWEN_TTS_VC_MODEL
        voice_id = _create_custom_voice(
            "",
            target,
            audio=str(media["audio"]),
            model="qwen-voice-enrollment",
        )
        speech_text = "这是一次声音克隆测试，欢迎收听。"
        endpoint, payload = _tts_request(target, speech_text, voice_id)
    else:
        target = QWEN_TTS_VD_MODEL
        voice_id = _create_custom_voice(
            "温柔年轻女性，清晰自然，语速适中",
            target,
            model="qwen-voice-design",
        )
        speech_text = "这是一次声音设计测试，欢迎收听。"
        endpoint, payload = _tts_request(target, speech_text, voice_id)
    saved = _submit_audio_sync(endpoint, payload, target)
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "files": [str(saved)],
    }


def _probe_music_generation(tmp: Path) -> dict[str, Any]:
    from omnimodal.mcp.generation_server import (
        MUSIC_ENDPOINT,
        _music_payload,
        _submit_audio_sync,
    )

    started = time.monotonic()
    saved = _submit_audio_sync(
        MUSIC_ENDPOINT,
        _music_payload("夏日清新民谣，木吉他与口琴伴奏，节奏轻快"),
        "fun-music-v1",
    )
    return {
        "ok": True,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "files": [str(saved)],
    }


MODEL_PROBES: list[dict[str, Any]] = [
    {"model": "qwen3.7-flash", "kind": "image_video"},
    {"model": "qwen3.7-plus", "kind": "image_video"},
    {"model": "qwen3.7-max", "kind": "image_video"},
    {"model": "qwen3.5-ocr", "kind": "image_ocr"},
    {"model": "qwen3.5-omni-flash", "kind": "image_audio_video"},
    {"model": "qwen3.5-omni-plus", "kind": "image_audio_video"},
    {"model": "fun-asr", "kind": "asr"},
    {"model": "paraformer-v2", "kind": "asr"},
    {"model": "qwen3-asr-flash", "kind": "asr"},
    {"model": "qwen-image-3.0", "kind": "image_generation"},
    {"model": "qwen-image-3.0-pro", "kind": "image_generation"},
    {"model": "wan2.7-image-pro", "kind": "image_generation"},
    {"model": "qwen-image-edit-max", "kind": "image_editing"},
    {"model": "wan2.7-t2v", "kind": "video_generation"},
    {"model": "wan2.7-i2v", "kind": "video_generation_i2v"},
    {"model": "wan2.7-videoedit", "kind": "video_editing"},
    {"model": "happyhorse-1.1-t2v", "kind": "video_generation"},
    {"model": "happyhorse-1.1-i2v", "kind": "video_generation_i2v"},
    {"model": "qwen-audio-3.0-tts-flash", "kind": "tts"},
    {"model": "qwen3-tts-instruct-flash", "kind": "tts"},
    {"model": "cosyvoice-v3.5-plus", "kind": "tts"},
    {"model": "qwen-voice-enrollment", "kind": "voice_clone"},
    {"model": "qwen-voice-design", "kind": "voice_design"},
    {"model": "fun-music-v1", "kind": "music_generation"},
]


def _run_one(item: dict[str, Any], media: dict[str, Path], tmp: Path) -> dict[str, Any]:
    model = item["model"]
    kind = item["kind"]
    started = time.monotonic()
    try:
        if kind == "image_ocr":
            result = _probe_image(model, media)
        elif kind == "image_audio_video":
            result = {
                "image": _probe_image(model, media),
                "video": _probe_video(model, media),
                "audio": _probe_audio_chat(model, media),
            }
        elif kind == "image":
            result = _probe_image(model, media)
        elif kind == "image_video":
            result = {
                "image": _probe_image(model, media),
                "video": _probe_video(model, media),
            }
        elif kind == "video":
            result = _probe_video(model, media)
        elif kind == "audio":
            result = _probe_audio_chat(model, media)
        elif kind == "asr":
            result = _probe_asr(model, media)
        elif kind == "image_generation":
            result = _probe_image_generation(model, tmp)
        elif kind == "image_editing":
            result = _probe_image_generation(model, tmp, edit_image=media["image"])
        elif kind == "tts":
            result = _probe_tts(model, tmp)
        elif kind in {"video_generation", "video_generation_i2v", "video_editing"}:
            result = _probe_video_generation(model, kind, media, tmp)
        elif kind in {"voice_clone", "voice_design"}:
            result = _probe_custom_voice(kind, media, tmp)
        elif kind == "music_generation":
            result = _probe_music_generation(tmp)
        else:
            result = {
                "ok": False,
                "error": f"probe kind not implemented in this run: {kind}",
            }
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict) and "ok" not in result:
        nested = [value for value in result.values() if isinstance(value, dict) and "ok" in value]
        if nested:
            result["ok"] = all(value.get("ok") is True for value in nested)
    result["model"] = model
    result["kind"] = kind
    result["total_elapsed_sec"] = round(time.monotonic() - started, 2)
    return result


def _summary_ok(item: dict[str, Any]) -> bool | None:
    if "ok" in item:
        return item["ok"]
    nested = [value for value in item.values() if isinstance(value, dict) and "ok" in value]
    if nested:
        return all(value.get("ok") is True for value in nested)
    return None


def _summary_preview(item: dict[str, Any]) -> str | None:
    preview = item.get("result_preview")
    if preview:
        return preview
    nested = [
        value for value in item.values() if isinstance(value, dict) and value.get("result_preview")
    ]
    if nested:
        return str(nested[0]["result_preview"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real Omnimodal model probes.")
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated model IDs to probe; default is all 24.",
    )
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Skip image/video/audio generation probes.",
    )
    parser.add_argument(
        "--merge-summary",
        action="store_true",
        help="Merge existing raw probes into probe-summary.json without rerunning.",
    )
    args = parser.parse_args()

    output_dir = probe_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.merge_summary:
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for probe_path in sorted(output_dir.glob("probe-*.json")):
            try:
                raw_items = json.loads(probe_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                key = (str(item.get("model")), str(item.get("kind")))
                latest[key] = item
        summary = [
            {
                "model": item["model"],
                "kind": item["kind"],
                "ok": _summary_ok(item),
                "elapsed_sec": item.get("total_elapsed_sec"),
                "result_preview": _summary_preview(item),
                "files_count": len(item.get("files") or []),
                "error": item.get("error"),
            }
            for item in latest.values()
        ]
        summary_dir = Path(__file__).resolve().parents[1] / "test-results"
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "probe-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Merged {len(summary)} probe results into probe-summary.json")
        return 0

    selected = MODEL_PROBES
    if args.only:
        allowed = {item.strip() for item in args.only.split(",") if item.strip()}
        selected = [item for item in MODEL_PROBES if item["model"] in allowed]
    if args.skip_generation:
        selected = [
            item
            for item in selected
            if item["kind"]
            not in {
                "image_generation",
                "image_editing",
                "video_generation",
                "video_generation_i2v",
                "video_editing",
                "tts",
                "voice_clone",
                "voice_design",
                "music_generation",
            }
        ]

    with tempfile.TemporaryDirectory(prefix="omnimodal-probe-") as raw_tmp:
        tmp = Path(raw_tmp)
        media = _make_media(tmp)
        results: list[dict[str, Any]] = []
        for item in selected:
            print(f"PROBE {item['model']} ({item['kind']})")
            result = _run_one(item, media, tmp)
            results.append(result)
            print("  ok:", result.get("ok"), "elapsed:", result.get("total_elapsed_sec"))

        raw_path = output_dir / f"probe-{int(time.time())}.json"
        raw_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        summary = [
            {
                "model": item["model"],
                "kind": item["kind"],
                "ok": item.get("ok"),
                "elapsed_sec": item.get("total_elapsed_sec"),
                "result_preview": item.get("result_preview"),
                "files": item.get("files"),
                "error": item.get("error"),
            }
            for item in results
        ]
        summary_dir = Path(__file__).resolve().parents[1] / "test-results"
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "probe-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Raw probe saved: {raw_path}")
        print(f"Summary saved: {summary_dir / 'probe-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
