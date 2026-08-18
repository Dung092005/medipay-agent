const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ChatCitation = {
  document_id: string;
  chunk_id: string;
  title: string;
  section_title: string;
  quote: string;
  channels: string[];
};

export type ChatResponse = {
  response: string;
  citations: ChatCitation[];
};

export type ChatHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

type ApiError = {
  code?: string;
  message?: string;
};

export async function sendChatMessage(
  message: string,
  chatHistory: ChatHistoryMessage[] = [],
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const response = await fetch(`${apiUrl}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, chat_history: chatHistory }),
    signal,
  });

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

function isChatResponse(payload: unknown): payload is ChatResponse {
  if (!payload || typeof payload !== "object") return false;
  const candidate = payload as Record<string, unknown>;
  return (
    typeof candidate.response === "string" &&
    candidate.response.trim().length > 0 &&
    Array.isArray(candidate.citations)
  );
}
