import json
import uuid
import time
import logging
from dataclasses import dataclass, field

from analytics_agent.agents.supervisor import run_supervisor, SupervisorError
from analytics_agent.agents.query import run_query_agent, QueryError
from analytics_agent.agents.summarize import run_summarize_agent, SummarizeError
from analytics_agent.gpt_client import LLMClientError

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    correlation_id: str
    summary: str | None = None
    chart_spec: dict | None = None
    rows: list[dict] = field(default_factory=list)
    clarification: str | None = None
    error: str | None = None
    log: dict = field(default_factory=dict)


def run_pipeline(question: str, registry: dict) -> PipelineResult:
    correlation_id = str(uuid.uuid4())
    log: dict = {"correlation_id": correlation_id, "question": question}
    t0 = time.monotonic()

    try:
        t1 = time.monotonic()
        supervisor_result = run_supervisor(question, registry)
        log["supervisor"] = {
            "table_selected": supervisor_result.table,
            "confidence": supervisor_result.confidence,
            "reasoning": supervisor_result.reasoning,
            "unexpected_confidence": supervisor_result.unexpected_confidence,
            "latency_ms": int((time.monotonic() - t1) * 1000),
            "raw_response": supervisor_result.raw_response,
        }

        if supervisor_result.confidence == "low":
            log["outcome"] = "clarification"
            log["total_latency_ms"] = int((time.monotonic() - t0) * 1000)
            _emit_log(log)
            return PipelineResult(
                correlation_id=correlation_id,
                clarification=(
                    f"Could you clarify your question? I wasn't confident which data to use. "
                    f"Reasoning: {supervisor_result.reasoning}"
                ),
                log=log,
            )

        t2 = time.monotonic()
        known_tables = set(registry.keys())
        query_result = run_query_agent(question, supervisor_result.table, registry, known_tables)
        log["query"] = {
            "sql": query_result.sql,
            "validator_passed": True,
            "row_count": len(query_result.rows),
            "row_count_before_cap": query_result.row_count_before_cap,
            "capped": query_result.capped,
            "latency_ms": int((time.monotonic() - t2) * 1000),
        }

        if not query_result.rows:
            log["outcome"] = "empty_rows"
            log["total_latency_ms"] = int((time.monotonic() - t0) * 1000)
            _emit_log(log)
            return PipelineResult(
                correlation_id=correlation_id,
                summary="No data found for this period.",
                log=log,
            )

        t3 = time.monotonic()
        summarize_result = run_summarize_agent(question, query_result.rows, query_result.capped)
        log["summarize"] = {
            "chart_spec_valid": summarize_result.chart_spec is not None,
            "chart_invalid_reason": summarize_result.chart_invalid_reason,
            "capped": summarize_result.capped,
            "latency_ms": int((time.monotonic() - t3) * 1000),
            "raw_response": summarize_result.raw_response,
        }

        log["total_latency_ms"] = int((time.monotonic() - t0) * 1000)
        log["outcome"] = "success"
        _emit_log(log)

        return PipelineResult(
            correlation_id=correlation_id,
            summary=summarize_result.summary,
            chart_spec=summarize_result.chart_spec,
            rows=query_result.rows,
            log=log,
        )

    except (SupervisorError, QueryError, SummarizeError, LLMClientError) as e:
        log["error"] = str(e)
        log["total_latency_ms"] = int((time.monotonic() - t0) * 1000)
        log["outcome"] = "error"
        _emit_log(log)
        return PipelineResult(correlation_id=correlation_id, error=str(e), log=log)


def _emit_log(log: dict) -> None:
    logger.info(json.dumps(log, default=str))
