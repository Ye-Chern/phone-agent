import json
import tempfile
import unittest
from pathlib import Path

from scripts import evaluate_memory as experiment
from phoneagent_core import memory, storage


class MemoryExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = experiment._new_db(self.root / "fixture.sqlite")
        self.cid = "test-fixture"
        self.corpus = experiment._build_corpus(self.conn, self.cid, 20, experiment.SEED)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_fixture_uses_sqlite_row_objects_and_production_supersession(self):
        self.assertIsInstance(self.conn.execute("SELECT * FROM conversations").fetchone(), __import__("sqlite3").Row)
        old = self.corpus["key_to_mid"]["allergy_old"]
        new = self.corpus["key_to_mid"]["allergy_new"]
        self.assertEqual(self.conn.execute("SELECT validity FROM memories WHERE id=?", (old,)).fetchone()[0], "superseded")
        self.assertEqual(self.conn.execute("SELECT revises FROM memories WHERE id=?", (new,)).fetchone()[0], old)

    def test_actual_retriever_respects_correction_audience_and_entity(self):
        corrected = memory.retrieve(self.conn, self.cid, "更正后我对什么过敏 芝麻 过敏",
                                    budget=experiment.RETRIEVAL_BUDGET, entity="user", perspective="all")
        self.assertIn(self.corpus["key_to_mid"]["allergy_new"], [row["id"] for row in corrected])
        self.assertNotIn(self.corpus["key_to_mid"]["allergy_old"], [row["id"] for row in corrected])
        hidden = memory.retrieve(self.conn, self.cid, "Mira 的秘密 高处",
                                 budget=experiment.RETRIEVAL_BUDGET, entity="Mira", perspective="Noah")
        self.assertNotIn(self.corpus["key_to_mid"]["secret_mira"], [row["id"] for row in hidden])
        mira_opinion = memory.retrieve(self.conn, self.cid, "Mira 山路旅行看法 刺激",
                                       budget=experiment.RETRIEVAL_BUDGET, entity="Mira", perspective="all")
        self.assertIn(self.corpus["key_to_mid"]["mira_opinion"], [row["id"] for row in mira_opinion])
        self.assertNotIn(self.corpus["key_to_mid"]["noah_opinion"], [row["id"] for row in mira_opinion])

    def test_actual_lexical_retriever_misses_unmatched_synonym_query(self):
        rows = memory.retrieve(self.conn, self.cid, "饮品口味如何？", budget=experiment.RETRIEVAL_BUDGET,
                               entity="user", perspective="all")
        self.assertNotIn(self.corpus["key_to_mid"]["tea"], [row["id"] for row in rows])

    def test_actual_context_respects_estimated_budget_and_reports_memory_ids(self):
        info = storage.session(self.conn, self.cid)
        info["settings"].update({"context_budget": experiment.CONTEXT_BUDGET,
                                 "memory_enabled": True, "perspective": "all", "scene": ""})
        query = "我最喜欢喝什么茶 桂花乌龙"
        rows = self.corpus["rows"] + [{"role": "user", "content": query}]
        payload, meta = memory.context(self.conn, info, rows, query)
        self.assertLessEqual(meta["estimated_input_tokens"], experiment.CONTEXT_BUDGET)
        self.assertIn(self.corpus["key_to_mid"]["tea"], meta["memory_ids"])
        self.assertEqual(payload[0]["role"], "system")

    def test_512_token_context_budget_rejects_oversized_system_scaffold(self):
        info = storage.session(self.conn, self.cid)
        info["settings"].update({"context_budget": 512, "memory_enabled": True,
                                 "perspective": "all", "scene": ""})
        with self.assertRaisesRegex(ValueError, "角色/场景设定超过上下文预算"):
            memory.context(self.conn, info, self.corpus["rows"], "我现在有哪些资源安排？")

    def test_actual_context_honors_audience_and_explicit_entity_filter(self):
        info = storage.session(self.conn, self.cid)
        info["settings"].update({"context_budget": experiment.CONTEXT_BUDGET,
                                 "memory_enabled": True, "scene": ""})
        rows = self.corpus["rows"]
        info["settings"]["perspective"] = "Noah"
        _, hidden_meta = memory.context(self.conn, info, rows, "Mira 的秘密 高处")
        self.assertNotIn(self.corpus["key_to_mid"]["secret_mira"], hidden_meta["memory_ids"])
        # context() infers a unique explicitly named entity, but keeps a
        # cross-entity question unfiltered so both views can be retrieved.
        info["settings"]["perspective"] = "all"
        _, opinion_meta = memory.context(self.conn, info, rows, "Mira 对山路旅行是什么看法")
        self.assertEqual(opinion_meta["entity_filter"], "Mira")
        self.assertIn(self.corpus["key_to_mid"]["mira_opinion"], opinion_meta["memory_ids"])
        self.assertNotIn(self.corpus["key_to_mid"]["noah_opinion"], opinion_meta["memory_ids"])
        # With no unique explicit entity, the context deliberately keeps both
        # viewpoints available for a genuinely cross-entity question.
        _, multi_meta = memory.context(self.conn, info, rows, "关于山路旅行，不同人的看法分别是什么？")
        self.assertIsNone(multi_meta["entity_filter"])
        self.assertIn(self.corpus["key_to_mid"]["mira_opinion"], multi_meta["memory_ids"])
        self.assertIn(self.corpus["key_to_mid"]["noah_opinion"], multi_meta["memory_ids"])

    def test_rolling_summary_updates_from_user_cues_and_keeps_corrections(self):
        raw_rows = [
            {"id": 1, "role": "user", "content": "请记住，我最喜欢的茶是桂花乌龙。"},
            {"id": 2, "role": "assistant", "content": "助手提到一项计划，但这不属于用户原文。"},
            {"id": 3, "role": "user", "content": "我对花生过敏。"},
            {"id": 4, "role": "user", "content": "更正：其实我对芝麻过敏。"},
            {"id": 5, "role": "user", "content": "普通闲聊，不带记忆线索。"},
        ]
        summary = experiment._rolling_summary(raw_rows, 600)
        self.assertEqual(summary["source_ids"], [1, 3, 4])
        self.assertIn("preference", summary["text"])
        self.assertIn("correction", summary["text"])
        self.assertIn("我对花生过敏", summary["text"])
        self.assertIn("我对芝麻过敏", summary["text"])
        self.assertNotIn("助手提到一项计划", summary["text"])
        self.assertEqual(summary["dropped_count"], 0)

    def test_rolling_summary_reports_entries_evicted_by_budget(self):
        rows = [
            {"id": 1, "role": "user", "content": "请记住，我最喜欢的茶是桂花乌龙。"},
            {"id": 2, "role": "user", "content": "更正：其实我不对花生过敏，我对芝麻严重过敏。"},
        ]
        summary = experiment._rolling_summary(rows, 100)
        self.assertGreater(summary["dropped_count"], 0)
        self.assertIn("省略", summary["text"])
        self.assertTrue(summary["dropped_categories"])
        self.assertLessEqual(summary["estimated_tokens"], 100)

    def test_blind_anonymization_removes_record_identifiers(self):
        items = [{"role": "system", "content": "[0123456789abcdef0123456789abcdef | fact | Lin | 来源消息 31] test"}]
        safe = experiment._anonymize_context(items)
        self.assertNotIn("0123456789abcdef", safe[0]["content"])
        self.assertNotIn("来源消息 31", safe[0]["content"])


if __name__ == "__main__":
    unittest.main()
