import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "load_claude_session.py"
SPEC = importlib.util.spec_from_file_location("load_claude_session", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def write_jsonl(path: Path, rows: list[dict], malformed: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed is not None:
        lines.insert(1, malformed)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class FindSessionTests(unittest.TestCase):
    def test_finds_exact_main_session_and_ignores_subagent_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "11111111-1111-4111-8111-111111111111"
            main = root / "project-a" / f"{session_id}.jsonl"
            subagent = root / "project-a" / "other-session" / "subagents" / f"{session_id}.jsonl"
            write_jsonl(main, [{"type": "user", "message": {"content": "main"}}])
            write_jsonl(subagent, [{"type": "user", "message": {"content": "subagent"}}])

            self.assertEqual(MODULE.find_session(session_id, root), main)

    def test_rejects_ambiguous_main_session_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "22222222-2222-4222-8222-222222222222"
            for project in ("project-a", "project-b"):
                write_jsonl(
                    root / project / f"{session_id}.jsonl",
                    [{"type": "user", "message": {"content": project}}],
                )

            with self.assertRaisesRegex(MODULE.HandoffError, "多个主会话"):
                MODULE.find_session(session_id, root)

    def test_rejects_subagent_id_with_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "33333333-3333-4333-8333-333333333333"
            write_jsonl(
                root / "project-a" / "parent" / "subagents" / f"{session_id}.jsonl",
                [{"type": "user", "message": {"content": "subagent"}}],
            )

            with self.assertRaisesRegex(MODULE.HandoffError, "子 agent"):
                MODULE.find_session(session_id, root)


class BuildHandoffTests(unittest.TestCase):
    def test_extracts_context_without_tool_results_or_thinking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "44444444-4444-4444-8444-444444444444"
            transcript = root / "project-a" / f"{session_id}.jsonl"
            rows = [
                {
                    "type": "user",
                    "sessionId": session_id,
                    "cwd": "/worktrees/feature-a",
                    "gitBranch": "feature/a",
                    "timestamp": "2026-08-25T10:00:00Z",
                    "message": {"role": "user", "content": "修复分页闪烁"},
                },
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "cwd": "/worktrees/feature-a",
                    "gitBranch": "feature/a",
                    "timestamp": "2026-08-25T10:01:00Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "private chain of thought"},
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "input": {"file_path": "/worktrees/feature-a/web-chat/src/Chat.tsx"},
                            },
                            {"type": "text", "text": "已定位到滚动锚点更新时机。"},
                        ],
                    },
                },
                {
                    "type": "user",
                    "sessionId": session_id,
                    "cwd": "/worktrees/feature-a",
                    "gitBranch": "feature/a",
                    "timestamp": "2026-08-25T10:02:00Z",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool-1",
                                "content": "SECRET_TOOL_OUTPUT_SHOULD_NOT_APPEAR",
                            }
                        ],
                    },
                },
            ]
            write_jsonl(transcript, rows, malformed="{not-json")

            handoff = MODULE.build_handoff(session_id, root, tail_messages=20, max_chars=20_000)
            markdown = MODULE.render_markdown(handoff)

            self.assertEqual(handoff.cwd, "/worktrees/feature-a")
            self.assertEqual(handoff.git_branch, "feature/a")
            self.assertEqual(handoff.malformed_lines, 1)
            self.assertIn("修复分页闪烁", markdown)
            self.assertIn("已定位到滚动锚点更新时机", markdown)
            self.assertIn("Edit × 1", markdown)
            self.assertIn("/worktrees/feature-a/web-chat/src/Chat.tsx", markdown)
            self.assertNotIn("private chain of thought", markdown)
            self.assertNotIn("SECRET_TOOL_OUTPUT_SHOULD_NOT_APPEAR", markdown)

    def test_redacts_credentials_and_truncates_large_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "55555555-5555-4555-8555-555555555555"
            transcript = root / "project-a" / f"{session_id}.jsonl"
            secret_text = (
                "API_KEY=sk-super-secret\n"
                'OPENAI_API_KEY="quoted-super-secret"\n'
                "Authorization: Bearer abc.def.ghi\n"
                + "A" * 2_000
            )
            write_jsonl(
                transcript,
                [
                    {
                        "type": "user",
                        "sessionId": session_id,
                        "cwd": "/worktrees/feature-b",
                        "message": {"role": "user", "content": secret_text},
                    }
                ],
            )

            handoff = MODULE.build_handoff(session_id, root, tail_messages=20, max_chars=500)
            markdown = MODULE.render_markdown(handoff)

            self.assertNotIn("sk-super-secret", markdown)
            self.assertNotIn("quoted-super-secret", markdown)
            self.assertNotIn("abc.def.ghi", markdown)
            self.assertIn("[REDACTED]", markdown)
            self.assertIn("已截断", markdown)
            self.assertLess(len(handoff.messages[0].text), 700)

    def test_keeps_first_user_request_and_latest_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "66666666-6666-4666-8666-666666666666"
            transcript = root / "project-a" / f"{session_id}.jsonl"
            rows = [
                {
                    "type": "user",
                    "sessionId": session_id,
                    "cwd": "/worktrees/feature-c",
                    "message": {"role": "user", "content": f"request-{index}"},
                }
                for index in range(10)
            ]
            write_jsonl(transcript, rows)

            handoff = MODULE.build_handoff(session_id, root, tail_messages=3, max_chars=10_000)
            texts = [message.text for message in handoff.messages]

            self.assertEqual(texts, ["request-0", "request-7", "request-8", "request-9"])


if __name__ == "__main__":
    unittest.main()
