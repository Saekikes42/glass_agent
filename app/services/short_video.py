"""Local vertical video assembly: Russian voice-over, score, captions and H.264 MP4."""
from __future__ import annotations

import asyncio
import math
import socket
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.services.images import find_font


class VideoError(RuntimeError):
    pass


def check_tts_connectivity() -> None:
    """Fail before spending AI/image limits if the server cannot reach the speech service."""
    try:
        with socket.create_connection(("speech.platform.bing.com", 443), timeout=4):
            pass
    except OSError as exc:
        raise VideoError("Для автоматической озвучки нужен исходящий HTTPS-доступ к Microsoft Edge TTS.") from exc


def _voiceover(text: str, mp3_path: Path, wav_path: Path, ffmpeg: str) -> None:
    try:
        import edge_tts

        async def synthesize() -> None:
            await asyncio.wait_for(
                edge_tts.Communicate(text, voice="ru-RU-SvetlanaNeural").save(str(mp3_path)), timeout=150
            )

        asyncio.run(synthesize())
    except Exception as exc:
        raise VideoError("Не удалось получить русскую озвучку Microsoft Edge TTS. Проверьте доступ сервера к интернету.") from exc
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3_path),
                             "-ar", "44100", "-ac", "1", str(wav_path)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode or not wav_path.is_file() or wav_path.stat().st_size < 1000:
        raise VideoError("Не удалось подготовить аудиодорожку озвучки. " + (result.stderr or "").strip()[-400:])


def _music(path: Path, duration: float) -> None:
    """Create a quiet, original ambient arpeggio without external music or API calls."""
    rate = 22050
    count = max(1, int((duration + .3) * rate))
    t = np.arange(count, dtype=np.float32) / rate
    # A gentle repeating C / Am / F / G progression with a soft plucked upper note.
    roots = np.array([130.81, 110.00, 87.31, 98.00], dtype=np.float32)
    chords = roots[np.floor(t / 3.0).astype(np.int32) % 4]
    beat = np.floor(t / .75).astype(np.int32)
    ratios = np.array([1.0, 1.25, 1.5, 2.0, 1.5, 1.25, 2.0, 1.5], dtype=np.float32)
    freq = chords * ratios[beat % len(ratios)]
    phase = np.mod(t, .75)
    pad = (np.sin(2 * math.pi * chords * t) + .55 * np.sin(2 * math.pi * chords * 1.5 * t)) * .12
    pluck = np.sin(2 * math.pi * freq * t) * np.exp(-phase * 3.4) * .20
    fade = np.minimum(1, t / .8) * np.minimum(1, np.maximum(0, duration - t) / .8)
    samples = np.clip((pad + pluck) * fade, -.95, .95)
    pcm = (samples * 32767).astype("<i2")
    stereo = np.empty(count * 2, dtype="<i2")
    stereo[0::2], stereo[1::2] = pcm, pcm
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(stereo.tobytes())


def _make_frame(source: Path, title: str, target: Path) -> None:
    with Image.open(source) as original:
        frame = ImageOps.fit(original.convert("RGB"), (1080, 1920), method=Image.Resampling.LANCZOS)
    rgba = np.zeros((1920, 1080, 4), dtype=np.uint8)
    rgba[1380:, :, 0:3] = (3, 34, 45)
    rgba[1380:, :, 3] = np.linspace(0, 190, 540, dtype=np.uint8)[:, None]
    frame = Image.alpha_composite(frame.convert("RGBA"), Image.fromarray(rgba, mode="RGBA"))
    draw = ImageDraw.Draw(frame)
    font = ImageFont.truetype(find_font(True), 66)
    words = title.strip().split()
    lines: list[str] = []
    line = ""
    for word in words:
        candidate = (line + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] > 920 and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    lines = lines[:3]
    y = 1585 - len(lines) * 86
    for value in lines:
        draw.text((76, y), value, font=font, fill="white", stroke_width=1, stroke_fill=(0, 35, 50))
        y += 88
    frame.convert("RGB").save(target, format="JPEG", quality=92, optimize=True)


def assemble(images: list[Path], titles: list[str], narration: str, output: Path,
             seconds_per_scene: int, topic: str) -> None:
    if not images or len(images) != len(titles):
        raise VideoError("Не удалось подготовить кадры ролика.")
    output.parent.mkdir(parents=True, exist_ok=True)
    narration = narration.strip() or ". ".join([topic, *titles])
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="glass-short-") as directory:
        work = Path(directory)
        voice_mp3, voice, music = work / "voice.mp3", work / "voice.wav", work / "music.wav"
        _voiceover(narration, voice_mp3, voice, ffmpeg)
        with wave.open(str(voice), "rb") as spoken:
            voice_seconds = spoken.getnframes() / spoken.getframerate()
        scene_seconds = max(seconds_per_scene, math.ceil(voice_seconds / len(images)))
        _music(music, len(images) * scene_seconds)
        frames = []
        for index, (image, title) in enumerate(zip(images, titles)):
            frame = work / f"scene-{index:02d}.jpg"
            _make_frame(image, title, frame)
            frames.append(frame)

        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for frame in frames:
            args += ["-i", str(frame)]
        voice_index, music_index = len(frames), len(frames) + 1
        args += ["-i", str(voice), "-i", str(music)]
        filters = []
        labels = []
        frames_per_scene = scene_seconds * 30
        for i in range(len(frames)):
            label = f"v{i}"
            filters.append(f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                           f"zoompan=z='min(zoom+0.00025,1.06)':d={frames_per_scene}:"
                           f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,setsar=1[{label}]")
            labels.append(f"[{label}]")
        filters.append(f"{' '.join(labels)}concat=n={len(frames)}:v=1:a=0[vout]")
        filters.append(f"[{voice_index}:a]aresample=44100,volume=1[voice]")
        filters.append(f"[{music_index}:a]volume=0.20[music]")
        filters.append("[voice][music]amix=inputs=2:duration=longest:dropout_transition=2,"
                       "loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
        args += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(output)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=max(300, len(images) * seconds_per_scene * 10))
        if result.returncode or not output.is_file() or output.stat().st_size < 10_000:
            output.unlink(missing_ok=True)
            raise VideoError("FFmpeg не собрал MP4: " + (result.stderr or result.stdout).strip()[-700:])
