from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.agents.graph import get_agent
from src.api.auth import get_current_user
from src.api.public_contract import public_citations
from src.application.adapters import LangGraphAgentAdapter
from src.application.answer import AnswerLegalQuestion, StreamLegalQuestion
from src.config import get_settings
from src.integrations.langfuse import configure_langfuse, trace_span, tracing_enabled
from src.models.schemas import (
    AgentStatusResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    ChatRequest,
    ChatResponse,
)
from src.services.chat import ChatProviderError, GraphRagUnavailableError
from src.services.conversation_context import build_conversation_anchors, resolve_conversational_query
from src.services.conversations import ConversationStoreError, get_conversation_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent"])


def _langgraph_provider():
    return get_agent()


def _answer_use_case() -> AnswerLegalQuestion:
    return AnswerLegalQuestion(LangGraphAgentAdapter(_langgraph_provider))


async def run_agent(
    message: str,
    *,
    feature: str = "chat",
    request_id: str | None = None,
    conversation_id: str | None = None,
    turn_id: str | None = None,
    owner_uid: str | None = None,
) -> dict:
    try:
        return await _invoke_agent(
            message,
            feature=feature,
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            owner_uid=owner_uid,
        )
    except GraphRagUnavailableError as exc:
        logger.exception("GraphRAG retrieval failure")
        raise HTTPException(status_code=503, detail="GraphRAG service unavailable") from exc
    except ChatProviderError as exc:
        logger.exception("Chat provider failure")
        raise HTTPException(status_code=502, detail="Chat provider unavailable") from exc
    except RuntimeError as exc:
        logger.exception("Agent runtime failure")
        raise HTTPException(status_code=503, detail="Agent service unavailable") from exc
    except Exception as exc:
        logger.exception("Agent request failure")
        raise HTTPException(status_code=500, detail="Internal agent error") from exc


async def _invoke_agent(
    message: str,
    *,
    feature: str,
    request_id: str | None,
    conversation_id: str | None = None,
    turn_id: str | None = None,
    owner_uid: str | None = None,
) -> dict:
    resolved_message = message
    if owner_uid and conversation_id:
        try:
            turns = await get_conversation_store().recent_turns(
                owner_uid=owner_uid, conversation_id=conversation_id
            )
            resolved_message = resolve_conversational_query(message, turns)
        except Exception:
            logger.exception("Conversation context resolution failed", extra={"request_id": request_id})
    if not tracing_enabled():
        return await _answer_use_case().execute(resolved_message)

    configure_langfuse()
    from langfuse import get_client, propagate_attributes

    trace_name = "chat-response" if feature == "chat" else "analyze-request"
    settings = get_settings()
    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="span", name=trace_name) as span:
        span.update(input={"message": resolved_message})
        with propagate_attributes(
            session_id=conversation_id or request_id,
            tags=[feature, "graphrag"],
            environment=settings.app_env,
            metadata={
                "request_id": request_id or "",
                "feature": feature,
                "conversation_id": conversation_id or "",
                "turn_id": turn_id or "",
            },
        ):
            result = await _answer_use_case().execute(resolved_message)
        response = result.get("response", "")
        citations = result.get("citations") or []
        span.update(
            output={
                "response": response if isinstance(response, str) else "",
                "citation_count": len(citations),
                "claim_count": len(result.get("claims") or []),
            }
        )
        return result


def _sse_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _persist_chat_turn(
    *,
    owner_uid: str,
    request: ChatRequest,
    response: str,
    citations: list[dict],
    claims: list[dict],
    request_id: str,
) -> None:
    if not request.conversation_id or not request.turn_id:
        return
    try:
        await get_conversation_store().append_turn(
            owner_uid=owner_uid,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            user_message=request.message,
            assistant_response=response,
            citations=citations,
            claims=claims,
            anchors=build_conversation_anchors(citations),
            request_id=request_id,
        )
    except ConversationStoreError as exc:
        logger.warning("Conversation turn rejected", extra={"reason": str(exc), "request_id": request_id})
    except Exception:
        logger.exception("Conversation turn persistence failed", extra={"request_id": request_id})


