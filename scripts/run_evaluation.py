import argparse
import asyncio
import shlex
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if (BACKEND_ROOT / "app").is_dir():
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.runner import (  # noqa: E402
    EvaluationRuntime,
    evaluate_cases,
    load_cases,
    write_markdown_report,
)


class ClosableRuntime(EvaluationRuntime, Protocol):
    async def close(self) -> None: ...


@dataclass(slots=True)
class RealEvaluationRuntime:
    graph: Any
    qdrant: Any
    redis: Any
    engine: Any
    session_factory: Any

    async def ainvoke(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], await self.graph.ainvoke(dict(state)))

    async def get_ticket_count(self) -> int:
        from sqlalchemy import func, select

        from app.db.models import Ticket

        async with self.session_factory() as session:
            return int(await session.scalar(select(func.count(Ticket.id))) or 0)

    async def close(self) -> None:
        await self.qdrant.close()
        await self.redis.aclose()
        await self.engine.dispose()


def build_runtime() -> RealEvaluationRuntime:
    from qdrant_client import AsyncQdrantClient
    from redis.asyncio import Redis

    from app.agent.graph import GraphDependencies, build_graph
    from app.core.config import get_settings
    from app.db.session import async_engine, async_session_factory
    from app.llm.providers import MockChatProvider, OpenAICompatibleProvider
    from app.rag.ingest import DeterministicEmbedder
    from app.rag.retriever import HybridRetriever, LexicalReranker
    from app.repositories.tickets import TicketRepository
    from app.tickets.service import TicketService

    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    qdrant = AsyncQdrantClient(
        url=str(settings.qdrant_url),
        check_compatibility=False,
    )
    ticket_service = TicketService(TicketRepository(async_session_factory), redis)
    provider = (
        MockChatProvider()
        if settings.model_mode == "mock"
        else OpenAICompatibleProvider(settings)
    )
    retriever = HybridRetriever(
        async_session_factory,
        qdrant,
        DeterministicEmbedder(),
        LexicalReranker(),
    )
    graph = build_graph(
        GraphDependencies(
            retriever=retriever,
            chat_provider=provider,
            ticket_service=ticket_service,
        )
    )
    return RealEvaluationRuntime(
        graph=graph,
        qdrant=qdrant,
        redis=redis,
        engine=async_engine,
        session_factory=async_session_factory,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fixed agent evaluation set.")
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "data" / "eval" / "cases.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "docs" / "evaluation-report.md",
    )
    parser.add_argument("--minimum-recall", type=float, default=0.80)
    parser.add_argument("--minimum-citation-precision", type=float, default=0.85)
    return parser


async def run(
    args: argparse.Namespace,
    *,
    runtime_factory: Callable[[], ClosableRuntime] = build_runtime,
) -> int:
    cases = load_cases(
        args.input,
        knowledge_dir=PROJECT_ROOT / "data" / "knowledge",
    )
    runtime = runtime_factory()
    try:
        results, summary = await evaluate_cases(runtime, cases)
    finally:
        await runtime.close()

    command = "python scripts/run_evaluation.py " + " ".join(
        (
            "--input",
            shlex.quote(str(args.input)),
            "--report",
            shlex.quote(str(args.report)),
        )
    )
    write_markdown_report(
        args.report,
        summary,
        results,
        dataset_path=args.input,
        command=command,
    )
    print(
        f"cases={summary.total_cases} recall_at_5={summary.recall_at_5:.3f} "
        f"citation_precision={summary.citation_precision:.3f} "
        f"final_state_pass_rate={summary.final_state_pass_rate:.3f} "
        f"tool_behavior_pass_rate={summary.tool_behavior_pass_rate:.3f} "
        f"unconfirmed_ticket_writes={summary.unconfirmed_ticket_writes} "
        f"p50_ms={summary.p50_latency_ms:.2f} p95_ms={summary.p95_latency_ms:.2f}"
    )
    if summary.failed_case_ids:
        print("failed_cases=" + ",".join(summary.failed_case_ids))
    return int(
        summary.recall_at_5 < args.minimum_recall
        or summary.citation_precision < args.minimum_citation_precision
        or summary.unconfirmed_ticket_writes > 0
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: Callable[[], ClosableRuntime] = build_runtime,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args, runtime_factory=runtime_factory))
    except (OSError, ValueError) as error:
        print(f"evaluation error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
