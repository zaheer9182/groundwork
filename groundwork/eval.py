"""Evaluation runner.

  python -m groundwork.eval                       # retrieval only, no API key needed
  python -m groundwork.eval --answers             # also generate answers with the configured provider
  python -m groundwork.eval --corpus path --questions path --min-hit-rate 0.9

Each question names the document that should answer it, or null when the
assistant should refuse. A question can also name documents that must never
be retrieved for the evaluating user ("forbid"), to catch permission leaks.

Retrieval mode (no API key): expected document is in the selected chunks,
forbidden documents never are.

Answer mode: also checks the answer cites the expected document and contains
the expected phrase, and that unanswerable questions are refused. Refusal is
judged here, not in retrieval mode: a lexical score cannot tell "related"
from "answers it" (a wifi question matches the password policy), so the score
gate only stops questions with no match at all and the model does the rest.

Exit code is 1 when the hit rate is below the threshold, any forbidden
document leaks, or (answer mode) answer accuracy is below the threshold.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .cache import MemoryCache
from .config import Settings
from .pipeline import Assistant
from .sources.local import LocalSource


async def run(corpus: str, questions_path: str, answers: bool, user: str | None) -> dict:
    s = Settings.from_env()
    provider = None
    if answers:
        from .providers import provider_from_settings
        provider = provider_from_settings(s)
    assistant = Assistant(s, provider, MemoryCache())
    source = LocalSource(corpus, user=user)
    cases = json.loads(Path(questions_path).read_text())

    rows = []
    for case in cases:
        expect = case.get("expect")
        found = await assistant.retrieve(source, case["question"])
        gated = found.coverage < s.min_coverage
        docs = [Path(c.doc_key.split("@")[0]).name for c in found.chunks]
        leaked = sorted(set(case.get("forbid", [])) & set(docs))
        ok = True if expect is None else (not gated and expect in docs)
        row = {"question": case["question"], "expect": expect, "retrieval_ok": ok and not leaked,
               "leaked": leaked, "gated": gated,
               "coverage": round(found.coverage, 2), "first_doc": docs[0] if docs else None}

        if answers and not gated:
            text, cited = "", []
            async for name, data in assistant.ask(source, case["question"], user="eval"):
                if name == "delta":
                    text += data["text"]
                elif name == "citations":
                    cited = [Path(c["url"]).name for c in data]
            refused = text.strip().startswith(s.refusal_text[:40])
            if expect is None:
                row["answer_ok"] = refused
            else:
                phrase = case.get("contains")
                row["answer_ok"] = expect in cited and (not phrase or phrase.lower() in text.lower())
            row["answer"] = text.strip()[:300]
        elif answers:
            row["answer_ok"] = expect is None
        rows.append(row)

    answerable = [r for r in rows if r["expect"]]
    unanswerable = [r for r in rows if not r["expect"]]
    summary = {
        "questions": len(rows),
        "retrieval_hit_rate": round(sum(r["retrieval_ok"] for r in answerable) / max(len(answerable), 1), 3),
        "permission_leaks": sum(len(r["leaked"]) for r in rows),
        "refused_before_model": sum(r["gated"] for r in unanswerable),
        "unanswerable": len(unanswerable),
    }
    if answers:
        summary["answer_accuracy"] = round(sum(r.get("answer_ok", False) for r in rows) / len(rows), 3)
        summary["refusal_accuracy"] = round(sum(r.get("answer_ok", False) for r in unanswerable) / max(len(unanswerable), 1), 3)
    return {"summary": summary, "results": rows}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Groundwork evaluation")
    p.add_argument("--corpus", default="evals/corpus")
    p.add_argument("--questions", default="evals/questions.json")
    p.add_argument("--answers", action="store_true", help="generate answers with the configured provider")
    p.add_argument("--user", default=None, help="local user name, for permission-trimmed corpora")
    p.add_argument("--min-hit-rate", type=float, default=0.9)
    p.add_argument("--report", default=None, help="write the full JSON report here")
    args = p.parse_args(argv)

    report = asyncio.run(run(args.corpus, args.questions, args.answers, args.user))
    for r in report["results"]:
        if r["expect"] is None and "answer_ok" not in r:
            mark = "GATE" if r["gated"] else "MODEL"     # refusal judged in --answers mode
        else:
            mark = "PASS" if r["retrieval_ok"] and r.get("answer_ok", True) else "FAIL"
        print(f"{mark}  {r['coverage']:>5}  {(r['first_doc'] or '-'):<28} {r['question']}")
    print(json.dumps(report["summary"], indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2))

    s = report["summary"]
    passed = s["retrieval_hit_rate"] >= args.min_hit_rate and s["permission_leaks"] == 0
    if args.answers:
        passed = passed and s["answer_accuracy"] >= args.min_hit_rate
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
