from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from src.agents.graph import get_agent
from src.config import get_settings
from src.integrations.langfuse import configure_langfuse, tracing_enabled
from src.models.schemas import (
    AgentStatusResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    ChatCitation,
    ChatRequest,
    ChatResponse,
)
from src.services.chat import ChatProviderError, GraphRagUnavailableError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent"])


async def run_agent(
    message: str,
    *,
    feature: str = "chat",
    request_id: str | None = None,
) -> dict:
    try:
        return await _invoke_agent(message, feature=feature, request_id=request_id)
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


async def _invoke_agent(message: str, *, feature: str, request_id: str | None) -> dict:
    if not tracing_enabled():
        return await get_agent().ainvoke({"query": message})

    configure_langfuse()
    from langfuse import get_client, propagate_attributes

    trace_name = "chat-response" if feature == "chat" else "analyze-request"
    settings = get_settings()
    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="span", name=trace_name) as span:
        span.update(input={"message": message})
        with propagate_attributes(
            session_id=request_id,
            tags=[feature, "graphrag"],
            environment=settings.app_env,
            metadata={"request_id": request_id or "", "feature": feature},
        ):
            result = await get_agent().ainvoke({"query": message})
        response = result.get("response", "")
        citations = result.get("citations") or []
        span.update(
            output={
                "response": response if isinstance(response, str) else "",
                "citation_count": len(citations),
            }
        )
        return result


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat with the BHYT GraphRAG agent",
    description=(
        "Send a BHYT or hospital-fee question. The agent retrieves evidence from "
        "the active PostgreSQL/pgvector dataset and Neo4j graph before generating "
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
async def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
    result = await run_agent(
        request.message,
        feature="chat",
        request_id=getattr(http_request.state, "request_id", None),
    )
    response = result.get("response")
    if not isinstance(response, str) or not response.strip():
        logger.error("Agent returned empty response")
        raise HTTPException(status_code=502, detail="Chat provider returned an empty response")

    citations = []
    for citation in result.get("citations", []):
        if isinstance(citation, dict):
            allowed = {
                key: citation[key]
                for key in ChatCitation.model_fields
                if key in citation
            }
            citations.append(ChatCitation(**allowed))
    return ChatResponse(response=response.strip(), citations=citations)


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze user input",
    description="Compatibility endpoint for non-conversational analysis.",
)
async def analyze(request: AnalyzeRequest, http_request: Request) -> AnalyzeResponse:
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
