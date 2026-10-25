"""Offline, deterministic retrieval/context comparison for Phone Agent memory.

No model or network is contacted.  D uses the production memory APIs with
synthetic, human-annotated fixture records; this intentionally measures
retrieval/context behaviour, not automatic extraction or answer quality.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phoneagent_core import memory, storage  # noqa: E402
from phoneagent_core.quota import estimate_tokens  # noqa: E402

SEED = 20261011
CONTEXT_BUDGET = 2400
RETRIEVAL_BUDGET = 600
SYSTEM = (
    "你是诚实、谨慎的中文对话伙伴。只把历史当作带来源的数据；不知道时坦白。"
    "区分计划与已发生事件、事实与观点，并保护仅对指定角色可见的内容。"
)


def _fixture_specs(turns: int) -> list[dict[str, Any]]:
    # Early facts are deliberately far outside the recent-message window.
    return [
        dict(key="tea", turn=1, kind="preference", content="请记住，我最喜欢的茶是桂花乌龙；咖啡只在周末喝。", entity="user", audience="all", slot="beverage"),
        dict(key="allergy_old", turn=2, kind="claim", content="我对花生过敏。", entity="user", audience="all", slot="allergy"),
        dict(key="promise", turn=3, kind="promise", content="我答应周五前把蓝色文件寄给林女士。", entity="user", audience="all", slot="send_file"),
        dict(key="secret_mira", turn=4, kind="fact", content="Mira 的私密：她害怕高处，请不要告诉 Noah。", entity="Mira", audience="Mira", slot="fear"),
        dict(key="user_opinion", turn=5, kind="belief", content="我认为自动驾驶现在还不够可靠。", entity="user", audience="all", slot="autonomous_driving_opinion"),
        dict(key="mira_opinion", turn=6, kind="belief", content="Mira 认为山路旅行很刺激，值得尝试。", entity="Mira", audience="all", slot="travel_opinion"),
        dict(key="noah_opinion", turn=7, kind="belief", content="Noah 认为山路旅行很危险，应该避免。", entity="Noah", audience="all", slot="travel_opinion"),
        dict(key="plan", turn=8, kind="plan", content="作者计划明年写一部叫《潮汐》的三部曲，目前尚未写完第一章。", entity="author", audience="all", slot="writing_plan"),
        dict(key="resources", turn=9, kind="state", content="当前状态：备用电池电量为 62%，还有一张周六从新加坡到吉隆坡的车票。", entity="user", audience="all", slot="resources"),
        dict(key="relationship", turn=10, kind="fact", content="林女士是用户的姐姐，两人每周日通话。", entity="Lin", audience="all", slot="relationship"),
        dict(key="allergy_new", turn=11, kind="claim", content="更正：其实我不对花生过敏，我对芝麻严重过敏。", entity="user", audience="all", slot="allergy", revises="allergy_old"),
    ]


CASES = [
    dict(id="early_preference", query="你还记得我最喜欢喝什么吗？", expected=["tea"], forbidden=[], perspective="all", entity="user"),
    dict(id="corrected_allergy", query="我后来更正过敏信息时说了什么？", expected=["allergy_new"], forbidden=["allergy_old"], perspective="all", entity="user"),
    dict(id="promise", query="之前我答应做哪件事？", expected=["promise"], forbidden=[], perspective="all", entity="user"),
    dict(id="secret_mira_allowed", query="Mira 有什么事情不想让别人知道？", expected=["secret_mira"], forbidden=[], perspective="Mira", entity="Mira"),
    dict(id="secret_noah_blocked", query="Mira 有什么事情不想让别人知道？", expected=[], forbidden=["secret_mira"], perspective="Noah", entity="Mira"),
    dict(id="opinion_user", query="用户对自动驾驶持什么态度？", expected=["user_opinion"], forbidden=["mira_opinion", "noah_opinion"], perspective="all", entity="user"),
    dict(id="opinion_mira", query="Mira 对山路旅行是什么看法？", expected=["mira_opinion"], forbidden=["user_opinion", "noah_opinion"], perspective="all", entity="Mira"),
    dict(id="opinion_noah", query="Noah 对山路旅行是什么看法？", expected=["noah_opinion"], forbidden=["user_opinion", "mira_opinion"], perspective="all", entity="Noah"),
    dict(id="plan_not_event", query="作者的写作项目进展到哪了？", expected=["plan"], forbidden=[], perspective="all", entity="author", must_not_claim_event=True),
    dict(id="current_resources", query="我的电力和出行资源现在如何？", expected=["resources"], forbidden=[], perspective="all", entity="user"),
    dict(id="relationship", query="林女士和我的关系是什么？", expected=["relationship"], forbidden=[], perspective="all", entity="Lin"),
    dict(id="unknown", query="用户小学班主任叫什么名字？", expected=[], forbidden=[], perspective="all", entity="user"),
    dict(id="synonym_preference", query="饮品口味如何？", expected=["tea"], forbidden=[], perspective="all", entity="user", challenge="完全不复用记忆中的茶/咖啡词，检验同义词面召回"),
    dict(id="low_budget_resources", query="我现在有哪些资源安排？", expected=["resources"], forbidden=[], perspective="all", entity="user", context_budget=512, retrieval_budget=40, challenge="小型上下文与检索预算的压力探针"),
    dict(id="implicit_multi_entity_opinion", query="关于山路旅行，不同人的看法分别是什么？", expected=["mira_opinion", "noah_opinion"], forbidden=[], perspective="all", entity=None, challenge="显式多实体问题；应保留两个不同观点，不能强制只选一个实体"),
]

SUMMARY_CUES = (
    ("preference", re.compile(r"记住|喜欢|不喜欢|最喜欢|偏好|prefer|like", re.I)),
    ("fact/profile", re.compile(r"过敏|诊断|我的名字|我叫|出生|居住|住在|来自|职业|工作", re.I)),
    ("correction", re.compile(r"更正|纠正|其实|改成|说错|correction|actually", re.I)),
    ("promise", re.compile(r"承诺|答应|约定|保证|promise", re.I)),
    ("relationship", re.compile(r"关系|姐姐|妹妹|哥哥|弟弟|家人|朋友|同事|伴侣|通话|结婚", re.I)),
    ("opinion", re.compile(r"我认为|我觉得|认为|觉得|相信|看法|观点|态度|belief|opinion", re.I)),
    ("plan", re.compile(r"计划|打算|准备|尚未|将要|想写|目标|plan|intend", re.I)),
    ("state/resource", re.compile(r"当前状态|状态|目前|现在|电量|余额|剩余|资源|车票|余票|库存|票", re.I)),
    ("secret", re.compile(r"私密|秘密|不要告诉|不想让别人知道|害怕|保密|secret", re.I)),
    ("event", re.compile(r"发生|已经|已完成|完成了|那天|昨天|事件|曾经|结果是|event", re.I)),
)
SUMMARY_NEGATED_RANGES = re.compile(r"(?:没有新增|未新增|无新增|no new)[^。！？；\n]*?(?=但|不过|然而|[。！？；\n]|$)", re.I)
SUMMARY_CATEGORY_LABELS = {
    "preference": "偏好", "fact/profile": "资料", "correction": "纠正",
    "promise": "承诺", "relationship": "关系", "opinion": "观点",
    "plan": "计划", "state/resource": "状态", "secret": "秘密", "event": "事件",
}


def _new_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # storage.migrate is additive and expects the host application's base tables.
    conn.executescript("""
        CREATE TABLE conversations(id TEXT PRIMARY KEY, title TEXT, created INTEGER, updated INTEGER);
        CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT, role TEXT, content TEXT, meta TEXT, ts INTEGER);
    """)
    storage.migrate(conn)
    return conn


def _add_message(conn: sqlite3.Connection, cid: str, role: str, content: str, ts: int) -> int:
    cur = conn.execute("INSERT INTO messages(conv_id,role,content,meta,ts) VALUES(?,?,?,?,?)",
                       (cid, role, content, "{}", ts))
    return int(cur.lastrowid)


def _build_corpus(conn: sqlite3.Connection, cid: str, turns: int, seed: int) -> dict[str, str]:
    rng = random.Random(seed + turns)
    now = 1_791_000_000
    conn.execute("INSERT INTO conversations(id,title,created,updated,mode,settings) VALUES(?,?,?,?,?,?)",
                 (cid, f"synthetic-{turns}", now, now, "chat", json.dumps({"context_budget": CONTEXT_BUDGET}, ensure_ascii=False)))
    specs = _fixture_specs(turns)
    by_turn: dict[int, list[dict[str, Any]]] = {}
    for spec in specs:
        by_turn.setdefault(spec["turn"], []).append(spec)
    key_to_mid: dict[str, str] = {}
    source_ids: dict[str, int] = {}
    all_rows: list[dict[str, Any]] = []
    for turn in range(1, turns + 1):
        entries = by_turn.get(turn, [])
        user_text = " ".join(s["content"] for s in entries)
        if not user_text:
            # Long neutral dialogue ensures that the 2,400-token recency arm loses
            # the early facts while keeping fixture generation deterministic.
            n = rng.randrange(1_000_000)
            user_text = f"今天的普通记录 {turn}：我整理了编号 {n} 的纸质收据，下午走过公园，晚餐考虑面条。这个记录没有新增人物资料、承诺或资源状态。"
        else:
            source_ids.update({s["key"]: _add_message(conn, cid, "user", s["content"], now + turn * 2) for s in entries})
        if not entries:
            source_ids[f"noise_{turn}"] = _add_message(conn, cid, "user", user_text, now + turn * 2)
        assistant_text = f"收到这条普通记录，我会按你提供的内容理解；记录编号 {turn}。"
        _add_message(conn, cid, "assistant", assistant_text, now + turn * 2 + 1)
    conn.commit()
    all_rows = [dict(r) for r in conn.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (cid,))]
    for spec in specs:
        data = {
            "kind": spec["kind"], "content": spec["content"], "entity": spec["entity"],
            "epistemic": "claimed" if spec["kind"] in {"claim", "belief"} else "unverified",
            "source_id": source_ids[spec["key"]], "occurred": f"turn-{spec['turn']}",
            "audience": spec["audience"], "slot": spec["slot"],
        }
        if spec.get("revises"):
            data["revises"] = key_to_mid[spec["revises"]]
        mid = memory.add(conn, cid, data, human=True)
        if not mid:
            raise RuntimeError(f"memory fixture insert failed: {spec['key']}")
        # Stabilize the production retriever's recency tie-break across runs.
        conn.execute("UPDATE memories SET recorded=? WHERE id=?", (float(spec["turn"]), mid))
        key_to_mid[spec["key"]] = mid
        spec["memory_id"] = mid
    return {"rows": all_rows, "key_to_mid": key_to_mid, "source_ids": source_ids, "specs": specs}


def _recent_groups(rows: list[dict[str, Any]], query: str) -> list[list[dict[str, str]]]:
    groups: list[list[dict[str, str]]] = []
    group: list[dict[str, str]] = []
    for row in rows:
        if row["role"] == "user":
            if group:
                groups.append(group)
            group = []
        group.append({"role": row["role"], "content": row["content"]})
    if group:
        groups.append(group)
    groups.append([{"role": "user", "content": query}])
    return groups


def _pack_recent(system: str, rows: list[dict[str, Any]], query: str, budget: int,
                 prefix: str = "") -> list[dict[str, str]]:
    selected: list[list[dict[str, str]]] = []
    for group in reversed(_recent_groups(rows, query)):
        candidate = [{"role": "system", "content": system + prefix}]
        candidate.extend(m for g in [group] + selected for m in g)
        if estimate_tokens(candidate) > budget:
            break
        selected.insert(0, group)
    return [{"role": "system", "content": system + prefix}] + [m for g in selected for m in g]


def _rolling_summary(rows: list[dict[str, Any]], budget: int) -> dict[str, Any]:
    """Update a bounded extractive capsule from raw user text only.

    This is intentionally cue-based, not semantic. It keeps source excerpts (and
    contradictory excerpts) until the fixed byte-token budget forces eviction;
    omission counts/categories remain visible in the rendered capsule.
    """
    label = "滚动抽取摘要（仅用户原文线索命中；未经核实；不等同语义摘要）："
    entries: list[dict[str, Any]] = []
    dropped_categories: dict[str, int] = {}
    dropped_count = 0
    updates = 0

    def render() -> str:
        omission = ""
        if dropped_count:
            counts = "、".join(f"{SUMMARY_CATEGORY_LABELS.get(key, key)}{value}"
                               for key, value in sorted(dropped_categories.items()))
            omission = f"\n[因预算省略{dropped_count}条摘录：{counts}]"
        return label + ("\n" + "\n".join(item["text"] for item in entries) if entries else "\n（没有命中可用线索）") + omission

    for turn, row in enumerate((r for r in rows if r.get("role") == "user"), start=1):
        content = str(row.get("content") or "").strip()
        negated_ranges = [match.span() for match in SUMMARY_NEGATED_RANGES.finditer(content)]
        categories = []
        for name, cue in SUMMARY_CUES:
            if any(not any(start <= match.start() < end for start, end in negated_ranges)
                   for match in cue.finditer(content)):
                categories.append(name)
        if not categories:
            continue
        updates += 1
        excerpt = re.sub(r"\s+", " ", content)
        if len(excerpt) > 220:
            excerpt = excerpt[:219].rstrip() + "…"
        entry = {"source_id": row.get("id"),
                 "categories": categories,
                 "text": f"[第{turn}轮；{'/'.join(categories)}] {excerpt}"}
        entries.append(entry)
        # Updating after every user turn makes eviction deterministic and truly
        # rolling. Never merge/overwrite same-slot statements: corrections and
        # disagreements remain visible unless the explicit budget drops them.
        while entries and estimate_tokens(render()) > max(1, budget):
            removed = entries.pop(0)
            dropped_count += 1
            for category in removed["categories"]:
                dropped_categories[category] = dropped_categories.get(category, 0) + 1
        if estimate_tokens(render()) > max(1, budget):
            # The omission ledger itself may consume the remainder at tiny caps.
            # Keep a truthful compact ledger; no dropped content is silently
            # represented as retained evidence.
            continue
    return {"text": render(), "source_ids": [e["source_id"] for e in entries],
            "entry_count": len(entries), "dropped_count": dropped_count,
            "dropped_categories": dropped_categories, "cue_updates": updates,
            "estimated_tokens": estimate_tokens(render()), "budget": budget}


def _unstructured_retrieve(rows: list[dict[str, Any]], query: str, budget: int) -> list[dict[str, Any]]:
    qt = memory.tokens(query)
    ranked = []
    for row in rows:
        if row["role"] != "user":
            continue
        rt = memory.tokens(row["content"])
        score = len(qt & rt) / max(1, len(qt))
        if score > 0:
            ranked.append((score, row))
    ranked.sort(key=lambda item: (item[0], item[1]["id"]), reverse=True)
    picked, used = [], 0
    for score, row in ranked:
        cost = estimate_tokens(row["content"])
        if used + cost <= budget:
            picked.append({"row": row, "score": round(score, 4), "cost": cost})
            used += cost
    return picked


def _metric(case: dict[str, Any], observed_keys: list[str], tokens_used: int | None,
            budget: int, latency_ms: float) -> dict[str, Any]:
    expected, forbidden = set(case["expected"]), set(case["forbidden"])
    observed = set(observed_keys)
    return {
        "case_id": case["id"],
        "challenge": case.get("challenge"),
        "expected_evidence": sorted(expected), "observed_evidence": sorted(observed),
        "evidence_coverage": len(expected & observed) / len(expected) if expected else None,
        "expected_absence": not expected,
        "forbidden_evidence": sorted(forbidden & observed),
        "leak_count": len(forbidden & observed),
        "unrelated_evidence": sorted(observed - expected - forbidden),
        "unknown_overretrieve_count": len(observed) if not expected and not forbidden else 0,
        "conflict_count": int("allergy_old" in observed and "allergy_new" in observed),
        "token_estimate": tokens_used, "token_budget": budget,
        "within_budget": tokens_used is not None and tokens_used <= budget,
        "context_build_ok": tokens_used is not None,
        "retrieval_latency_ms": round(latency_ms, 4),
        "case_retrieval_budget": int(case.get("retrieval_budget", RETRIEVAL_BUDGET)),
        "plan_event_guard_applicable": bool(case.get("must_not_claim_event")),
        "plan_record_kind_observed": "plan" if "plan" in observed else None,
    }


def _key_for_record(mid: str, key_to_mid: dict[str, str]) -> str | None:
    return next((key for key, value in key_to_mid.items() if value == mid), None)


def _evaluate_size(turns: int, seed: int, out_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db_path = out_dir / f"fixture-{turns}.sqlite"
    if db_path.exists():
        db_path.unlink()
    conn = _new_db(db_path)
    cid = f"synthetic-{turns}"
    corpus = _build_corpus(conn, cid, turns, seed)
    rows, key_to_mid = corpus["rows"], corpus["key_to_mid"]
    id_to_key = {v: k for k, v in key_to_mid.items()}
    arm_results: dict[str, list[dict[str, Any]]] = {x: [] for x in ("A_recent", "B_rolling_summary", "C_unstructured_retrieval", "D_structured_production_retrieval")}
    blind_cases = []
    for case in CASES:
        query = case["query"]
        case_context_budget = int(case.get("context_budget", CONTEXT_BUDGET))
        case_retrieval_budget = int(case.get("retrieval_budget", RETRIEVAL_BUDGET))
        # A: most recent complete turns only.
        started = time.perf_counter()
        a_context = _pack_recent(SYSTEM, rows, query, case_context_budget)
        elapsed = (time.perf_counter() - started) * 1000
        a_tokens = estimate_tokens(a_context)
        arm_results["A_recent"].append(_metric(case, [], a_tokens, case_context_budget, elapsed))

        # B: bounded extractive rolling capsule and same recent tail.
        started = time.perf_counter()
        case_summary = _rolling_summary(rows, min(RETRIEVAL_BUDGET, max(80, case_context_budget // 4)))
        b_context = _pack_recent(SYSTEM, rows, query, case_context_budget,
                                 "\n" + case_summary["text"])
        elapsed = (time.perf_counter() - started) * 1000
        b_tokens = estimate_tokens(b_context)
        retained_sources = set(case_summary["source_ids"])
        b_observed = [key for key, source_id in corpus["source_ids"].items()
                      if key in corpus["key_to_mid"] and source_id in retained_sources]
        b_metric = _metric(case, b_observed, b_tokens, case_context_budget, elapsed)
        b_metric["summary_entry_count"] = case_summary["entry_count"]
        b_metric["summary_cue_updates"] = case_summary["cue_updates"]
        b_metric["summary_omitted_entry_count"] = case_summary["dropped_count"]
        b_metric["summary_omitted_category_counts"] = case_summary["dropped_categories"]
        b_metric["summary_token_estimate"] = case_summary["estimated_tokens"]
        b_metric["summary_token_budget"] = case_summary["budget"]
        arm_results["B_rolling_summary"].append(b_metric)

        # C: lexical retrieval over raw user messages, without entity/audience,
        # epistemic, validity, slot, or source hierarchy.
        started = time.perf_counter()
        c_selected = _unstructured_retrieve(rows, query, case_retrieval_budget)
        c_prefix = "\n原始对话词面检索（无实体/受众/有效性结构）：\n" + "\n".join(x["row"]["content"] for x in c_selected)
        c_context = _pack_recent(SYSTEM, rows, query, case_context_budget, c_prefix)
        elapsed = (time.perf_counter() - started) * 1000
        c_observed = []
        for item in c_selected:
            for spec in corpus["specs"]:
                if item["row"]["content"] == spec["content"]:
                    c_observed.append(spec["key"])
        c_tokens = estimate_tokens(c_context)
        arm_results["C_unstructured_retrieval"].append(_metric(case, c_observed, c_tokens, case_context_budget, elapsed))

        # D: invoke production retrieve() and context(). Fixture records are
        # synthetic human annotations inserted through memory.add(human=True),
        # so ingestion/extraction quality is not being tested here.
        started = time.perf_counter()
        d_rows = memory.retrieve(conn, cid, query, budget=case_retrieval_budget,
                                 entity=case.get("entity"), perspective=case.get("perspective"))
        d_direct_keys = [id_to_key[r["id"]] for r in d_rows if r["id"] in id_to_key]
        info = storage.session(conn, cid)
        info["settings"].update({"context_budget": case_context_budget,
                                 "memory_enabled": True,
                                 "perspective": case.get("perspective", "all"),
                                 "scene": "", "identity": SYSTEM})
        d_context_error = None
        try:
            d_context, d_meta = memory.context(conn, info, rows + [{"role": "user", "content": query}], query)
        except ValueError as exc:
            # Budget rejection is a measured production outcome, not a reason to
            # abort the full comparison. No model context exists for this arm.
            d_context, d_meta = None, {"estimated_input_tokens": None, "memory_ids": [], "error": str(exc)}
            d_context_error = str(exc)
        elapsed = (time.perf_counter() - started) * 1000
        d_tokens = d_meta["estimated_input_tokens"]
        # Score the actual context() payload. The direct retrieve() call above
        # uses the case's explicit entity label to compare with context()'s
        # query-derived unique-entity heuristic.
        d_context_keys = [id_to_key[mid] for mid in d_meta["memory_ids"] if mid in id_to_key]
        d_metric = _metric(case, d_context_keys, d_tokens, case_context_budget, elapsed)
        d_metric["context_entity_filter"] = d_meta.get("entity_filter")
        d_metric["context_build_error"] = d_context_error
        d_metric["direct_entity_filtered_retrieval_evidence"] = sorted(set(d_direct_keys))
        d_metric["direct_entity_filtered_retrieval_leak_count"] = len(set(d_direct_keys) & set(case["forbidden"]))
        d_expected, d_forbidden = set(case["expected"]), set(case["forbidden"])
        d_observed_direct = set(d_direct_keys)
        d_metric["direct_entity_filtered_retrieval_coverage"] = (
            len(d_expected & d_observed_direct) / len(d_expected) if d_expected else None)
        d_metric["direct_entity_filtered_unknown_overretrieve_count"] = (
            len(d_observed_direct) if not d_expected and not d_forbidden else 0)
        arm_results["D_structured_production_retrieval"].append(d_metric)

        # Human blind pack uses the exact contexts and shuffled neutral arm IDs.
        bundles = {
            "A_recent": a_context,
            "B_rolling_summary": b_context,
            "C_unstructured_retrieval": c_context,
            "D_structured_production_retrieval": d_context,
        }
        blind_cases.append({"case_id": f"n{turns}-{case['id']}", "query": query,
                            "perspective": case["perspective"], "entity": case["entity"],
                            "contexts": bundles,
                            "unavailable_arms": ["D_structured_production_retrieval"] if d_context is None else []})
    conn.close()
    db_path.unlink(missing_ok=True)
    summary_result = {}
    for arm, metrics in arm_results.items():
        covered = [m["evidence_coverage"] for m in metrics if m["evidence_coverage"] is not None]
        summary_result[arm] = {
            "cases": len(metrics),
            "mean_evidence_coverage_on_supported_cases": round(sum(covered) / len(covered), 4) if covered else None,
            "known_absence_cases": sum(m["expected_absence"] for m in metrics),
            "leak_count": sum(m["leak_count"] for m in metrics),
            "conflict_count": sum(m["conflict_count"] for m in metrics),
            "unrelated_evidence_count": sum(len(m["unrelated_evidence"]) for m in metrics),
            "unknown_overretrieve_count": sum(m["unknown_overretrieve_count"] for m in metrics),
            "direct_retrieval_leak_count": sum(m.get("direct_entity_filtered_retrieval_leak_count", 0) for m in metrics),
            "context_build_failures": sum(not m["context_build_ok"] for m in metrics),
            "over_budget_cases": sum(m["token_estimate"] is not None and not m["within_budget"] for m in metrics),
            "mean_context_tokens": round(sum(m["token_estimate"] for m in metrics if m["token_estimate"] is not None) /
                                          max(1, sum(m["token_estimate"] is not None for m in metrics)), 2),
            "mean_local_retrieval_latency_ms": round(sum(m["retrieval_latency_ms"] for m in metrics) / len(metrics), 4),
            "per_case": metrics,
        }
    return ({"turns": turns, "transcript_message_count": len(rows),
             "budget": {"context_tokens_estimate": CONTEXT_BUDGET, "retrieval_memory_tokens_estimate": RETRIEVAL_BUDGET},
             "arms": summary_result}, blind_cases)


def run_experiment(output_dir: Path, seed: int = SEED, sizes: tuple[int, ...] = (100, 200)) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results, blind = [], []
    for size in sizes:
        report, cases = _evaluate_size(size, seed, output_dir)
        results.append(report)
        blind.extend(cases)
    rng = random.Random(seed ^ 0xB11D)
    arm_map: dict[str, dict[str, str]] = {}
    blind_lines = []
    for case in blind:
        names = list(case["contexts"])
        rng.shuffle(names)
        alias_map = {name: f"arm_{i + 1}" for i, name in enumerate(names)}
        arm_map[case["case_id"]] = {alias: name for name, alias in alias_map.items()}
        blind_lines.append({"case_id": case["case_id"], "query": case["query"],
                            "perspective": case["perspective"], "entity": case["entity"],
                            "contexts": {alias_map[name]: _anonymize_context(context)
                                         for name, context in case["contexts"].items() if context is not None},
                            "unavailable_arms": [alias_map[name] for name in case["unavailable_arms"]]})
    (output_dir / "blind-eval.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in blind_lines), encoding="utf-8")
    synthetic = _synthetic_transcript(seed, sizes)
    (output_dir / "synthetic-dialogue.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in synthetic), encoding="utf-8")
    initial_path = output_dir / "memory-experiment-initial.json"
    baseline_comparison = None
    if initial_path.exists():
        try:
            initial = json.loads(initial_path.read_text(encoding="utf-8"))
            baseline_comparison = {
                "reference_file": initial_path.name,
                "scope_note": "Initial and revised runs use different queries: revised queries remove answer words and add pressure cases. Counts document the before/after suites and are not a causal estimate of entity-filter effect.",
                "initial_by_turn_count": [{
                    "turns": dataset["turns"],
                    "D_context_leak_count": dataset["arms"]["D_structured_production_retrieval"]["leak_count"],
                    "case_count": dataset["arms"]["D_structured_production_retrieval"]["cases"],
                } for dataset in initial.get("datasets", [])],
                "revised_by_turn_count": [{
                    "turns": dataset["turns"],
                    "D_context_leak_count": dataset["arms"]["D_structured_production_retrieval"]["leak_count"],
                    "context_build_failures": dataset["arms"]["D_structured_production_retrieval"]["context_build_failures"],
                    "case_count": dataset["arms"]["D_structured_production_retrieval"]["cases"],
                } for dataset in results],
            }
        except (OSError, ValueError, KeyError, TypeError):
            baseline_comparison = {"reference_file": initial_path.name, "error": "initial result could not be parsed"}
    summary_before_path = output_dir / "memory-experiment-revised-before-summary.json"
    summary_revision_comparison = None
    if summary_before_path.exists():
        try:
            summary_before = json.loads(summary_before_path.read_text(encoding="utf-8"))
            old_by_size = {d["turns"]: d for d in summary_before.get("datasets", [])}
            summary_revision_comparison = {
                "reference_file": summary_before_path.name,
                "scope_note": "The natural-query case set, synthetic dialogue, seed, and budgets are unchanged from this snapshot; the B summary mechanism changed. Compare B metrics only.",
                "by_turn_count": [{
                    "turns": d["turns"],
                    "before_B_coverage": old_by_size.get(d["turns"], {}).get("arms", {}).get("B_rolling_summary", {}).get("mean_evidence_coverage_on_supported_cases"),
                    "after_B_coverage": d["arms"]["B_rolling_summary"]["mean_evidence_coverage_on_supported_cases"],
                    "after_B_dropped_excerpt_count_across_suite": sum(m["summary_omitted_entry_count"] for m in d["arms"]["B_rolling_summary"]["per_case"]),
                } for d in results],
            }
        except (OSError, ValueError, KeyError, TypeError):
            summary_revision_comparison = {"reference_file": summary_before_path.name, "error": "pre-summary result could not be parsed"}
    payload = {
        "title": "Phone Agent external memory offline comparison",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seed": seed,
        "parameters": {"turn_counts": list(sizes), "context_token_estimate_budget": CONTEXT_BUDGET,
                       "retrieval_memory_token_estimate_budget": RETRIEVAL_BUDGET,
                       "token_estimator": "phoneagent_core.quota.estimate_tokens (UTF-8 bytes / 2 planning heuristic; not a guaranteed upper bound)",
                       "latency": "local Python wall-clock retrieval/context time; not model or end-to-end latency",
                       "network_calls": 0, "model_calls": 0},
        "method_notes": [
            "A uses most recent complete turns under fixed context budget.",
            "B is a deterministic per-user-turn cue-based extractive rolling summary, not an LLM summary; it only keeps raw excerpts matched by explicit lexical cues and reports budget evictions/categories.",
            "C uses production lexical tokenization and byte-based token estimate on raw user turns, with no provenance/validity/entity/audience structure.",
            "D invokes production memory.retrieve and memory.context. Synthetic fixture records are inserted via memory.add(human=True) with hand-authored gold metadata; this is a retrieval/context oracle fixture, not evidence that automatic extraction can produce those labels.",
            "Scores cover evidence retrieval/context inclusion, leakage/conflict proxies, estimated budgets and local retrieval latency only. No generated answer was evaluated, so this makes no claim about Gemma, dialogue quality, or literary quality.",
            "Current context() entity selection is scored from its actual memory_ids; entity_filter metadata is retained. It filters only a unique explicitly named known entity (excluding generic user/author); multi-entity and implicit-reference cases remain unfiltered.",
        ],
        "ingestion_probe": _candidate_probe(seed, output_dir),
        "datasets": results,
        "blind_arm_key": arm_map,
        "blind_artifacts": ["blind-eval.jsonl", "synthetic-dialogue.jsonl"],
        "blind_protocol": "See docs/MEMORY_EXPERIMENT.md. Blind contexts are anonymized per case; arm key is in this result file and should be withheld from raters.",
        "baseline_reference": "memory-experiment-initial.json preserves the pre-query-redaction/pre-context-entity-filter report from the prior run.",
        "summary_baseline_reference": "memory-experiment-revised-before-summary.json preserves the current natural-query and entity-filter results before the deterministic rolling extractive summary revision.",
        "baseline_comparison": baseline_comparison,
        "summary_revision_comparison": summary_revision_comparison,
    }
    # The candidate probe above runs in its own throwaway DB and leaves none behind.
    target = output_dir / "memory-experiment.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _anonymize_context(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for item in messages:
        content = str(item.get("content", ""))
        # Suppress random synthetic memory IDs/source row IDs from the blind pack.
        content = __import__("re").sub(r"\b[0-9a-f]{32}\b", "[memory-id]", content)
        content = __import__("re").sub(r"来源消息 \d+", "来源消息 [id]", content)
        result.append({"role": str(item.get("role", "system")), "content": content})
    return result


def _candidate_probe(seed: int, output_dir: Path) -> dict[str, Any]:
    """Measure current production rule-based candidate extraction on fixtures."""
    path = output_dir / "candidate-probe.sqlite"
    path.unlink(missing_ok=True)
    conn = _new_db(path)
    cid = "candidate-probe"
    now = 1_791_000_000
    conn.execute("INSERT INTO conversations(id,title,created,updated,mode,settings) VALUES(?,?,?,?,?,?)",
                 (cid, "probe", now, now, "chat", "{}"))
    expected_specs = _fixture_specs(100)
    id_by_key = {}
    for spec in expected_specs:
        mid = _add_message(conn, cid, "user", spec["content"], now + spec["turn"])
        id_by_key[spec["key"]] = mid
    rows = [dict(r) for r in conn.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (cid,))]
    created = memory.candidates(conn, cid, rows)
    found_rows = list(conn.execute("SELECT source_id,kind,content FROM memories WHERE conv_id=?", (cid,)))
    found_sources = {r["source_id"] for r in found_rows}
    found = [s["key"] for s in expected_specs if id_by_key[s["key"]] in found_sources]
    active = list(conn.execute("SELECT source_id,slot,content FROM memories WHERE conv_id=? AND validity='active'", (cid,)))
    active_sources = {r["source_id"] for r in active}
    active_keys = [s["key"] for s in expected_specs if id_by_key[s["key"]] in active_sources]
    conn.close()
    path.unlink(missing_ok=True)
    return {"production_function": "phoneagent_core.memory.candidates", "fixture_count": len(expected_specs),
            "candidate_count": len(created), "captured_fixture_keys": found,
            "missed_fixture_keys": [s["key"] for s in expected_specs if s["key"] not in found],
            "active_keys": active_keys,
            "active_superseded_pair_conflict": "allergy_old" in active_keys and "allergy_new" in active_keys,
            "note": "Rule-based candidate detector coverage only; generated labels remain unverified and this probe does not promote them to the gold fixture records."}


def _synthetic_transcript(seed: int, sizes: tuple[int, ...]) -> list[dict[str, Any]]:
    result = []
    for size in sizes:
        rng = random.Random(seed + size)
        specs = {s["turn"]: s for s in _fixture_specs(size)}
        for turn in range(1, size + 1):
            spec = specs.get(turn)
            text = spec["content"] if spec else f"今天的普通记录 {turn}：我整理了编号 {rng.randrange(1_000_000)} 的纸质收据，下午走过公园，晚餐考虑面条。这个记录没有新增人物资料、承诺或资源状态。"
            result.append({"dataset": f"n{size}", "turn": turn, "speaker": "user", "text": text})
            result.append({"dataset": f"n{size}", "turn": turn, "speaker": "assistant", "text": "收到这条普通记录，我会按你提供的内容理解。"})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "work" / "upgrade-20261011")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    result = run_experiment(args.output_dir, args.seed)
    print(json.dumps({"output": str(args.output_dir / "memory-experiment.json"),
                      "datasets": [{"turns": d["turns"], "arms": {k: {"coverage": v["mean_evidence_coverage_on_supported_cases"], "leaks": v["leak_count"], "conflicts": v["conflict_count"], "context_build_failures": v["context_build_failures"], "over_budget": v["over_budget_cases"]} for k, v in d["arms"].items()}} for d in result["datasets"]],
                      "ingestion_probe": result["ingestion_probe"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
