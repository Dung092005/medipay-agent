const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function authorizationHeaders(forceRefresh = false): Promise<Record<string, string>> {
  if (typeof window === "undefined") return {};
  const { auth } = await import("./firebase");
  if (!auth) return {};
  const token = await auth.currentUser?.getIdToken(forceRefresh);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type ChatCitation = {
  title: string;
  document_number: string;
  section_title: string;
  quote: string;
  source_url: string;
  source_checked_at: string;
};

export type AnswerClaim = {
  claim_id: string;
  text: string;
  claim_type: "document" | "status" | "entitlement" | "condition" | "procedure" | "exception" | "general";
  subject: string;
  condition: string;
  entitlement: string;
  exception: string;
  procedure: string;
  effective_from: string;
  evidence_ids: string[];
  source_spans: Array<Array<number | null>>;
  source_hashes: string[];
  verification: "entailed" | "partial" | "unsupported";
  reason: string;
};

export type ChatResponse = {
  response: string;
  citations: ChatCitation[];
  claims?: AnswerClaim[];
};

export type ReviewQueueItem = {
  review_id: string;
  domain: "legal_document" | "hospital_fee_ocr";
  source_id: string;
  title: string;
  status: "pending" | "accepted" | "rejected";
  confidence: number;
  summary: string;
  payload: Record<string, unknown>;
  submitted_by: string;
  assigned_to: string;
  decision_note: string;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  audit: Array<Record<string, unknown>>;
};

export type ChatStreamEvent =
  | { type: "status"; stage: string }
  | { type: "final"; response: string; citations: ChatCitation[]; claims?: AnswerClaim[] }
  | { type: "done"; ok: boolean }
  | { type: "error"; code: string; message: string };

export type ChatTurnContext = {
  conversationId?: string;
  turnId?: string;
};

type ApiError = {
  code?: string;
  message?: string;
};

async function adminRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const authHeaders = await authorizationHeaders();
  let response = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders, ...(init.headers ?? {}) },
  });
  if (response.status === 401) {
    const refreshedHeaders = await authorizationHeaders(true);
    response = await fetch(`${apiUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...refreshedHeaders, ...(init.headers ?? {}) },
    });
  }
  return response;
}

export async function fetchAdminReviews(status = "pending", domain = "all"): Promise<ReviewQueueItem[]> {
  const response = await adminRequest(`/api/v1/auth/admin/reviews?status=${encodeURIComponent(status)}&domain=${encodeURIComponent(domain)}`);
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.message ?? "Không thể tải hàng đợi kiểm duyệt");
  }
  return (await response.json()) as ReviewQueueItem[];
}

export async function decideAdminReview(reviewId: string, status: "accepted" | "rejected", note = ""): Promise<ReviewQueueItem> {
  const response = await adminRequest(`/api/v1/auth/admin/reviews/${encodeURIComponent(reviewId)}`, {
    method: "PATCH",
    body: JSON.stringify({ status, note }),
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.message ?? "Không thể cập nhật bản kiểm duyệt");
  }
  return (await response.json()) as ReviewQueueItem;
}

export async function sendChatMessage(
  message: string,
  context: ChatTurnContext = {},
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const authHeaders = await authorizationHeaders();
  let response = await fetch(`${apiUrl}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ message, conversation_id: context.conversationId ?? "", turn_id: context.turnId ?? "" }),
    signal,
  });
  if (response.status === 401) {
    const refreshedHeaders = await authorizationHeaders(true);
    response = await fetch(`${apiUrl}/api/v1/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...refreshedHeaders },
      body: JSON.stringify({ message, conversation_id: context.conversationId ?? "", turn_id: context.turnId ?? "" }),
      signal,
    });
  }

  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.message ?? "Không thể kết nối MediPay Agent");
  }

  const payload: unknown = await response.json();
  if (!isChatResponse(payload)) {
    throw new Error("API trả dữ liệu chat không đúng định dạng");
  }
  return payload;
}

/** Consume the safe SSE envelope; raw provider tokens are never exposed. */
export async function sendChatMessageStream(
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
  context: ChatTurnContext = {},
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const authHeaders = await authorizationHeaders();
  let response = await fetch(`${apiUrl}/api/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...authHeaders },
    body: JSON.stringify({ message, conversation_id: context.conversationId ?? "", turn_id: context.turnId ?? "" }),
    signal,
  });
  if (response.status === 401) {
    const refreshedHeaders = await authorizationHeaders(true);
    response = await fetch(`${apiUrl}/api/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...refreshedHeaders },
      body: JSON.stringify({ message, conversation_id: context.conversationId ?? "", turn_id: context.turnId ?? "" }),
      signal,
    });
  }
  if (!response.ok || !response.body) {
    const error = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(error?.message ?? "Không thể kết nối MediPay Agent");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final: ChatResponse | null = null;
  while (true) {
    const chunk = await reader.read();
    buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const lines = frame.split("\n");
      const eventType = lines
        .find((line) => line.startsWith("event: "))
        ?.slice(7)
        .trim();
      const dataLine = lines.find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      const data = JSON.parse(dataLine.slice(6)) as Record<string, unknown>;
      // Backend emits SSE event name separately from JSON body.
      const payload = { ...data, type: eventType ?? data.type } as ChatStreamEvent;
      onEvent(payload);
      if (payload.type === "final") {
        final = {
          response: payload.response,
          citations: payload.citations ?? [],
          claims: payload.claims,
        };
      }
      if (payload.type === "error") throw new Error(payload.message);
    }
    if (chunk.done) break;
  }
  if (!final || !isChatResponse(final)) throw new Error("API trả dữ liệu stream không đúng định dạng");
  return final;
}

function isChatResponse(payload: unknown): payload is ChatResponse {
  if (!payload || typeof payload !== "object") return false;
  const candidate = payload as Record<string, unknown>;
  return (
    typeof candidate.response === "string" &&
    candidate.response.trim().length > 0 &&
    Array.isArray(candidate.citations)
  );
}
