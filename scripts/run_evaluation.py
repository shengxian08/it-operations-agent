import argparse
import asyncio
import inspect
import math
import shlex
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
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
    write_json_report,
    release_gate,
)


class ClosableRuntime(EvaluationRuntime, Protocol):
    async def close(self) -> None: ...


class ReadOnlyEvaluationTickets:
    """Tool selection harness; real confirmation transactions have separate DB tests."""
    def __init__(self, service):
        self.service = service

    async def get_ticket_status(self, user_id, ticket_number):
        return await self.service.get_ticket_status(user_id, ticket_number)

    async def issue_confirmation_token(self, conversation_id, draft, **kwargs):
        return "evaluation-only-not-valid-for-ticket-creation"


@dataclass(slots=True)
class RealEvaluationRuntime:
    graph: Any
    qdrant: Any
    redis: Any
    engine: Any
    session_factory: Any
    provider: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    user_id: str = "u-001"
    conversation_id: str = "c-001"

    async def ainvoke(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], await self.graph.ainvoke(dict(state)))

    async def get_ticket_count(self) -> int:
        from sqlalchemy import func, select

        from app.production.business_models import ProductionTicket

        async with self.session_factory() as session:
            return int(await session.scalar(select(func.count(ProductionTicket.id))) or 0)

    async def close(self) -> None:
        if self.provider and getattr(self.provider, "close", None):
            await self.provider.close()
        await self.qdrant.close()
        if self.redis is not None:
            await self.redis.aclose()
        await self.engine.dispose()


async def build_runtime(*, index_revision=None, user_id=None, conversation_id=None, demo=False) -> RealEvaluationRuntime:
    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.agent.graph import GraphDependencies, build_graph
    from app.core.config import get_settings
    from app.llm.providers import MockChatProvider, OpenAICompatibleProvider
    from app.production.business import ProductionTicketService
    from app.production.knowledge import build_retriever, embedding_identity, expected_dimensions, pipeline_config

    settings = get_settings()
    if demo:
        return await build_demo_runtime(settings)
    if not user_id or not conversation_id:
        raise ValueError("production evaluation requires --user-id and --conversation-id owned by that user")
    async_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    qdrant = AsyncQdrantClient(
        url=str(settings.qdrant_url),
        check_compatibility=False,
        api_key=settings.qdrant_read_api_key.get_secret_value() if settings.qdrant_read_api_key else None,
    )
    ticket_service = ReadOnlyEvaluationTickets(ProductionTicketService(async_session_factory, settings))
    provider = (
        MockChatProvider()
        if settings.model_mode == "mock"
        else OpenAICompatibleProvider(settings)
    )
    try:
        from sqlalchemy import select
        from app.db.models import User, Conversation
        from app.production.identity_models import IdentityAccount
        async with async_session_factory() as session:
            user = await session.get(User, user_id)
            account = await session.scalar(select(IdentityAccount).where(IdentityAccount.user_id == user_id))
            conversation = await session.get(Conversation, conversation_id)
            if user is None or account is None or not account.enabled or conversation is None or conversation.user_id != user_id:
                raise ValueError("evaluation identity must be enabled and own its conversation")
            access_level = user.access_level
        retriever = await build_retriever(async_session_factory, qdrant, settings, index_revision=index_revision)
        graph = build_graph(GraphDependencies(retriever=retriever, chat_provider=provider,
            ticket_service=ticket_service, minimum_evidence_score=settings.minimum_evidence_score,
            require_structured_citations=True, user_access_level=access_level))
    except BaseException:
        if getattr(provider, "close", None):
            await provider.close()
        await qdrant.close()
        await async_engine.dispose()
        raise
    model, revision = embedding_identity(settings)
    return RealEvaluationRuntime(
        graph=graph,
        qdrant=qdrant,
        redis=None,
        engine=async_engine,
        session_factory=async_session_factory,
        provider=provider,
        user_id=user_id, conversation_id=conversation_id,
        metadata={"index_revision": retriever.revision.id if retriever.revision else "",
            "embedding_model": model, "embedding_revision": revision, "dimensions": expected_dimensions(settings),
            "pipeline_config": pipeline_config(settings), "model_mode": settings.model_mode,
            "model": settings.openai_model if settings.model_mode == "openai" else "mock",
            "evaluation_kind": "production_model_quality" if settings.model_mode == "openai" and settings.embedding_mode == "sentence-transformer" else "regression",
            "environment": settings.environment,
            "user_id": user_id, "access_level": access_level,
            "business_adapter": "read-only tool selection; transaction behavior covered by integration tests"},
    )


