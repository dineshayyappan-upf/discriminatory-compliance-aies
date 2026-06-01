#!/usr/bin/env python3
"""
Standalone replication pipeline for collecting a comparable v3 dataset.

This folder intentionally contains no collected responses and no derived report
artifacts. It builds prompt matrices, builds judge-classification prompts, and
validates a newly collected/tagged JSONL.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import config as study


PUBLISH_DIR = Path(__file__).resolve().parent
DEFAULT_REQUESTS = PUBLISH_DIR / "replication_requests.jsonl"
DEFAULT_JUDGE_REQUESTS = PUBLISH_DIR / "judge_requests.jsonl"
VALID_TAG_VALUES = {"Yes", "No"}


def record_id(model: str, query_id: str, condition: str, phrasing: str) -> str:
    return f"{model}__{query_id}__{condition}__{phrasing}"


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            row["_line_num"] = line_num
            yield row


def selected_models(args: argparse.Namespace) -> list[str]:
    return list(args.models or study.DEFAULT_MODEL_LABELS)


def selected_queries(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = list(study.QUERIES)
    if getattr(args, "query_ids", None):
        requested = set(args.query_ids)
        rows = [row for row in rows if row["id"] in requested]
    if getattr(args, "limit_queries", None):
        rows = rows[: args.limit_queries]
    return rows


def selected_conditions(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = list(study.CONDITIONS)
    if getattr(args, "categories", None):
        categories = set(args.categories)
        rows = [row for row in rows if row["category"] in categories]
    if getattr(args, "condition_names", None):
        names = set(args.condition_names)
        rows = [row for row in rows if row["condition"] in names]
    return rows


def build_prompt(query_text: str, disclosure_text: str) -> str:
    if not disclosure_text:
        return query_text
    return f"{disclosure_text} {query_text}"


def request_rows(
    models: list[str],
    queries: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    include_controls: bool,
) -> Iterable[dict[str, Any]]:
    for model in models:
        for query in queries:
            if include_controls:
                yield {
                    "request_id": record_id(model, query["id"], "bare", "none"),
                    "model": model,
                    "query_id": query["id"],
                    "query_text": query["query"],
                    "query_type": query["type"],
                    "cluster": query["cluster"],
                    "condition": "bare",
                    "category": "control",
                    "phrasing": "none",
                    "prompt_type": "control",
                    "disclosure_text": "",
                    "prompt": query["query"],
                }
            for condition in conditions:
                for phrasing in study.PHRASING_NAMES:
                    disclosure = condition[phrasing]
                    yield {
                        "request_id": record_id(
                            model, query["id"], condition["condition"], phrasing
                        ),
                        "model": model,
                        "query_id": query["id"],
                        "query_text": query["query"],
                        "query_type": query["type"],
                        "cluster": query["cluster"],
                        "condition": condition["condition"],
                        "category": condition["category"],
                        "matches_condition": condition.get("matches_condition", ""),
                        "phrasing": phrasing,
                        "prompt_type": "variant",
                        "disclosure_text": disclosure,
                        "prompt": build_prompt(query["query"], disclosure),
                    }


def matrix_shape(args: argparse.Namespace) -> dict[str, Any]:
    models = selected_models(args)
    queries = selected_queries(args)
    conditions = selected_conditions(args)
    controls_per_model = len(queries) if not args.no_controls else 0
    variants_per_model = len(queries) * len(conditions) * len(study.PHRASING_NAMES)
    per_model = controls_per_model + variants_per_model
    return {
        "models": models,
        "queries": queries,
        "conditions": conditions,
        "controls_per_model": controls_per_model,
        "variants_per_model": variants_per_model,
        "records_per_model": per_model,
        "total_records": per_model * len(models),
    }


def print_plan(args: argparse.Namespace) -> int:
    shape = matrix_shape(args)
    categories = Counter(row["category"] for row in shape["conditions"])
    print("# Replication Collection Plan")
    print()
    print(f"Models: {len(shape['models'])} ({', '.join(shape['models'])})")
    print(f"Queries: {len(shape['queries'])}")
    print(f"Conditions: {len(shape['conditions'])}")
    print(f"Condition categories: {dict(sorted(categories.items()))}")
    print(f"Phrasings per condition: {len(study.PHRASING_NAMES)}")
    print(f"Controls/model: {shape['controls_per_model']}")
    print(f"Variants/model: {shape['variants_per_model']}")
    print(f"Records/model: {shape['records_per_model']}")
    print(f"Total generation requests: {shape['total_records']}")
    print(f"Total judge requests after collection: {shape['total_records']}")
    return 0


def build_requests(args: argparse.Namespace) -> int:
    shape = matrix_shape(args)
    rows = request_rows(
        shape["models"],
        shape["queries"],
        shape["conditions"],
        include_controls=not args.no_controls,
    )
    count = write_jsonl(args.output, rows)
    print(f"Wrote {count:,} generation requests -> {args.output}")
    return 0


def build_judge_requests(args: argparse.Namespace) -> int:
    def rows() -> Iterable[dict[str, Any]]:
        for row in iter_jsonl(args.responses):
            response = row.get("response", "")
            query_text = row.get("query_text") or row.get("query") or ""
            request = {
                k: v
                for k, v in row.items()
                if not k.startswith("_") and k not in {"response", "tags", "judge_raw"}
            }
            yield {
                "judge_request_id": f"judge__{row.get('request_id') or row.get('_line_num')}",
                "request_id": row.get("request_id", ""),
                "model": row.get("model", ""),
                "query_id": row.get("query_id", ""),
                "condition": row.get("condition", ""),
                "phrasing": row.get("phrasing", ""),
                "prompt": study.BEHAVIORAL_TAG_JUDGE_PROMPT.format(
                    user_query=query_text,
                    response=response,
                ),
                "source_request": request,
            }

    count = write_jsonl(args.output, rows())
    print(f"Wrote {count:,} judge requests -> {args.output}")
    return 0


def validation_errors(path: Path, require_tags: bool) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    required_fields = {
        "request_id",
        "model",
        "query_id",
        "query_text",
        "condition",
        "category",
        "phrasing",
        "prompt_type",
        "response",
    }
    row_count = 0
    for row in iter_jsonl(path):
        row_count += 1
        label = f"line {row['_line_num']}"
        missing = sorted(field for field in required_fields if field not in row)
        if missing:
            errors.append(f"{label}: missing fields {', '.join(missing)}")
        request_id = row.get("request_id")
        if request_id:
            if request_id in seen_ids:
                errors.append(f"{label}: duplicate request_id {request_id}")
            seen_ids.add(request_id)
        if require_tags:
            tags = row.get("tags")
            if not isinstance(tags, dict):
                errors.append(f"{label}: missing tags object")
            else:
                for tag in study.BEHAVIORAL_TAG_NAMES:
                    if tags.get(tag) not in VALID_TAG_VALUES:
                        errors.append(f"{label}: invalid or missing tag {tag}")
    if row_count == 0:
        errors.append("input contains no records")
    return errors


def validate(args: argparse.Namespace) -> int:
    errors = validation_errors(args.input, args.require_tags)
    if errors:
        print(f"Validation failed with {len(errors)} error(s):")
        for error in errors[:50]:
            print(f"- {error}")
        if len(errors) > 50:
            print(f"- ... {len(errors) - 50} more")
        return 1
    print(f"Validation passed: {args.input}")
    return 0


def synthetic_tagged_rows() -> list[dict[str, Any]]:
    args = argparse.Namespace(
        models=["smoke_model"],
        query_ids=None,
        limit_queries=2,
        categories=None,
        condition_names=[study.CONDITIONS[0]["condition"], study.CONDITIONS[1]["condition"]],
        no_controls=False,
    )
    rows = []
    for idx, row in enumerate(
        request_rows(
            selected_models(args),
            selected_queries(args),
            selected_conditions(args),
            include_controls=True,
        )
    ):
        tags = {
            tag: "Yes" if (idx + tag_idx) % 3 == 0 else "No"
            for tag_idx, tag in enumerate(study.BEHAVIORAL_TAG_NAMES)
        }
        row["response"] = "Synthetic response for smoke testing only."
        row["tags"] = tags
        rows.append(row)
    return rows


def smoke_test(args: argparse.Namespace) -> int:
    rows = synthetic_tagged_rows()
    request_ids = [row["request_id"] for row in rows]
    if len(request_ids) != len(set(request_ids)):
        print("Smoke test failed: duplicate synthetic request ids")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        tagged_path = tmp_dir / "synthetic_tagged.jsonl"
        write_jsonl(tagged_path, rows)
        errors = validation_errors(tagged_path, require_tags=True)
        if errors:
            print("Smoke test failed during validation:")
            for error in errors:
                print(f"- {error}")
            return 1
    print(f"Smoke test passed: {len(rows)} synthetic tagged records exercised.")
    return 0


def add_matrix_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--models", nargs="+", default=None)
    parser.add_argument("--query-ids", nargs="+", default=None)
    parser.add_argument("--limit-queries", type=int, default=None)
    parser.add_argument(
        "--categories",
        nargs="+",
        choices=["baseline", "type1", "type2", "subclinical"],
        default=None,
    )
    parser.add_argument("--condition-names", nargs="+", default=None)
    parser.add_argument("--no-controls", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="Print collection matrix counts.")
    add_matrix_args(p)
    p.set_defaults(func=print_plan)

    p = sub.add_parser("build-requests", help="Write generation prompt requests.")
    add_matrix_args(p)
    p.add_argument("--output", type=Path, default=DEFAULT_REQUESTS)
    p.set_defaults(func=build_requests)

    p = sub.add_parser("build-judge-requests", help="Write judge-classification prompts.")
    p.add_argument("--responses", type=Path, required=True)
    p.add_argument("--output", type=Path, default=DEFAULT_JUDGE_REQUESTS)
    p.set_defaults(func=build_judge_requests)

    p = sub.add_parser("validate", help="Validate collected or tagged JSONL schema.")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--require-tags", action="store_true")
    p.set_defaults(func=validate)

    p = sub.add_parser("smoke-test", help="Run an in-memory synthetic smoke test.")
    p.set_defaults(func=smoke_test)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
