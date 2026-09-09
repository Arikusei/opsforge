"""Оффлайн-прогон агента по золотому набору.

Метрики: выбор инструментов, обоснованность ответа, нарушения гардрейлов,
цитируемость, латентность и стоимость. Работает и на demo-контуре (baseline
без LLM), и на живых ключах — так видно, что именно даёт модель.

    python eval/run_eval.py --provider demo
    python eval/run_eval.py --provider openai --out eval/reports/openai.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
API_ROOT = HERE.parent
sys.path.insert(0, str(API_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Eval агента OpsForge")
    parser.add_argument("--provider", default="demo", choices=["demo", "openai", "anthropic", "auto"])
    parser.add_argument("--dataset", default="dataset.jsonl")
    parser.add_argument("--out", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--category", default="", help="Прогнать только одну категорию кейсов")
    parser.add_argument(
        "--database-url",
        default="sqlite:///./data/eval.db",
        help="Отдельная БД, чтобы eval не засорял рабочие лиды",
    )
    parser.add_argument("--no-native-tools", action="store_true", help="Прогнать на JSON-протоколе")
    return parser.parse_args()


def load_cases(path: str, limit: int, category: str) -> list[dict]:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        case = json.loads(line)
        if category and case.get("category") != category:
            continue
        cases.append(case)
    return cases[:limit] if limit else cases


def grade(case: dict, result: dict) -> dict:
    answer = (result.get("answer") or "").lower()
    used_tools = [s["action"] for s in result.get("trace", []) if s.get("type") == "tool"]
    citations = result.get("citations") or []
    checks: list[dict] = []

    required = case.get("expect_tools") or []
    if required:
        missing = [t for t in required if t not in used_tools]
        checks.append(
            {
                "name": "tools_required",
                "ok": not missing,
                "detail": f"не вызваны: {missing}" if missing else "все вызваны",
            }
        )

    any_of = case.get("expect_tools_any") or []
    if any_of:
        hit = [t for t in any_of if t in used_tools]
        checks.append(
            {
                "name": "tools_any_of",
                "ok": bool(hit),
                "detail": f"вызваны: {hit}" if hit else f"ни один из {any_of}",
            }
        )

    forbidden = case.get("forbid_tools") or []
    if forbidden:
        violated = [t for t in forbidden if t in used_tools]
        checks.append(
            {
                "name": "tools_forbidden",
                "ok": not violated,
                "detail": f"лишние вызовы: {violated}" if violated else "нет лишних вызовов",
            }
        )

    groups = case.get("expect_any") or []
    satisfied = 0
    missed_groups: list[list[str]] = []
    for group in groups:
        if any(kw.lower() in answer for kw in group):
            satisfied += 1
        else:
            missed_groups.append(group)
    if groups:
        checks.append(
            {
                "name": "answer_content",
                "ok": not missed_groups,
                "detail": f"{satisfied}/{len(groups)} групп; нет: {missed_groups}"
                if missed_groups
                else f"{satisfied}/{len(groups)} групп",
            }
        )

    patterns = case.get("forbid_patterns") or []
    hits = [p for p in patterns if re.search(p, answer, re.I)]
    if patterns:
        checks.append(
            {
                "name": "guardrails",
                "ok": not hits,
                "detail": f"сработали запреты: {hits}" if hits else "нарушений нет",
            }
        )

    prefix = case.get("expect_citation_prefix")
    if prefix:
        ok = any(c.startswith(prefix) for c in citations)
        checks.append(
            {
                "name": "citation_source",
                "ok": ok,
                "detail": f"цитаты: {citations}" if not ok else f"есть источник {prefix}",
            }
        )

    return {
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "used_tools": used_tools,
        "content_groups_total": len(groups),
        "content_groups_ok": satisfied,
        "guardrail_violations": len(hits),
        "has_citations": bool(citations),
    }


def aggregate(rows: list[dict]) -> dict:
    total = len(rows)
    latencies = [r["latency_ms"] for r in rows]
    groups_total = sum(r["grade"]["content_groups_total"] for r in rows)
    groups_ok = sum(r["grade"]["content_groups_ok"] for r in rows)

    def check_rate(name: str) -> float | None:
        relevant = [c for r in rows for c in r["grade"]["checks"] if c["name"] == name]
        if not relevant:
            return None
        return round(sum(1 for c in relevant if c["ok"]) / len(relevant), 3)

    by_category: dict[str, dict] = {}
    for row in rows:
        bucket = by_category.setdefault(row["category"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(row["grade"]["passed"])
    for bucket in by_category.values():
        bucket["pass_rate"] = round(bucket["passed"] / bucket["total"], 3)

    return {
        "cases": total,
        "pass_rate": round(sum(1 for r in rows if r["grade"]["passed"]) / total, 3) if total else 0,
        "tools_required_rate": check_rate("tools_required"),
        "tools_any_of_rate": check_rate("tools_any_of"),
        "tools_forbidden_rate": check_rate("tools_forbidden"),
        "answer_content_rate": check_rate("answer_content"),
        "guardrail_rate": check_rate("guardrails"),
        "citation_source_rate": check_rate("citation_source"),
        "content_group_coverage": round(groups_ok / groups_total, 3) if groups_total else None,
        "citation_rate": round(sum(1 for r in rows if r["grade"]["has_citations"]) / total, 3) if total else 0,
        "guardrail_violations": sum(r["grade"]["guardrail_violations"] for r in rows),
        "fallbacks": sum(1 for r in rows if r["mode"] == "demo" or r.get("warning")),
        "latency_ms_p50": int(statistics.median(latencies)) if latencies else 0,
        "latency_ms_p95": int(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]) if latencies else 0,
        "total_tokens": sum(r["usage"].get("total_tokens", 0) for r in rows),
        "total_cost_usd": round(sum(r["usage"].get("cost_usd", 0) for r in rows), 6),
        "avg_iterations": round(sum(r["iterations"] for r in rows) / total, 2) if total else 0,
        "avg_tool_calls": round(sum(len(r["grade"]["used_tools"]) for r in rows) / total, 2) if total else 0,
        "by_category": by_category,
    }


def render_markdown(meta: dict, summary: dict, rows: list[dict]) -> str:
    lines = [
        "# Eval агента OpsForge",
        "",
        f"- провайдер: `{meta['provider']}` / модель `{meta['model']}` / режим `{meta['mode']}`",
        f"- кейсов: {summary['cases']}, pass rate: **{summary['pass_rate']:.0%}**",
        f"- покрытие контента: {_pct(summary['content_group_coverage'])}, цитаты: {summary['citation_rate']:.0%}",
        f"- нарушения гардрейлов: {summary['guardrail_violations']}, фолбэков: {summary['fallbacks']}",
        f"- латентность p50/p95: {summary['latency_ms_p50']} / {summary['latency_ms_p95']} мс",
        f"- токены: {summary['total_tokens']}, стоимость: ${summary['total_cost_usd']}",
        f"- среднее число итераций: {summary['avg_iterations']}, вызовов инструментов: {summary['avg_tool_calls']}",
        "",
        "## Метрики по проверкам",
        "",
        "| Проверка | Доля успеха |",
        "| --- | --- |",
        f"| обязательные инструменты | {_pct(summary['tools_required_rate'])} |",
        f"| инструменты «любой из» | {_pct(summary['tools_any_of_rate'])} |",
        f"| отсутствие лишних вызовов | {_pct(summary['tools_forbidden_rate'])} |",
        f"| содержание ответа | {_pct(summary['answer_content_rate'])} |",
        f"| гардрейлы | {_pct(summary['guardrail_rate'])} |",
        f"| цитирование источника | {_pct(summary['citation_source_rate'])} |",
        "",
        "## По категориям",
        "",
        "| Категория | Кейсов | Pass rate |",
        "| --- | --- | --- |",
    ]
    for name, bucket in sorted(summary["by_category"].items()):
        lines.append(f"| {name} | {bucket['total']} | {bucket['pass_rate']:.0%} |")

    failed = [r for r in rows if not r["grade"]["passed"]]
    if failed:
        lines += ["", "## Провалившиеся кейсы", ""]
        for row in failed:
            reasons = "; ".join(c["detail"] for c in row["grade"]["checks"] if not c["ok"])
            lines.append(f"- **{row['id']}** ({row['category']}): {reasons}")
    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "н/д" if value is None else f"{value:.0%}"


def main() -> int:
    args = parse_args()

    # Настройки читаются один раз при импорте app.config, поэтому правим env заранее.
    os.environ["LLM_PROVIDER"] = args.provider
    os.environ["DATABASE_URL"] = args.database_url
    os.environ["TRACE_PERSIST"] = "true"
    if args.no_native_tools:
        os.environ["AGENT_NATIVE_TOOLS"] = "false"

    from app.agents.react import agent_capabilities, run_agent
    from app.db import SessionLocal
    from app.main import init_db

    init_db()
    caps = agent_capabilities()
    if args.provider not in {"demo", "auto"} and caps["provider"] == "demo":
        print(f"[!] Ключ для {args.provider} не найден, прогон пойдёт на demo-контуре", file=sys.stderr)

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = HERE / dataset_path
    cases = load_cases(str(dataset_path), args.limit, args.category)
    if not cases:
        print("Нет кейсов для прогона", file=sys.stderr)
        return 1

    rows: list[dict] = []
    for i, case in enumerate(cases, 1):
        db = SessionLocal()
        try:
            result = run_agent(db, case["question"], channel="eval")
        except Exception as exc:  # прогон не должен падать целиком из-за одного кейса
            result = {"answer": f"ERROR: {exc}", "trace": [], "citations": [], "usage": {}, "mode": "error"}
        finally:
            db.close()

        verdict = grade(case, result)
        rows.append(
            {
                "id": case["id"],
                "category": case.get("category", "misc"),
                "question": case["question"],
                "answer": result.get("answer", ""),
                "run_id": result.get("run_id", ""),
                "mode": result.get("mode", ""),
                "iterations": result.get("iterations", 0),
                "usage": result.get("usage", {}),
                "latency_ms": result.get("usage", {}).get("latency_ms", 0),
                "warning": result.get("warning"),
                "grade": verdict,
            }
        )
        mark = "OK  " if verdict["passed"] else "FAIL"
        print(f"[{i:>2}/{len(cases)}] {mark} {case['id']:<28} tools={verdict['used_tools']}")

    summary = aggregate(rows)
    meta = {
        "provider": caps["provider"],
        "model": caps["model"],
        "mode": caps["mode"],
        "dataset": dataset_path.name,
    }
    report: dict[str, Any] = {"meta": meta, "summary": summary, "cases": rows}

    out = Path(args.out) if args.out else HERE / "reports" / f"{caps['provider']}-{caps['mode']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = out.with_suffix(".md")
    md.write_text(render_markdown(meta, summary, rows), encoding="utf-8")

    print()
    print(render_markdown(meta, summary, rows))
    print(f"Отчёты: {out} и {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