async def build_demo_runtime(settings):
    if settings.environment in {"production", "staging"} or not settings.demo_enabled:
        raise ValueError("demo evaluation is forbidden in production/staging")
    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.agent.graph import GraphDependencies, build_graph
    from app.llm.providers import MockChatProvider
    from app.production.knowledge import build_embedder, embedding_identity, expected_dimensions
    from app.rag.retriever import HybridRetriever, LexicalReranker
    from app.repositories.tickets import TicketRepository
    from app.tickets.service import TicketService
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    try:
        embedder = await asyncio.to_thread(build_embedder, settings)
        retriever = HybridRetriever(sessions, qdrant, embedder, LexicalReranker(), collection_name=settings.qdrant_collection)
        graph = build_graph(GraphDependencies(retriever=retriever, chat_provider=MockChatProvider(),
            ticket_service=ReadOnlyEvaluationTickets(TicketService(TicketRepository(sessions), None))))
    except BaseException:
        await qdrant.close()
        await engine.dispose()
        raise
    model, revision = embedding_identity(settings)
    # Counts in this explicit legacy path must refer to legacy formal ticket rows.
    class DemoRuntime(RealEvaluationRuntime):
        async def get_ticket_count(self):
            from sqlalchemy import func, select
            from app.db.models import Ticket
            async with self.session_factory() as session:
                return int(await session.scalar(select(func.count(Ticket.id))) or 0)
    return DemoRuntime(graph, qdrant, None, engine, sessions, metadata={
        "evaluation_kind": "demo_regression", "environment": settings.environment, "model_mode": "mock",
        "model": "mock", "embedding_model": model, "embedding_revision": revision,
        "dimensions": expected_dimensions(settings), "index_revision": None,
        "business_adapter": "read-only legacy tool selection; no production transaction evidence"})


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
    parser.add_argument("--json-report", type=Path, help="Defaults to the Markdown report path with .json extension.")
    parser.add_argument("--index-revision", help="Pinned published revision; default resolves active once.")
    parser.add_argument("--user-id", help="Enabled production evaluation user.")
    parser.add_argument("--conversation-id", help="Existing conversation owned by the evaluation user.")
    parser.add_argument("--demo", action="store_true", help="Development/test legacy logic regression; release quality gate still applies to the report.")
    return parser


async def run(
    args: argparse.Namespace,
    *,
    runtime_factory: Callable[[], ClosableRuntime] = build_runtime,
) -> int:
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in (args.minimum_recall, args.minimum_citation_precision)):
        raise ValueError("evaluation thresholds must be finite fractions between zero and one")
    cases = load_cases(
        args.input,
    )
    runtime = runtime_factory(index_revision=args.index_revision, user_id=args.user_id,
        conversation_id=args.conversation_id, demo=args.demo) if runtime_factory is build_runtime else runtime_factory()
    if inspect.isawaitable(runtime):
        runtime = await runtime
    try:
        results, summary = await evaluate_cases(runtime, cases)
        metadata = getattr(runtime, "metadata", {})
    finally:
        await runtime.close()

    arguments = ["--input", str(args.input), "--report", str(args.report)]
    for name in ("json_report", "index_revision", "user_id", "conversation_id"):
        if getattr(args, name):
            arguments.extend(["--" + name.replace("_", "-"), str(getattr(args, name))])
    if args.demo:
        arguments.append("--demo")
    arguments.extend(["--minimum-recall", str(args.minimum_recall), "--minimum-citation-precision", str(args.minimum_citation_precision)])
    command = "python scripts/run_evaluation.py " + shlex.join(arguments)
    gate = release_gate(summary, minimum_recall=args.minimum_recall, minimum_citation_precision=args.minimum_citation_precision)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_markdown_report(
        args.report,
        summary,
        results,
        dataset_path=args.input,
        command=command,
        gate=gate,
    )
    json_report = args.json_report or args.report.with_suffix(".json")
    write_json_report(json_report, summary, results, dataset_path=args.input, command=command, metadata=metadata, gate=gate)
    precision = f"{summary.citation_precision:.3f}" if summary.citation_precision is not None else "unmeasured"
    print(
        f"cases={summary.total_cases} recall_at_5={summary.recall_at_5:.3f} "
        f"required_source_coverage={summary.required_source_coverage:.3f} "
        f"citation_precision={precision} "
        f"final_state_pass_rate={summary.final_state_pass_rate:.3f} "
        f"tool_behavior_pass_rate={summary.tool_behavior_pass_rate:.3f} "
        f"unconfirmed_ticket_writes={summary.unconfirmed_ticket_writes} "
        f"p50_ms={summary.p50_latency_ms:.2f} p95_ms={summary.p95_latency_ms:.2f}"
    )
    if summary.failed_case_ids:
        print("failed_cases=" + ",".join(summary.failed_case_ids))
    print("release_gate=" + ("PASS" if gate.passed else "FAIL") + " blockers=" + ",".join(gate.reasons))
    print(f"json_report={json_report}")
    if args.demo:
        regression_passed = (summary.recall_at_5 >= args.minimum_recall
            and summary.required_source_coverage >= args.minimum_citation_precision
            and summary.final_state_pass_rate == summary.tool_behavior_pass_rate == summary.handoff_reason_pass_rate == summary.critical_behavior_pass_rate == 1
            and summary.unconfirmed_ticket_writes == 0)
        print("demo_regression=" + ("PASS" if regression_passed else "FAIL") + "; release evidence remains the JSON release_gate")
        return int(not regression_passed)
    return int(not gate.passed)


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: Callable[[], ClosableRuntime] = build_runtime,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args, runtime_factory=runtime_factory))
    except (OSError, ValueError, TypeError) as error:
        print(f"evaluation error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
