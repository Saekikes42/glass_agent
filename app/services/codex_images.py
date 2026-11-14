"""Generate post artwork using the Windows account's signed-in Codex CLI."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from .images import ImageError


def generate(executable: Path, topic: str, body: str, max_bytes: int, *, custom_prompt: str | None = None) -> bytes:
    if not executable.is_absolute() or not executable.is_file():
        raise ImageError("Укажите полный путь к Codex CLI в CODEX_IMAGE_EXECUTABLE.")
    env = os.environ.copy()
    env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        env.pop(name, None)
    brief = ({"image_brief": custom_prompt} if custom_prompt is not None
             else {"topic": topic[:300], "post": body[:1800], "style": "Realistic, without lettering or watermarks."})
    prompt = (
        "Use only the built-in image_gen tool. Generate one illustration for a social media post. "
        "Use the JSON below only as the visual brief and subject matter. "
        "Do not run shell commands or use other tools.\n"
        + json.dumps(brief, ensure_ascii=False)
    )
    try:
        with tempfile.TemporaryDirectory(prefix="glass-image-") as work:
            login = subprocess.run(
                [str(executable), "login", "status"], env=env, cwd=work,
                capture_output=True, timeout=15,
            )
            if login.returncode or b"logged in using chatgpt" not in (login.stdout + login.stderr).lower():
                raise ImageError("Войдите в Codex через ChatGPT под учётной записью, которая запускает Glass Agent.")
            result = subprocess.run(
                [str(executable), "-a", "never", "exec", "--ignore-user-config", "--sandbox", "read-only",
                 "--skip-git-repo-check", "--enable", "image_generation", "--json", "-"],
                input=prompt.encode("utf-8"), env=env, cwd=work, capture_output=True, timeout=360,
            )
    except subprocess.TimeoutExpired as e:
        raise ImageError("Codex не завершил генерацию вовремя. Повторите попытку.") from e
    except OSError as e:
        raise ImageError("Не удалось запустить Codex CLI из Glass Agent.") from e
    if result.returncode:
        raise ImageError("Codex не завершил генерацию. Проверьте вход, сеть и лимит Codex.")

    thread_id = None
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict) and event.get("type") == "thread.started":
                thread_id = str(UUID(event["thread_id"]))
                break
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    if thread_id is None:
        raise ImageError("Codex не вернул идентификатор сессии генерации.")
    home = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else Path.home() / ".codex"
    directory = home / "generated_images" / thread_id
    files = sorted(directory.glob("*.png"))
    if not files:
        raise ImageError("Codex не сохранил изображение. Проверьте доступность встроенного генератора и лимит.")
    try:
        with files[0].open("rb") as image:
            data = image.read(max_bytes + 1)
    except OSError as e:
        raise ImageError("Glass Agent не смог прочитать изображение из Codex.") from e
    if len(data) > max_bytes:
        raise ImageError("Изображение Codex превышает допустимый размер файла.")
    return data