async def _stream_agent(
    message: str,
    *,
    request_id: str | None,
    conversation_id: str = "",
    turn_id: str = "",
    owner_uid: str = "",
    http_request: Request,
) -> AsyncIterator[str]:
    """Stream safe lifecycle/final events from the verified LangGraph run.

    Raw model tokens are intentionally not forwarded: the guardrail and
    citation builder must finish first, otherwise an unsupported token could
    reach the browser before verification. Clients still get immediate stage
    progress and can cancel the request without changing the JSON endpoint.
    """
    resolved_message = message
    if owner_uid and conversation_id:
        try:
            turns = await get_conversation_store().recent_turns(
                owner_uid=owner_uid, conversation_id=conversation_id
            )
            resolved_message = resolve_conversational_query(message, turns)
        except Exception:
            logger.exception("Conversation stream context resolution failed", extra={"request_id": request_id})
    # Stable IDs are passed through to persistence; stream tracing remains a
    # separate contract until provider token callbacks are wired.
    final: dict | None = None
    stream_use_case = StreamLegalQuestion(LangGraphAgentAdapter(_langgraph_provider))
    try:
        yield _sse_event("status", {"stage": "started"})
        async with trace_span(
            "chat-stream",
            as_type="span",
            input={"message": resolved_message},
            metadata={
                "request_id": request_id or "",
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "feature": "chat-stream",
            },
        ) as stream_span:
            event_stream = stream_use_case.execute(resolved_message)
            try:
                async for event in event_stream:
                    if await http_request.is_disconnected():
                        return
                    event_name = str(event.get("name") or "")
                    event_type = str(event.get("event") or "")
                    if event_type == "on_chain_start" and event_name in {
                        "retrieve_vectors", "assemble_context", "verify_evidence", "generate", "guardrail"
                    }:
                        yield _sse_event("status", {"stage": event_name})
                    if event_type == "on_chain_end" and event_name == "guardrail":
                        output = event.get("data", {}).get("output")
                        if isinstance(output, dict):
                            final = output
            finally:
                close_stream = getattr(event_stream, "aclose", None)
                if close_stream is not None:
                    await close_stream()
            if stream_span is not None:
                stream_span.update(output={"verified": bool(final), "citation_count": len((final or {}).get("citations") or [])})
        if not final:
            raise RuntimeError("Agent stream ended without a verified final event")
        response = final.get("response")
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("Agent returned an empty response")
        browser_citations = public_citations(final.get("citations") or [])
        yield _sse_event(
            "final",
            {
                "response": response.strip(),
                "citations": [citation.model_dump() for citation in browser_citations],
            },
        )
        await _persist_chat_turn(
            owner_uid=owner_uid,
            request=ChatRequest(message=message, conversation_id=conversation_id, turn_id=turn_id),
            response=response.strip(),
            citations=final.get("citations") or [],
            claims=final.get("claims") or [],
            request_id=request_id or "",
        )
        yield _sse_event("done", {"ok": True})
    except Exception as exc:
        logger.exception("Agent stream failure")
        del exc
        yield _sse_event("error", {"code": "stream_unavailable", "message": "Chat stream unavailable"})


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat with the BHYT GraphRAG agent",
    description=(
        "Send a BHYT or hospital-fee question. The agent retrieves evidence from "
        "the active Supabase release, Qdrant semantic index, and approved Neo4j graph before generating "
        "a grounded answer. No evidence means no answer is invented."
    ),
    responses={
        200: {"description": "Grounded answer and provenance-checked citations."},
        422: {"description": "Invalid request payload."},
        502: {"description": "OpenAI chat provider unavailable."},
        503: {"description": "Required GraphRAG dependency unavailable."},
        500: {"description": "Unexpected internal error."},
    },
)
async def chat(
    request: ChatRequest,
    http_request: Request,
    user: dict = Depends(get_current_user),
) -> ChatResponse:
    result = await run_agent(
        request.message,
        feature="chat",
        request_id=getattr(http_request.state, "request_id", None),
        conversation_id=request.conversation_id,
        turn_id=request.turn_id,
        owner_uid=str(user.get("uid") or ""),
    )
    response = result.get("response")
    if not isinstance(response, str) or not response.strip():
        logger.error("Agent returned empty response")
        raise HTTPException(status_code=502, detail="Chat provider returned an empty response")

    internal_citations = [
        citation for citation in result.get("citations", []) if isinstance(citation, dict)
    ]
    internal_claims = [
        claim for claim in result.get("claims", []) if isinstance(claim, dict)
    ]
    citations = public_citations(internal_citations)
    await _persist_chat_turn(
        owner_uid=str(user.get("uid") or ""),
        request=request,
        response=response.strip(),
        citations=internal_citations,
        claims=internal_claims,
        request_id=getattr(http_request.state, "request_id", "") or "",
    )
    return ChatResponse(response=response.strip(), citations=citations)


@router.post(
    "/chat/stream",
    status_code=status.HTTP_200_OK,
    summary="Stream verified chat lifecycle and final answer",
    responses={200: {"description": "Server-sent events; final is emitted only after guardrails."}},
)
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    return StreamingResponse(
        _stream_agent(
            request.message,
            request_id=getattr(http_request.state, "request_id", None),
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            owner_uid=str(user.get("uid") or ""),
            http_request=http_request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze user input",
    description="Compatibility endpoint for non-conversational analysis.",
)
async def analyze(
    request: AnalyzeRequest,
    http_request: Request,
    _user: dict = Depends(get_current_user),
) -> AnalyzeResponse:
    result = await run_agent(
        request.message,
        feature="analyze",
        request_id=getattr(http_request.state, "request_id", None),
    )
    return AnalyzeResponse(analysis=result.get("response", ""))


@router.get(
    "/status",
    response_model=AgentStatusResponse,
    summary="Get agent status",
    description="Check whether the LangGraph GraphRAG agent is available.",
)
async def agent_status() -> AgentStatusResponse:
    return AgentStatusResponse(status="ready", agent="LangGraph GraphRAG Agent v1.0")
