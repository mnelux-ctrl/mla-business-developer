"""voice/transcriber.py — Slack audio -> OpenAI Whisper transcription for Heir."""

import logging
import os
import tempfile
from typing import Optional

import httpx

import config

logger = logging.getLogger(__name__)

WHISPER_MODEL = "whisper-1"
MIN_LENGTH = 5


async def transcribe_slack_audio(
    audio_url: str,
    slack_token: str,
    download_url: str = None,
) -> tuple[Optional[str], Optional[str]]:
    if not getattr(config, "OPENAI_API_KEY", ""):
        return None, (
            "Voice transcription disabled — OPENAI_API_KEY nije postavljen u "
            "Heirovom env-u. Pošalji tekstualnu poruku umjesto glasovne."
        )

    try:
        from openai import OpenAI
    except ImportError as e:
        return None, f"openai package missing: {e}"

    tmp_path = None
    try:
        audio_bytes = None
        last_error = "Unknown download error"

        if download_url:
            audio_bytes, last_error = await _download_slack_file(download_url, slack_token)
        if not audio_bytes:
            audio_bytes, last_error = await _download_slack_file(audio_url, slack_token)

        if not audio_bytes:
            return None, last_error
        if len(audio_bytes) < 100:
            return None, f"Audio too small ({len(audio_bytes)} bytes)"

        suffix = _guess_ext(audio_url or download_url or "")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        logger.info(f"Audio downloaded: {len(audio_bytes)} bytes, transcribing...")

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        with open(tmp_path, "rb") as f:
            resp = client.audio.transcriptions.create(model=WHISPER_MODEL, file=f)

        text = resp.text.strip()
        if len(text) < MIN_LENGTH:
            return None, "Transcript too short or empty"

        logger.info(f"Transcribed ({len(text)} chars): {text[:120]}...")
        return text, None
    except Exception as e:
        logger.error(f"Transcription failed: {e}", exc_info=True)
        return None, f"Transcription error: {e}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


async def _download_slack_file(url: str, token: str) -> tuple[Optional[bytes], Optional[str]]:
    if not url:
        return None, "Empty URL"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=headers, follow_redirects=True)
            ct = r.headers.get("content-type", "")
            if r.status_code == 403:
                return None, "HTTP 403 — bot needs 'files:read' scope"
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}"
            if "text/html" in ct:
                return None, "Got HTML instead of audio — auth issue"
            if len(r.content) == 0:
                return None, "Empty response"
            return r.content, None
    except Exception as e:
        return None, f"Download error: {e}"


def _guess_ext(url: str) -> str:
    url_lower = url.lower()
    for ext in (".mp4", ".m4a", ".ogg", ".webm", ".mp3", ".wav", ".flac"):
        if ext in url_lower:
            return ext
    return ".mp4"
