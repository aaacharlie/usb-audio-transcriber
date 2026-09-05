"""Minimal OpenRouter chat client shared by per-file and session summaries."""
import time

from pipeline_config import log

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def call_llm(prompt, key, model, max_tokens=2000, timeout=240, attempts=3):
    """Send one user message and return the reply text, retrying transient errors."""
    import requests
    for attempt in range(attempts):
        try:
            response = requests.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            if attempt == attempts - 1:
                raise
            log(f"  LLM retry {attempt + 1} ({exc})")
            time.sleep(3 * (attempt + 1))


def split_windows(text, size):
    """Cut text into windows of roughly `size` characters at sentence ends."""
    windows, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            pivot = text.rfind(". ", start + size // 2, end)
            if pivot != -1:
                end = pivot + 1
        windows.append(text[start:end].strip())
        start = end
    return [window for window in windows if window]
