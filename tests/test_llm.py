import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import llm


class BackendSelectionTests(unittest.TestCase):
    def test_empty_setting_keeps_the_old_openrouter_behaviour(self):
        backend = llm.backend_from_config({"OPENROUTER_API_KEY": "key"})

        self.assertIsInstance(backend, llm.HttpBackend)
        self.assertEqual(backend.kind, "openrouter")
        self.assertEqual(backend.describe(), "openrouter:anthropic/claude-haiku-4.5")
        self.assertEqual(backend.endpoint, llm.OPENROUTER_ENDPOINT)
        self.assertIsNone(llm.backend_from_config({}))
        self.assertIsNone(llm.backend_from_config({"SUMMARY_BACKEND": "none",
                                                   "OPENROUTER_API_KEY": "key"}))

    def test_openai_backend_targets_ollama_by_default(self):
        backend = llm.backend_from_config({"SUMMARY_BACKEND": "openai", "LLM_MODEL": "llama3.1:8b"})

        self.assertEqual(backend.kind, "openai")
        self.assertEqual(backend.endpoint, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(backend.key, "")
        custom = llm.backend_from_config({"SUMMARY_BACKEND": "openai", "LLM_MODEL": "m",
                                          "LLM_BASE_URL": "https://llm.example/v1/",
                                          "LLM_API_KEY": "secret"})
        self.assertEqual(custom.endpoint, "https://llm.example/v1/chat/completions")
        self.assertEqual(custom.key, "secret")
        self.assertIsNone(llm.backend_from_config({"SUMMARY_BACKEND": "openai"}))

    def test_command_backend_needs_a_command(self):
        backend = llm.backend_from_config({"SUMMARY_BACKEND": "command",
                                           "SUMMARY_COMMAND": "codex exec -",
                                           "SUMMARY_COMMAND_TIMEOUT": "30"})

        self.assertIsInstance(backend, llm.CommandBackend)
        self.assertEqual(backend.timeout, 30)
        self.assertEqual(backend.describe(), "command:codex")
        self.assertIsNone(llm.backend_from_config({"SUMMARY_BACKEND": "command"}))

    def test_model_override_and_unknown_backend(self):
        backend = llm.backend_from_config({"OPENROUTER_API_KEY": "key"}, model_override="big/model")
        self.assertEqual(backend.model, "big/model")
        with self.assertRaises(ValueError):
            llm.backend_from_config({"SUMMARY_BACKEND": "carrier-pigeon"})


class CommandBackendTests(unittest.TestCase):
    def script(self, root, body):
        path = root / "tool.sh"
        path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_prompt_goes_in_on_stdin_and_the_reply_comes_from_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = self.script(Path(directory), "tr a-z A-Z\n")

            self.assertEqual(llm.CommandBackend(str(tool)).complete("hello there"), "HELLO THERE")

    def test_output_file_wins_over_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = self.script(Path(directory), 'echo "progress noise"; cat > "$1"\n')

            reply = llm.CommandBackend(f"{tool} {{output_file}}").complete("the summary")

            self.assertEqual(reply, "the summary")

    def test_prompt_file_is_available_for_argument_style_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = self.script(Path(directory), 'cat "$1"\n')

            reply = llm.CommandBackend(f"{tool} {{prompt_file}}").complete("from a file")

            self.assertEqual(reply, "from a file")

    def test_failures_are_reported_with_the_last_error_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failing = self.script(root, 'echo "not signed in" >&2; exit 3\n')
            with self.assertRaisesRegex(RuntimeError, "exited 3: not signed in"):
                llm.CommandBackend(str(failing)).complete("x")
            silent = self.script(root, "exit 0\n")
            with self.assertRaisesRegex(RuntimeError, "no output"):
                llm.CommandBackend(str(silent)).complete("x")
            slow = self.script(root, "sleep 5\n")
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                llm.CommandBackend(str(slow), timeout=1).complete("x")


class HttpBackendTests(unittest.TestCase):
    def fake_requests(self, responses):
        calls = []

        def post(url, headers=None, json=None, timeout=None):
            calls.append({"url": url, "headers": headers, "json": json})
            outcome = responses.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            reply = mock.Mock()
            reply.raise_for_status = lambda: None
            reply.json = lambda: {"choices": [{"message": {"content": outcome}}]}
            return reply

        return types.SimpleNamespace(post=post), calls

    def test_posts_a_chat_completion_with_the_key(self):
        fake, calls = self.fake_requests(["  Summary text  "])
        backend = llm.HttpBackend("openrouter", llm.OPENROUTER_ENDPOINT, "m/x", key="k")
        with mock.patch.dict(sys.modules, {"requests": fake}):
            self.assertEqual(backend.complete("prompt", max_tokens=99), "Summary text")

        self.assertEqual(calls[0]["url"], llm.OPENROUTER_ENDPOINT)
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer k")
        self.assertEqual(calls[0]["json"]["model"], "m/x")
        self.assertEqual(calls[0]["json"]["max_tokens"], 99)
        self.assertEqual(calls[0]["json"]["messages"][0]["content"], "prompt")

    def test_no_authorization_header_without_a_key_and_retries_once_then_gives_up(self):
        fake, calls = self.fake_requests([RuntimeError("down"), "ok"])
        backend = llm.HttpBackend("openai", "http://localhost/v1/chat/completions", "llama")
        with mock.patch.dict(sys.modules, {"requests": fake}), \
                mock.patch.object(llm.time, "sleep"):
            self.assertEqual(backend.complete("p"), "ok")
        self.assertNotIn("Authorization", calls[0]["headers"])
        self.assertEqual(len(calls), 2)

        fake, _calls = self.fake_requests([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
        with mock.patch.dict(sys.modules, {"requests": fake}), \
                mock.patch.object(llm.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "c"):
                backend.complete("p")


if __name__ == "__main__":
    unittest.main()
