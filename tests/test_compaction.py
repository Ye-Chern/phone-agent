import sqlite3
import tempfile
import unittest
import hashlib
import json
from pathlib import Path

from phoneagent_core import compaction, storage
from phoneagent_core.quota import estimate_tokens


def make_group(i, text, *, tool=False, complete=True):
    messages = [{"role": "user", "content": f"round {i}: {text}"}]
    if tool:
        messages.extend([
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"call-{i}", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"call-{i}", "content": f"tool output {i}: {text}"},
            {"role": "assistant", "content": f"tool result processed {text}"},
        ])
    else:
        messages.append({"role": "assistant", "content": f"acknowledged {text}"})
    return {"start_id": i * 10, "end_id": i * 10 + len(messages) - 1,
            "messages": messages, "complete": complete}


def make_tool_subgroup(i, *, complete=True, text=None):
    text = text or ("tool output detail " + str(i) + " ") * 12
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": f"loop-{i}", "type": "function",
         "function": {"name": "read_file", "arguments": json_text(i)}}]},
        {"role": "tool", "tool_call_id": f"loop-{i}", "content": text},
        {"role": "assistant", "content": "Completed tool batch " + str(i)},
    ]
    return {"start_id": i * 10, "end_id": i * 10 + 2, "messages": messages,
            "complete": complete, "current": True, "task_source_hash": "active-task"}


def source_user_group(message, start_id, *, current, task_hash=None):
    task_hash = task_hash or hashlib.sha256(message["content"].encode("utf-8")).hexdigest()
    return {"start_id": start_id, "end_id": start_id, "messages": [], "source_user": message,
            "complete": True, "current": current, "task_source_hash": task_hash}


def json_text(value):
    return '{"batch":' + str(value) + '}'


class WorkCompactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "work.sqlite"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE conversations(id TEXT PRIMARY KEY)")
        self.conn.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, conv_id TEXT, role TEXT, content TEXT)")
        self.conn.execute("INSERT INTO conversations(id) VALUES('c1')")
        storage.migrate(self.conn)
        compaction.migrate(self.conn)
        self.info = {"id": "c1", "settings": {"work_context_budget": 2048}}

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def make_history(self, n=12):
        return [make_group(i + 1, ("long fact " + str(i) + " ") * 12,
                           tool=(i == n - 2)) for i in range(n)]

    def summarizer(self, calls=None, result="Compact factual notes: task, constraints, actual results, unknowns, next steps."):
        def run(prompt):
            if calls is not None:
                calls.append(prompt)
            return result
        return run

    def test_compacts_prefix_and_keeps_recent_tool_group_whole(self):
        groups = self.make_history()
        messages, meta = compaction.context(self.conn, self.info, groups, "fixed system", "model-a",
                                            self.summarizer())
        self.assertEqual(meta["compaction"]["status"], "compacted")
        self.assertLessEqual(meta["estimated_input_tokens"], 2048)
        self.assertEqual(messages[0], {"role": "system", "content": "fixed system"})
        tail = groups[-2:]
        self.assertEqual(messages[-len([m for g in tail for m in g["messages"]]):],
                         [m for g in tail for m in g["messages"]])
        tool_group = next(g for g in tail if any(m["role"] == "tool" for m in g["messages"]))
        self.assertIn(next(m for m in tool_group["messages"] if m["role"] == "tool"), messages)
        self.assertIn(compaction.SUMMARY_LABEL, messages[1]["content"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM work_compaction_checkpoints").fetchone()[0], 1)

    def test_checkpoint_reuses_after_reopen_and_incrementally_extends(self):
        groups = self.make_history()
        _, first = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer())
        checkpoint_id = first["compaction"]["checkpoint_id"]
        self.conn.commit()
        self.conn.close()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        calls = []
        _, reused = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer(calls))
        self.assertEqual(reused["compaction"]["status"], "reused")
        self.assertEqual(reused["compaction"]["checkpoint_id"], checkpoint_id)
        self.assertEqual(calls, [])
        extended = groups + [make_group(13, "new status " * 12), make_group(14, "current question")]
        _, second = compaction.context(self.conn, self.info, extended, "sys", "model-a", self.summarizer(calls))
        self.assertIn(second["compaction"]["status"], {"compacted", "reused"})
        if second["compaction"]["status"] == "compacted":
            self.assertTrue(second["compaction"]["incremental"])
            self.assertTrue(calls)

    def test_changed_source_hash_does_not_reuse_branch_checkpoint(self):
        groups = self.make_history()
        _, first = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer())
        self.assertEqual(first["compaction"]["status"], "compacted")
        drifted = self.make_history()
        drifted[0] = make_group(1, "branched content " * 12)
        calls = []
        _, after_drift = compaction.context(self.conn, self.info, drifted, "sys", "model-a", self.summarizer(calls))
        self.assertEqual(after_drift["compaction"]["status"], "compacted")
        self.assertFalse(after_drift["compaction"]["incremental"])
        self.assertTrue(calls)

    def test_summarizer_failure_writes_no_checkpoint_and_falls_back(self):
        def fail(_prompt):
            raise RuntimeError("offline/quota error")
        groups = self.make_history()
        _, meta = compaction.context(self.conn, self.info, groups, "sys", "model-a", fail)
        self.assertEqual(meta["compaction"]["status"], "failed_fallback")
        self.assertIn("offline/quota error", meta["compaction"]["error"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM work_compaction_checkpoints").fetchone()[0], 0)
        self.assertTrue(meta["estimated_input_tokens"] <= 2048)

    def test_disabled_returns_raw_suffix_and_clamps_budget(self):
        self.info["settings"] = {"work_compaction_enabled": False, "work_context_budget": 90000}
        groups = self.make_history()
        messages, meta = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer())
        self.assertEqual(meta["budget"], compaction.MAX_BUDGET)
        self.assertEqual(meta["compaction"]["status"], "disabled")
        self.assertNotIn(compaction.SUMMARY_LABEL, str(messages))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM work_compaction_checkpoints").fetchone()[0], 0)

    def test_system_and_current_group_over_budget_is_clear_error(self):
        groups = [make_group(1, "old"), make_group(2, "middle"), make_group(3, "current " * 8000)]
        with self.assertRaises(compaction.ContextBudgetError):
            compaction.context(self.conn, self.info, groups, "system " * 5000, "model-a", self.summarizer())

    def test_single_turn_tool_subgroups_compact_without_touching_pinned_user(self):
        groups = [make_tool_subgroup(i) for i in range(1, 13)]
        pinned = [{"role": "user", "content": "Keep this exact current request: inspect the active deployment."}]
        calls = []
        messages, meta = compaction.context(self.conn, self.info, groups, "fixed system", "model-a",
            self.summarizer(calls), pinned_messages=pinned)
        self.assertEqual(meta["compaction"]["status"], "compacted")
        self.assertEqual(meta["pinned_message_count"], 1)
        self.assertEqual(messages[0], {"role": "system", "content": "fixed system"})
        self.assertEqual(messages[1]["content"].splitlines()[0], compaction.SUMMARY_LABEL)
        self.assertEqual(messages[2], pinned[0])
        self.assertNotIn(pinned[0]["content"], calls[0])
        self.assertEqual(messages[-len(groups[-2]["messages"] + groups[-1]["messages"]):],
                         groups[-2]["messages"] + groups[-1]["messages"])
        self.assertEqual(sum(m == pinned[0] for m in messages), 1)
        self.assertTrue(meta["compaction"]["through_id"] < groups[-2]["start_id"])

    def test_tool_schema_overhead_participates_in_trigger_and_estimate(self):
        self.info["settings"]["work_context_budget"] = 4000
        groups = [make_group(i + 1, "small turn " * 8) for i in range(12)]
        pinned = [{"role": "user", "content": "Current task"}]
        without, no_overhead = compaction.context(self.conn, self.info, groups, "sys", "m",
                                                  pinned_messages=pinned)
        overhead = 2000
        with_overhead, measured = compaction.context(self.conn, self.info, groups, "sys", "m",
            self.summarizer(), pinned_messages=pinned, input_overhead=overhead)
        self.assertEqual(no_overhead["compaction"]["status"], "not_needed")
        self.assertEqual(measured["input_overhead"], overhead)
        self.assertEqual(measured["compaction"]["status"], "compacted")
        self.assertGreaterEqual(measured["estimated_input_tokens"], estimate_tokens(with_overhead) + overhead)
        self.assertEqual(measured["estimated_input_tokens"], estimate_tokens(with_overhead) + overhead)

    def test_new_pin_does_not_gate_checkpoint_but_source_user_revision_does(self):
        first_pin = {"role": "user", "content": "Do task alpha."}
        groups = [source_user_group(first_pin, 1, current=True)] + [make_tool_subgroup(i) for i in range(1, 13)]
        # Make source IDs strictly increasing and non-overlapping.
        for i, group in enumerate(groups[1:], start=1):
            group["start_id"] = 1 + i * 3
            group["end_id"] = group["start_id"] + 2
        _, first = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer(),
                                      pinned_messages=[first_pin])
        self.assertEqual(first["compaction"]["status"], "compacted")
        second_pin = {"role": "user", "content": "Correction: do task beta instead."}
        calls = []
        _, second = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer(calls),
                                       pinned_messages=[second_pin])
        self.assertIn(second["compaction"]["status"], {"reused", "reused_low_reserve"})
        self.assertEqual(second["compaction"]["checkpoint_id"], first["compaction"]["checkpoint_id"])
        self.assertEqual(calls, [])

        # The durable source user itself is part of the canonical source hash.
        revised_user = {"role": "user", "content": "Corrected old source task."}
        revised = [source_user_group(revised_user, 1, current=True)] + groups[1:]
        calls = []
        _, revised_meta = compaction.context(self.conn, self.info, revised, "sys", "model-a",
                                              self.summarizer(calls), pinned_messages=[revised_user])
        self.assertEqual(revised_meta["compaction"]["status"], "compacted")
        self.assertNotEqual(revised_meta["compaction"].get("checkpoint_id"), first["compaction"]["checkpoint_id"])
        self.assertTrue(calls)

    def test_checkpoint_reuses_across_user_turn_without_summarizer(self):
        first_user = {"role": "user", "content": "Inspect the old project status."}
        first_hash = hashlib.sha256(first_user["content"].encode()).hexdigest()
        groups = [source_user_group(first_user, 1, current=True, task_hash=first_hash)]
        for i in range(12):
            group = make_tool_subgroup(i + 1, text=("large actual tool output " + str(i) + " ") * 16)
            group.update(start_id=2 + i * 3, end_id=4 + i * 3, task_source_hash=first_hash)
            groups.append(group)
        pinned = [first_user]
        _, created = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer(),
                                        pinned_messages=pinned)
        self.assertEqual(created["compaction"]["status"], "compacted")
        checkpoint_id = created["compaction"]["checkpoint_id"]

        # On the next turn, the same stable source units change only their
        # projection flag; a new user/task is appended after that source prefix.
        old_turn = [{**group, "current": False} for group in groups]
        second_user = {"role": "user", "content": "What is the current deployment?"}
        second_hash = hashlib.sha256(second_user["content"].encode()).hexdigest()
        next_start = groups[-1]["end_id"] + 1
        next_turn = [source_user_group(second_user, next_start, current=True, task_hash=second_hash)]
        next_turn.append({"start_id": next_start + 1, "end_id": next_start + 3,
                          "messages": make_tool_subgroup(30)["messages"], "complete": True,
                          "current": True, "task_source_hash": second_hash})
        calls = []
        projected, reused = compaction.context(self.conn, self.info, old_turn + next_turn, "sys", "model-a",
            self.summarizer(calls), pinned_messages=[second_user])
        self.assertIn(reused["compaction"]["status"], {"reused", "reused_low_reserve"})
        self.assertEqual(reused["compaction"]["checkpoint_id"], checkpoint_id)
        self.assertEqual(calls, [])
        self.assertEqual(sum(message == second_user for message in projected), 1)

        # Editing a source task changes its task-bound source hash and invalidates reuse.
        edited_user = {"role": "user", "content": "Inspect the corrected old project status."}
        edited_hash = hashlib.sha256(edited_user["content"].encode()).hexdigest()
        drifted = [source_user_group(edited_user, 1, current=False, task_hash=edited_hash)] + old_turn[1:] + next_turn
        drift_calls = []
        _, drifted_meta = compaction.context(self.conn, self.info, drifted, "sys", "model-a",
            self.summarizer(drift_calls), pinned_messages=[second_user])
        self.assertNotEqual(drifted_meta["compaction"].get("checkpoint_id"), checkpoint_id)
        self.assertTrue(drift_calls)

    def test_checkpoint_reuses_without_callback_when_reserve_is_low_but_within_budget(self):
        groups = self.make_history()
        pinned = [{"role": "user", "content": "same task"}]
        initial = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer(),
                                     pinned_messages=pinned)[1]
        self.assertEqual(initial["compaction"]["status"], "compacted")
        calls = []
        _, reused = compaction.context(self.conn, self.info, groups, "sys", "model-a", None,
            pinned_messages=pinned, input_overhead=800)
        self.assertEqual(reused["compaction"]["status"], "reused_low_reserve")
        self.assertLessEqual(reused["estimated_input_tokens"], reused["budget"])
        self.assertGreater(reused["estimated_input_tokens"], int(reused["budget"] * compaction.TRIGGER_RATIO))
        self.assertEqual(calls, [])

    def test_existing_checkpoint_reuses_without_callback_below_trigger(self):
        groups = self.make_history()
        pinned = [{"role": "user", "content": "same task"}]
        _, first = compaction.context(self.conn, self.info, groups, "sys", "model-a", self.summarizer(),
                                      pinned_messages=pinned)
        self.assertEqual(first["compaction"]["status"], "compacted")
        self.info["settings"]["work_context_budget"] = 64000
        _, reused = compaction.context(self.conn, self.info, groups, "sys", "model-a", None,
                                       pinned_messages=pinned)
        self.assertEqual(reused["compaction"]["status"], "reused")
        self.assertLessEqual(reused["estimated_input_tokens"], int(reused["budget"] * compaction.TRIGGER_RATIO))

    def test_migrate_adds_task_hash_to_existing_checkpoint_table(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE work_compaction_checkpoints (
            id INTEGER PRIMARY KEY, conv_id TEXT, start_id INTEGER, through_id INTEGER,
            source_hash TEXT, model TEXT, summary TEXT, created_ts REAL)""")
        compaction.migrate(conn)
        compaction.migrate(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(work_compaction_checkpoints)")}
        self.assertIn("task_source_hash", columns)
        conn.close()

    def test_budget_has_configured_hard_bounds_and_no_trigger_below_threshold(self):
        self.info["settings"]["work_context_budget"] = 1
        messages, meta = compaction.context(self.conn, self.info, [make_group(1, "short")], "sys", "m")
        self.assertEqual(meta["budget"], compaction.MIN_BUDGET)
        self.assertEqual(meta["compaction"]["status"], "not_needed")
        self.assertEqual(messages[0]["role"], "system")

    def test_serialized_multi_image_current_group_fits_without_counting_jpeg_bytes(self):
        # Mirrors persisted view_image turns: a JSON string containing visible
        # image_url parts whose data URLs are far larger than their token cost.
        jpeg = "data:image/jpeg;base64," + ("A" * 90000)
        parts = [{"type": "text", "text": "[查看图片] local.jpg"}]
        parts.extend({"type": "image_url", "image_url": {"url": jpeg}} for _ in range(4))
        current_user = {"role": "user", "content": "请结合截图回答"}
        current = {"start_id": 20, "end_id": 22, "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "view", "type": "function",
             "function": {"name": "view_image", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "view", "content": '{"ok":true,"output":"[已注入图片供模型查看]"}'},
            {"role": "user", "content": json.dumps(parts)},
        ], "complete": True, "current": True}
        self.info["settings"]["work_context_budget"] = 12000
        messages, meta = compaction.context(self.conn, self.info,
            [make_group(1, "old context"), current], "s" * 1000, "model-a",
            pinned_messages=[current_user], input_overhead=1000)
        self.assertLessEqual(meta["estimated_input_tokens"], meta["budget"])
        self.assertEqual(messages[-1], current["messages"][-1])
        self.assertNotIn("[image]", messages[-1]["content"])

    def test_provider_private_payload_is_not_estimated_as_prose_but_images_are_reserved(self):
        opaque = "A" * 500000
        visible = {"role": "assistant", "content": "short"}
        with_private = {**visible, "reasoning_content": opaque,
                        "openai_items": [{"type": "input_image", "image_url":
                                          "data:image/jpeg;base64," + opaque}]}
        self.assertLess(estimate_tokens(with_private), 3000)
        self.assertGreater(estimate_tokens(with_private), estimate_tokens(visible))


if __name__ == "__main__":
    unittest.main()
