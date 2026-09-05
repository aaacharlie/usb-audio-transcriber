"""Summary backends: a command-line AI tool, any OpenAI-compatible server, or OpenRouter.

Transcription is always local. Summaries are optional and SUMMARY_BACKEND in
config.env decides how they are produced:

    none        no summaries
    command     pipe the prompt into a command (Codex, Claude Code, Gemini CLI,
                Hermes, ...) and read its reply; uses a subscription you have
    openai      any OpenAI-compatible HTTP server, such as Ollama on this machine
    openrouter  OpenRouter, pay per use, needs OPENROUTER_API_KEY

An empty SUMMARY_BACKEND means openrouter when a key is set, otherwise none,
which keeps older configurations behaving exactly as before.
"""
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from pipeline_config import log

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-haiku-4.5"
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
BACKENDS = ("none", "command", "openai", "openrouter")


class HttpBackend:
    """Chat-completions over HTTP; OpenRouter and OpenAI-compatible servers."""

    def __init__(self, kind, endpoint, model, key="", timeout=240, attempts=3):
        self.kind = kind
        self.endpoint = endpoint
        self.model = model
        self.key = key
        self.timeout = timeout
        self.attempts = attempts

    def describe(self):
        return f"{self.kind}:{self.model}"

    def complete(self, prompt, max_tokens=2000):
        import requests
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        for attempt in range(self.attempts):
            try:
                response = requests.post(
                    self.endpoint,
                    headers=headers,
                    json={"model": self.model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": max_tokens},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"].strip()
            except Exception as exc:
                if attempt == self.attempts - 1:
                    raise
                log(f"  LLM retry {attempt + 1} ({exc})")
                time.sleep(3 * (attempt + 1))


class CommandBackend:
    """Run a command with the prompt on stdin and read the summary back.

    The command may mention {prompt_file} (a file holding the prompt, useful
    for tools that take the prompt as an argument) and {output_file} (a file
    the tool writes its final answer to; read in preference to stdout).
    """

    kind = "command"

    def __init__(self, template, timeout=900):
        self.template = template
        self.timeout = timeout

    def describe(self):
        words = shlex.split(self.template) if self.template else []
        return "command:" + (Path(words[0]).name if words else "?")

    def complete(self, prompt, max_tokens=None):
        with tempfile.TemporaryDirectory(prefix="summary-") as directory:
            prompt_file = Path(directory) / "prompt.md"
            output_file = Path(directory) / "summary.md"
            prompt_file.write_text(prompt, encoding="utf-8")
            command = (self.template
                       .replace("{prompt_file}", shlex.quote(str(prompt_file)))
                       .replace("{output_file}", shlex.quote(str(output_file))))
            uses_stdin = "{prompt_file}" not in self.template
            try:
                result = subprocess.run(
                    ["bash", "-c", command],
                    input=prompt if uses_stdin else None,
                    capture_output=True, text=True, timeout=self.timeout, check=False,
                    cwd=directory,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"summary command timed out after {self.timeout}s") from exc
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip().splitlines()
                tail = detail[-1] if detail else "no output"
                raise RuntimeError(
                    f"summary command exited {result.returncode}: {tail}")
            text = (output_file.read_text(encoding="utf-8")
                    if "{output_file}" in self.template and output_file.exists()
                    else result.stdout)
            text = text.strip()
            if not text:
                raise RuntimeError("summary command produced no output")
            return text


def backend_choice(config):
    """The effective SUMMARY_BACKEND after the empty-means-compatible rule."""
    choice = config.get("SUMMARY_BACKEND", "").strip().lower()
    if not choice:
        return "openrouter" if config.get("OPENROUTER_API_KEY", "").strip() else "none"
    return choice


def backend_from_config(config, model_override=None):
    """Build the configured backend, or None when summaries are off or unusable."""
    choice = backend_choice(config)
    if choice == "none":
        return None
    if choice == "openrouter":
        key = config.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            return None
        model = model_override or config.get("OPENROUTER_MODEL", "").strip() \
            or DEFAULT_OPENROUTER_MODEL
        return HttpBackend("openrouter", OPENROUTER_ENDPOINT, model, key)
    if choice == "openai":
        model = model_override or config.get("LLM_MODEL", "").strip()
        if not model:
            return None
        base = (config.get("LLM_BASE_URL", "").strip() or DEFAULT_LLM_BASE_URL).rstrip("/")
        return HttpBackend("openai", f"{base}/chat/completions", model,
                           config.get("LLM_API_KEY", "").strip())
    if choice == "command":
        template = config.get("SUMMARY_COMMAND", "").strip()
        if not template:
            return None
        timeout = int(config.get("SUMMARY_COMMAND_TIMEOUT", "900").strip() or 900)
        return CommandBackend(template, timeout)
    raise ValueError(f"unknown SUMMARY_BACKEND: {choice}")


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
