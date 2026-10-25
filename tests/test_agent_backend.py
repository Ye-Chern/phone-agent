import io
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import agent


class FakeResponse:
    def __init__(self, events):
        self._lines = iter(
            ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
            for event in events
        )
        self.fp = None

    def readline(self):
        return next(self._lines, b"")

    def close(self):
        pass


def text_response(text="done", include_done=True):
    events = [{"choices": [{"delta": {"content": text}, "finish_reason": None}]},
              {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
    events.append({"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 2,
                                               "total_tokens": 10}})
    if include_done:
        events.append({"__done__": True})
    return FakeResponse(events)


def tool_response(calls, reasoning="thinking"):
    deltas = [{"choices": [{"delta": {"reasoning_content": reasoning},
                             "finish_reason": None}]}]
    for index, (call_id, name, args) in enumerate(calls):
        deltas.append({"choices": [{"delta": {"tool_calls": [{
            "index": index, "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }]}, "finish_reason": None}]})
    deltas.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
    deltas.append({"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 5,
                                                "total_tokens": 25}})
    deltas.append({"__done__": True})
    return FakeResponse(deltas)


class AgentBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config_temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_file = self.root / "agent.db"
        self.patch_db = mock.patch.object(agent, "DB_FILE", self.db_file)
        self.patch_config = mock.patch.object(agent, "CONFIG_DIR", Path(self.config_temp.name))
        self.patch_db.start()
        self.patch_config.start()
        self.cid = agent.conv_create()
        with agent.db() as conn:
            agent.storage.update_session(conn, self.cid, {'workdir': str(self.root)}, agent.CONFIG_DIR)
        with agent._active_lock:
            agent._active_runs.clear()
            agent._active_socks.clear()
        agent._stop_events.clear()

    def tearDown(self):
        self.patch_config.stop()
        self.patch_db.stop()
        self.temp.cleanup()
        self.config_temp.cleanup()

    def _events(self):
        events = []
        return events, events.append

    def test_long_history_keeps_system_and_complete_tool_groups_with_reasoning(self):
        for turn in range(17):
            agent.msg_add(self.cid, "user", f"request-{turn}")
            calls = [{"id": f"call-{turn}-{n}", "type": "function",
                      "function": {"name": "list_files", "arguments": "{}"}}
                     for n in range(2)]
            agent.msg_add(self.cid, "assistant", "inspect",
                          {"tool_calls": calls,
                           "tool_results": {c["id"]: {"ok": True, "output": "ok"}
                                            for c in calls},
                           "reasoning": f"reason-{turn}"})
            agent.msg_add(self.cid, "assistant", f"answer-{turn}",
                          {"reasoning": f"plain-reason-{turn}"})

        history = [{"role": "system", "content": agent.SYSTEM_PROMPT}]
        history.extend(agent.history_for_agent(self.cid))
        self.assertEqual(history[0]["role"], "system")
        self.assertEqual(history[-1]["content"], "answer-16")
        self.assertEqual(history[-1]["reasoning_content"], "plain-reason-16")
        for i, item in enumerate(history):
            if item["role"] != "assistant" or not item.get("tool_calls"):
                continue
            count = len(item["tool_calls"])
            following = history[i + 1:i + 1 + count]
            self.assertEqual([m["role"] for m in following], ["tool"] * count)
            self.assertEqual(item.get("reasoning_content"), "reason-" +
                             item["tool_calls"][0]["id"].split("-")[1])
            self.assertEqual([m["tool_call_id"] for m in following],
                             [c["id"] for c in item["tool_calls"]])

    def test_many_tools_then_reply_has_valid_reasoning_and_usage(self):
        calls = [(f"call-{n}", "list_files", {"path": str(self.root)}) for n in range(12)]
        for turn in range(12):
            agent.msg_add(self.cid, "user", f"old-{turn}")
            c = {"id": f"oldcall-{turn}", "type": "function",
                 "function": {"name": "list_files", "arguments": "{}"}}
            agent.msg_add(self.cid, "assistant", "old tool",
                          {"tool_calls": [c], "tool_results": {c["id"]: "old result"},
                           "reasoning": "old thought"})
        with mock.patch.object(agent, "TOOL_IMPL",
                               {**agent.TOOL_IMPL,
                                "list_files": lambda _args: {"ok": True, "output": "ok"}}), \
             mock.patch.object(agent, "api_request",
                               side_effect=[tool_response(calls), text_response()] ) as api:
            events, emit = self._events()
            agent.run_agent(self.cid, "latest request", agent.DEFAULT_MODEL, emit)

        payload = api.call_args_list[1].args[0]
        messages = payload["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertTrue(any(m.get("tool_call_id") == "call-11" for m in messages))
        for i, item in enumerate(messages):
            if item["role"] == "assistant" and item.get("tool_calls"):
                following = messages[i + 1:i + 1 + len(item["tool_calls"])]
                self.assertEqual([m["role"] for m in following],
                                 ["tool"] * len(item["tool_calls"]))
                self.assertTrue(item.get("reasoning_content"))
        self.assertIn({"type": "usage", "usage": {"prompt_tokens": 8,
                                                     "completion_tokens": 2,
                                                     "total_tokens": 10}}, events)
        self.assertEqual(events[-1]["usage"], {"prompt_tokens": 28,
                                                "completion_tokens": 7,
                                                "total_tokens": 35})
        self.assertLessEqual(payload["max_tokens"], 8192)
        self.assertEqual(events[-1]["status"], "completed")

    def test_retry_reuses_user_and_does_not_repeat_completed_tool(self):
        executions = []
        impls = {**agent.TOOL_IMPL,
                 "notify": lambda args: executions.append(args) or
                 {"ok": True, "output": "sent"}}
        failure = urllib.error.HTTPError("https://api.deepseek.com", 503,
                                         "temporary", {}, io.BytesIO(b"temporary"))
        with mock.patch.object(agent, "TOOL_IMPL", impls), \
             mock.patch.object(agent, "api_request",
                               side_effect=[tool_response([("once", "notify", {"message": "hi"})]),
                                            failure]) as api:
            events, emit = self._events()
            agent.run_agent(self.cid, "do it", agent.DEFAULT_MODEL, emit)
        self.assertEqual(len(executions), 1)
        self.assertEqual(events[-1]["retryable"], True)

        with mock.patch.object(agent, "api_request", return_value=text_response()) as retry_api:
            events, emit = self._events()
            agent.run_agent(self.cid, "", agent.DEFAULT_MODEL, emit, retry=True)
        self.assertEqual(len(executions), 1)
        self.assertEqual(sum(m["role"] == "user" for m in agent.conv_messages(self.cid)), 1)
        prior = retry_api.call_args.args[0]["messages"]
        tool_result = next(m for m in prior if m.get("tool_call_id") == "once")
        self.assertIn("sent", tool_result["content"])
        self.assertEqual(events[-1]["status"], "completed")

    def test_legacy_incomplete_turn_is_retryable_after_reopen(self):
        user_id = agent.msg_add(self.cid, "user", "continue old task")
        call = {"id": "legacy-call", "type": "function",
                "function": {"name": "notify", "arguments": '{"message":"sent"}'}}
        agent.msg_add(self.cid, "assistant", "tool step",
                      {"tool_calls": [call],
                       "tool_results": {"legacy-call": "sent"}})
        state = agent.latest_run_state(self.cid)
        self.assertTrue(state["retryable"])
        self.assertIsNone(state["run_id"])
        with mock.patch.object(agent, "api_request", return_value=text_response()):
            events, emit = self._events()
            agent.run_agent(self.cid, "", agent.DEFAULT_MODEL, emit, retry=True)
        self.assertEqual(sum(m["role"] == "user" and m["id"] == user_id
                             for m in agent.conv_messages(self.cid)), 1)
        self.assertEqual(events[-1]["status"], "completed")
        self.assertFalse(agent.latest_run_state(self.cid)["retryable"])

    def test_new_turn_does_not_resume_old_pending_tool_effect(self):
        old_user = agent.msg_add(self.cid, "user", "old task")
        call = {"id": "old-pending", "type": "function",
                "function": {"name": "notify", "arguments": '{"message":"once"}'}}
        agent.msg_add(self.cid, "assistant", "pending",
                      {"tool_calls": [call], "tool_results": {},
                       "tool_statuses": {"old-pending": "pending"},
                       "batch_status": "pending", "run_id": 100})
        effects = []
        with mock.patch.object(agent, "TOOL_IMPL", {**agent.TOOL_IMPL,
                                                     "notify": lambda args: effects.append(args) or
                                                     {"ok": True, "output": "done"}}), \
             mock.patch.object(agent, "api_request", return_value=text_response()):
            events, emit = self._events()
            agent.run_agent(self.cid, "new task", agent.DEFAULT_MODEL, emit)
        self.assertEqual(effects, [])
        messages = agent.conv_messages(self.cid)
        self.assertGreater(messages[-3]["id"], old_user)
        self.assertEqual(events[-1]["status"], "completed")

    def test_uncertain_started_effect_is_not_repeated_on_retry(self):
        user_id = agent.msg_add(self.cid, "user", "once")
        run_id = agent.run_create(self.cid, user_id, agent.DEFAULT_MODEL)
        agent.run_update(run_id, "interrupted", "simulated process restart")
        call = {"id": "maybe", "type": "function",
                "function": {"name": "notify", "arguments": '{"message":"once"}'}}
        agent.msg_add(self.cid, "assistant", "tool",
                      {"tool_calls": [call], "tool_results": {},
                       "tool_statuses": {"maybe": "started"},
                       "batch_status": "pending", "run_id": run_id})
        run = agent.conv_messages(self.cid)[-1]
        effects = []
        with mock.patch.object(agent, "TOOL_IMPL", {**agent.TOOL_IMPL,
                                                     "notify": lambda args: effects.append(args) or
                                                     {"ok": True, "output": "sent"}}), \
             mock.patch.object(agent, "api_request", return_value=text_response()):
            events, emit = self._events()
            agent.run_agent(self.cid, "", agent.DEFAULT_MODEL, emit, retry=True)
        self.assertEqual(effects, [])
        meta = json.loads(next(m for m in agent.conv_messages(self.cid)
                               if m["id"] == run["id"])["meta"])
        self.assertIn("状态未知", meta["tool_results"]["maybe"]["output"])
        self.assertEqual(events[-1]["status"], "completed")

    def test_eof_without_terminal_frame_is_retryable_and_user_is_not_duplicated(self):
        broken = FakeResponse([{"choices": [{"delta": {"content": "partial"},
                                             "finish_reason": None}]}])
        with mock.patch.object(agent, "api_request", side_effect=[broken, text_response()]) as api:
            events, emit = self._events()
            agent.run_agent(self.cid, "hello", agent.DEFAULT_MODEL, emit)
            self.assertEqual(events[-1]["status"], "failed")
            self.assertTrue(events[-1]["retryable"])
            events, emit = self._events()
            agent.run_agent(self.cid, "", agent.DEFAULT_MODEL, emit, retry=True)
        self.assertEqual(sum(m["role"] == "user" for m in agent.conv_messages(self.cid)), 1)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(events[-1]["status"], "completed")

    def test_concurrent_run_is_rejected_without_appending_second_user(self):
        started = threading.Event()
        release = threading.Event()
        responses = []

        def blocking_request(_payload, **_kwargs):
            started.set()
            release.wait(3)
            return text_response()

        with mock.patch.object(agent, "api_request", side_effect=blocking_request) as api:
            first_events, first_emit = self._events()
            thread = threading.Thread(target=agent.run_agent,
                                      args=(self.cid, "first", agent.DEFAULT_MODEL, first_emit))
            thread.start()
            self.assertTrue(started.wait(2))
            second_events, second_emit = self._events()
            agent.run_agent(self.cid, "second", agent.DEFAULT_MODEL, second_emit)
            release.set()
            thread.join(3)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(sum(m["role"] == "user" for m in agent.conv_messages(self.cid)), 1)
        self.assertEqual(second_events[-1]["status"], "busy")

    def test_invalid_tool_json_is_never_executed_as_a_shell_command(self):
        with mock.patch.object(agent, "TOOL_IMPL", {**agent.TOOL_IMPL,
                                                     "run_command": mock.Mock()}) as tools:
            result, _, _ = agent._execute_tool({"function": {
                "name": "run_command", "arguments": "echo should-not-run"}}, lambda _e: None)
        self.assertFalse(result["ok"])
        tools["run_command"].assert_not_called()

    def test_delete_rejects_empty_and_home_paths(self):
        self.assertFalse(agent.tool_delete_file({"path": ""})["ok"])
        self.assertFalse(agent.tool_delete_file({"path": str(Path.home())})["ok"])


if __name__ == "__main__":
    unittest.main()
