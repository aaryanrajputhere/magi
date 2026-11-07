"""Inspect local model logs or explicitly invoke a small, billable smoke test."""

import argparse
import json
import sys

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import create_database_engine, initialize_database
from app.llm.client import ModelRequest, ModelRunError, RunLoggingError, TrackedOpenAI
from app.models import LLMRun


def run_record(run: LLMRun, *, full: bool) -> dict:
    fields = (
        [column.key for column in inspect(LLMRun).columns]
        if full
        else [
            "id",
            "operation",
            "requested_model",
            "returned_model",
            "started_at",
            "status",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "estimated_cost_usd",
            "cost_status",
            "cost_reason",
            "cache_read_ratio",
            "duration_ms",
        ]
    )
    result = {key: getattr(run, key) for key in fields}
    if full:
        if run.operation in {"embed_chunks", "embed_queries"} and run.cost_status == "estimated":
            result["pricing_calculation"] = {
                "formula": "input_tokens * input_rate / 1000000",
                "input_tokens": run.input_tokens,
                "rates": run.pricing_snapshot,
                "total_estimated_usd": run.estimated_cost_usd,
                "note": "Embedding input usage from the provider; recovered runs are not billed again locally.",
            }
        elif (
            run.cost_status == "estimated"
            and run.input_tokens is not None
            and run.cached_input_tokens is not None
        ):
            result["pricing_calculation"] = {
                "formula": "((input - cached - writes) * input_rate + cached * cached_rate + writes * write_rate + output * output_rate) / 1000000",
                "ordinary_input_tokens": run.input_tokens
                - run.cached_input_tokens
                - (run.cache_write_tokens or 0),
                "cached_input_tokens": run.cached_input_tokens,
                "cache_write_tokens_reported": run.cache_write_tokens,
                "output_tokens": run.output_tokens,
                "rates": run.pricing_snapshot,
                "total_estimated_usd": run.estimated_cost_usd,
                "note": "Uses recorded rates and total, not current registry pricing. Reasoning is already included in output.",
            }
        else:
            result["pricing_calculation"] = {"status": run.cost_status, "reason": run.cost_reason}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    show = commands.add_parser("show")
    show.add_argument("run_id")
    smoke = commands.add_parser("smoke", help="Makes one billable API request")
    smoke.add_argument("--model", default="gpt-6-luna")
    args = parser.parse_args()
    if args.command == "list" and not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    engine = None
    client = None
    try:
        settings = get_settings()
        engine = create_database_engine(settings.magi_database_url)
        initialize_database(engine)
        if args.command == "smoke":
            client = TrackedOpenAI(engine, settings)
            result = client.run(
                ModelRequest(
                    operation="smoke_test",
                    model=args.model,
                    instructions="Reply briefly and exactly as requested.",
                    input="Reply with the word ready.",
                    max_output_tokens=64,
                )
            )
            print(json.dumps({"run_id": result.run_id, "text": result.text}))
        else:
            with Session(engine) as session:
                if args.command == "list":
                    result = [
                        run_record(run, full=False)
                        for run in session.scalars(
                            select(LLMRun)
                            .order_by(LLMRun.started_at.desc(), LLMRun.id)
                            .limit(args.limit)
                        )
                    ]
                else:
                    run = session.get(LLMRun, args.run_id)
                    if run is None:
                        raise ValueError(f"Unknown run: {args.run_id}")
                    result = run_record(run, full=True)
                print(json.dumps(result, indent=2, default=str))
        return 0
    except (SQLAlchemyError, ValueError, ModelRunError, RunLoggingError) as exc:
        result = {"error": str(exc)}
        if isinstance(exc, (ModelRunError, RunLoggingError)):
            result["run_id"] = exc.run_id
        print(json.dumps(result), file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
