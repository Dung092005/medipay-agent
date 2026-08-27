"use client";

import { FormEvent, forwardRef, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import Image from "next/image";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";

import { ChatCitation, sendChatMessageStream } from "../lib/api";
import { useAuth } from "../lib/auth-context";
import { AuthRoute } from "../components/auth-route";

type ChatMessage = { role: "user" | "assistant"; content: string; id: string; citations?: ChatCitation[] };
type IconName = "arrow" | "book" | "chat" | "check" | "chevron" | "close" | "document" | "help" | "history" | "logout" | "menu" | "new" | "review" | "search" | "shield" | "spark" | "user";

const topicCards = [
  {
    eyebrow: "Quyền lợi dài hạn",
    title: "BHYT 5 năm liên tục",
    description: "Điều kiện và quyền lợi khi cùng chi trả vượt mức quy định.",
    question: "Quyền lợi BHYT 5 năm liên tục được tính như thế nào?",
    icon: "shield" as const,
    tone: "mint",
  },
  {
    eyebrow: "Mức đóng",
    title: "Đóng bao nhiêu, ai hỗ trợ?",
    description: "Tra cứu mức đóng theo nhóm hộ gia đình, học sinh và người lao động.",
    question: "Mức đóng BHYT hiện nay và mức hỗ trợ theo từng nhóm là bao nhiêu?",
    icon: "document" as const,
    tone: "sky",
  },
  {
    eyebrow: "Thủ tục khám chữa bệnh",
    title: "Chuyển tuyến đúng quy trình",
    description: "Hồ sơ cần có, thời hạn giấy chuyển tuyến và nơi tiếp nhận.",
    question: "Thủ tục chuyển tuyến khám chữa bệnh BHYT gồm những gì?",
    icon: "arrow" as const,
    tone: "blue",
  },
  {
    eyebrow: "Phạm vi hưởng",
    title: "Khám trái tuyến",
    description: "Hiểu mức hưởng nội trú, ngoại trú theo từng tuyến điều trị.",
    question: "Mức hưởng BHYT khi đi khám trái tuyến năm 2026 như thế nào?",
    icon: "book" as const,
    tone: "amber",
  },
];

function splitAnswer(value: string) {
  const lines = value.trim().split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const isDebugLine = (line: string) => {
    const normalized = line.replace(/[*_`]/g, "").trim();
    return /^(?:nguồn(?:\s+trích dẫn)?|sources?|citations?)\s*:/i.test(normalized)
      || /^evidence\s+(?:hiện\s+có|available)\b/i.test(normalized)
      || /^(?:căn cứ pháp lý|nguồn tham khảo|nguồn trích dẫn):?$/i.test(normalized);
  };
  const cleanLine = (line: string) => line
    .replace(/^#{1,4}\s+/, "")
    .replace(/^(?:[-*•]|\d+[.)])\s+/, "")
    .replace(/^\*\*(.+)\*\*:?$/, "$1")
    .trim();
  const contentLines = lines.map(cleanLine).filter((line) => line && !isDebugLine(line));
  return {
    summary: contentLines[0] ?? "",
    details: contentLines.slice(1).filter((line) => !/^(?:nội dung|chi tiết|điều kiện|quy định):?$/i.test(line)),
  };
}

function formatInlineMarkdown(value: string) {
  return value.split(/(\*\*[^*]+\*\*)/g).map((part, index) => part.startsWith("**") && part.endsWith("**") ? <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong> : part);
}

export default function HomePage() {
  const { user, signOut } = useAuth();
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [streamStage, setStreamStage] = useState("started");
  const [error, setError] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [expandedCitationIds, setExpandedCitationIds] = useState<string[]>([]);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const welcomeRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);
  const drawerReturnFocusRef = useRef<HTMLElement | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const conversationIdRef = useRef(crypto.randomUUID());

  const activeMessage = messages.find((message) => message.id === activeMessageId && message.role === "assistant");
  const activeEvidence = (activeMessage?.citations ?? []).map((citation, index) => ({ ...citation, evidenceId: `${activeMessage?.id ?? "active"}-${index}` }));

  useGSAP(() => {
    if (!welcomeRef.current || messages.length || typeof window === "undefined") return;
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const intro = gsap.timeline();
      intro.from(".bhyt-hero-copy > *", { opacity: 0, y: 24, duration: 0.72, stagger: 0.08, ease: "power3.out", clearProps: "opacity,transform" });
      gsap.fromTo(".bhyt-hero-visual", { opacity: 0.45, scale: 0.84 }, {
        opacity: 1,
        scale: 1,
        duration: .9,
        ease: "power3.out",
        clearProps: "opacity,transform",
      });
      intro.fromTo(".bhyt-topic-card", { opacity: 0, y: 32, scale: 0.97 }, {
        opacity: 1,
        y: 0,
        scale: 1,
        duration: .58,
        stagger: .07,
        ease: "power3.out",
        clearProps: "opacity,transform",
      }, "-=.2");
    });
    return () => media.revert();
  }, { scope: welcomeRef, dependencies: [messages.length], revertOnUpdate: true });

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (!evidenceOpen) return;
    const previousOverflow = document.body.style.overflow;
    drawerReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = "hidden";
    drawerCloseRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setEvidenceOpen(false);
      if (event.key !== "Tab") return;
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])');
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
      drawerReturnFocusRef.current?.focus();
    };
  }, [evidenceOpen]);

  function createNewChat() {
    if (loading) return;
    streamAbortRef.current?.abort();
    conversationIdRef.current = crypto.randomUUID();
    setMessages([]);
    setQuestion("");
    setError("");
    setActiveMessageId(null);
    setExpandedCitationIds([]);
    setEvidenceOpen(false);
  }

  function chooseTopic(value: string) {
    setQuestion(value);
    window.setTimeout(() => inputRef.current?.focus(), 40);
  }

  function toggleEvidenceDrawer(message: ChatMessage) {
    if (evidenceOpen && activeMessageId === message.id) {
      setEvidenceOpen(false);
      return;
    }
    setActiveMessageId(message.id);
    setExpandedCitationIds([]);
    setEvidenceOpen(true);
    if (evidenceOpen) window.setTimeout(() => drawerCloseRef.current?.focus(), 0);
  }

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = question.trim();
    if (!message || loading) return;
    const userMessage: ChatMessage = { id: `user-${Date.now()}`, role: "user", content: message };
    setMessages((current) => [...current, userMessage]);
    setQuestion("");
    setError("");
    setLoading(true);
    setStreamStage("started");
    setActiveMessageId(null);
    setExpandedCitationIds([]);
    setEvidenceOpen(false);
    const controller = new AbortController();
    streamAbortRef.current = controller;
    try {
      const result = await sendChatMessageStream(
        message,
        (streamEvent) => {
          if (streamEvent.type === "status") setStreamStage(streamEvent.stage);
          if (streamEvent.type === "final") setStreamStage("verified");
        },
        {
          conversationId: conversationIdRef.current,
          turnId: crypto.randomUUID(),
        },
        controller.signal,
      );
      setMessages((current) => [...current, { id: `assistant-${Date.now()}`, role: "assistant", content: result.response, citations: result.citations }]);
    } catch (requestError) {
      if (!(requestError instanceof DOMException && requestError.name === "AbortError")) {
        setError(requestError instanceof Error ? requestError.message : "Không thể gửi câu hỏi");
      }
    } finally {
      streamAbortRef.current = null;
      setLoading(false);
    }
  }

  function cancelRequest() {
    streamAbortRef.current?.abort();
    setStreamStage("cancelled");
  }

  return (
    <AuthRoute>
    <main className={`bhyt-app${sidebarCollapsed ? " is-sidebar-collapsed" : ""}`}>
      <button className={`bhyt-mobile-backdrop${mobileMenuOpen ? " is-visible" : ""}`} type="button" aria-label="Đóng menu" tabIndex={mobileMenuOpen ? 0 : -1} onClick={() => setMobileMenuOpen(false)} />

      <aside className={`bhyt-sidebar${mobileMenuOpen ? " is-mobile-open" : ""}`} aria-label="Điều hướng chính">
        <div className="bhyt-brand-row">
          <a className="bhyt-brand" href="#main-chat" aria-label="BHYT AI - Trang tra cứu">
            <span className="bhyt-brand-symbol" aria-hidden="true"><Icon name="spark" /></span>
            <span className="bhyt-brand-copy"><strong>BHYT AI</strong><small>Trợ lý y tế số</small></span>
          </a>
          <button className="bhyt-collapse-button" type="button" aria-label={sidebarCollapsed ? "Mở rộng menu" : "Thu gọn menu"} aria-expanded={!sidebarCollapsed} onClick={() => setSidebarCollapsed((current) => !current)}><Icon name="chevron" /></button>
        </div>

        <nav className="bhyt-nav" onClick={() => setMobileMenuOpen(false)}>
          <a className="bhyt-nav-item is-active" href="#main-chat"><Icon name="chat" /><span>Tra cứu BHYT</span></a>
          <button className="bhyt-nav-item" type="button" onClick={() => chooseTopic(topicCards[2].question)}><Icon name="document" /><span>Hướng dẫn thủ tục</span></button>
          <button className="bhyt-nav-item" type="button" onClick={() => inputRef.current?.focus()}><Icon name="help" /><span>Trợ giúp &amp; Hỏi đáp</span></button>
        </nav>

        <div className="bhyt-sidebar-footer">
          {user ? (
            <div className="bhyt-user-info">
              {user.photoURL ? <Image src={user.photoURL} alt="" width={32} height={32} unoptimized className="bhyt-user-avatar" /> : <Icon name="user" />}
              <span><strong>{user.displayName || user.email}</strong><small>{user.role === "admin" ? "Quản trị viên" : "Người dùng"}</small></span>
              <button type="button" className="bhyt-logout-btn" onClick={() => signOut()} title="Đăng xuất"><Icon name="logout" /></button>
            </div>
          ) : null}
          <div className="bhyt-support-note"><Icon name="shield" /><span><strong>Hỗ trợ tra cứu BHYT</strong><small>Thông tin được đối chiếu từ nguồn pháp lý</small></span></div>
          {user?.role === "admin" ? <a className="bhyt-admin-link" href="/admin/review"><Icon name="shield" /><span>Cổng quản trị viên</span></a> : null}
        </div>
      </aside>

      <section className="bhyt-workspace" id="main-chat">
        <header className="bhyt-topbar">
          <div className="bhyt-topbar-leading">
            <button className="bhyt-mobile-menu" type="button" aria-label="Mở menu" aria-expanded={mobileMenuOpen} onClick={() => setMobileMenuOpen(true)}><Icon name="menu" /></button>
            <div>
              <p className="bhyt-topbar-kicker">Tra cứu văn bản pháp quy</p>
              <h2>Trợ lý BHYT</h2>
            </div>
          </div>
          <div className="bhyt-topbar-actions">
            <span className="bhyt-live-status"><span />Dữ liệu đang hiệu lực</span>
            <button className="bhyt-source-button" type="button" disabled={!activeEvidence.length} aria-label="Mở nguồn trích dẫn" aria-expanded={evidenceOpen} onClick={() => setEvidenceOpen(true)}><Icon name="document" /><span>{activeEvidence.length ? `${activeEvidence.length} nguồn` : "Nguồn"}</span></button>
            <button className="bhyt-new-chat" type="button" onClick={createNewChat} disabled={loading}><Icon name="new" /><span>Đoạn chat mới</span></button>
          </div>
        </header>

        <div className="bhyt-chat-viewport" ref={streamRef} aria-live="polite">
          {!messages.length && !loading ? (
            <WelcomeScreen ref={welcomeRef} onChooseTopic={chooseTopic} onFocusComposer={() => inputRef.current?.focus()} />
          ) : (
            <div className="bhyt-message-stream">
              {messages.map((message) => message.role === "user" ? (
                <div className="bhyt-message-row is-user" key={message.id}>
                  <div className="bhyt-message-meta">Bạn <span>vừa hỏi</span></div>
                  <div className="bhyt-user-bubble">{message.content}</div>
                </div>
              ) : (
                <AssistantMessage key={message.id} message={message} evidenceDrawerOpen={evidenceOpen && activeMessageId === message.id} onEvidenceToggle={toggleEvidenceDrawer} />
              ))}
              {loading ? <LoadingMessage stage={streamStage} onCancel={cancelRequest} /> : null}
              {error ? <div className="bhyt-error" role="alert">{error}</div> : null}
              {!loading && messages.some((message) => message.role === "assistant") ? (
                <section className="bhyt-follow-up" aria-labelledby="follow-up-title">
                  <div><p>Tiếp tục tìm hiểu</p><h3 id="follow-up-title">Một số câu hỏi liên quan</h3></div>
                  <div>{topicCards.slice(0, 3).map((item) => <button key={item.title} type="button" onClick={() => chooseTopic(item.question)}>{item.title}<Icon name="arrow" /></button>)}</div>
                </section>
              ) : null}
            </div>
          )}
        </div>

        <form className="bhyt-composer" onSubmit={submitQuestion}>
          <Icon name="search" />
          <input ref={inputRef} aria-label="Nhập câu hỏi về bảo hiểm y tế" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Hỏi về quyền lợi, mức đóng hoặc thủ tục BHYT..." disabled={loading} />
          <span className="bhyt-composer-hint">Enter</span>
          <button type={loading ? "button" : "submit"} aria-label={loading ? "Hủy tra cứu" : "Gửi câu hỏi"} onClick={loading ? cancelRequest : undefined} disabled={!loading && !question.trim()}>{loading ? "Hủy" : "Gửi câu hỏi"}<Icon name={loading ? "close" : "arrow"} /></button>
        </form>
      </section>

      <button className={`bhyt-drawer-backdrop${evidenceOpen ? " is-visible" : ""}`} type="button" aria-label="Đóng nguồn trích dẫn" tabIndex={evidenceOpen ? 0 : -1} onClick={() => setEvidenceOpen(false)} />
      <aside ref={drawerRef} className={`bhyt-evidence-drawer${evidenceOpen ? " is-open" : ""}`} id="evidence-drawer" role="dialog" aria-modal="true" aria-hidden={!evidenceOpen} aria-labelledby="evidence-title">
        {evidenceOpen ? <>
          <div className="bhyt-drawer-header">
            <div><p>Nguồn kiểm chứng</p><h2 id="evidence-title">Văn bản liên quan</h2></div>
            <button ref={drawerCloseRef} type="button" aria-label="Đóng nguồn trích dẫn" onClick={() => setEvidenceOpen(false)}><Icon name="close" /></button>
          </div>
          <div className="bhyt-drawer-context"><Icon name="shield" /><p>{activeMessage ? `Có ${activeEvidence.length} nguồn được dùng cho câu trả lời đang chọn.` : "Nguồn trích dẫn sẽ xuất hiện sau khi bạn mở nguồn từ một câu trả lời."}</p></div>
          <div className="bhyt-evidence-list">
            {activeEvidence.length ? activeEvidence.map((citation) => (
              <CitationCard key={citation.evidenceId} citation={citation} expanded={expandedCitationIds.includes(citation.evidenceId)} onToggle={() => setExpandedCitationIds((current) => current.includes(citation.evidenceId) ? current.filter((id) => id !== citation.evidenceId) : [...current, citation.evidenceId])} />
            )) : <div className="bhyt-empty-evidence"><Icon name="document" /><p>Chưa có nguồn nào được chọn.</p><span>Mở nguồn ngay dưới câu trả lời của trợ lý để kiểm tra căn cứ.</span></div>}
          </div>
        </> : null}
      </aside>
    </main>
    </AuthRoute>
  );
}

const WelcomeScreen = forwardRef<HTMLDivElement, { onChooseTopic: (question: string) => void; onFocusComposer: () => void }>(function WelcomeScreen({ onChooseTopic, onFocusComposer }, ref) {
  return (
    <div className="bhyt-welcome" ref={ref}>
      <section className="bhyt-hero" aria-labelledby="welcome-title">
        <div className="bhyt-hero-copy">
          <p className="bhyt-hero-kicker">Trợ lý hành chính y tế đáng tin cậy</p>
          <h1 id="welcome-title">Hiểu đúng quyền lợi BHYT, ngay khi bạn cần.</h1>
          <p className="bhyt-hero-description">Hỏi bằng ngôn ngữ tự nhiên, nhận câu trả lời dễ hiểu và kiểm tra lại từng nguồn pháp lý được sử dụng.</p>
          <div className="bhyt-hero-actions">
            <button className="is-primary" type="button" onClick={onFocusComposer}>Đặt câu hỏi ngay <Icon name="arrow" /></button>
            <button className="is-secondary" type="button" onClick={() => document.getElementById("suggested-topics")?.scrollIntoView({ behavior: "smooth" })}>Khám phá chủ đề <Icon name="book" /></button>
          </div>
        </div>
        <div className="bhyt-hero-visual" aria-label="Minh họa hồ sơ bảo hiểm y tế được xác minh">
          <div className="bhyt-visual-orbit orbit-one" />
          <div className="bhyt-visual-orbit orbit-two" />
          <div className="bhyt-medical-card main-card">
            <div className="bhyt-card-heading"><span><Icon name="shield" /></span><div><small>Hồ sơ BHYT</small><strong>Đã đối chiếu quyền lợi</strong></div></div>
            <div className="bhyt-card-lines"><span /><span /><span /></div>
            <div className="bhyt-card-confirm"><span><Icon name="shield" /></span>Nguồn pháp lý đã xác minh</div>
          </div>
          <div className="bhyt-medical-card source-card-mini"><Icon name="document" /><div><small>Văn bản hiệu lực</small><strong>Nguồn chính thống</strong></div></div>
          <div className="bhyt-medical-card care-card"><span className="bhyt-care-mark"><Icon name="spark" /></span><div><strong>Trợ lý BHYT</strong><small>Giải thích theo từng trường hợp</small></div></div>
        </div>
      </section>

      <section className="bhyt-topic-section" id="suggested-topics" aria-labelledby="topics-title">
        <div className="bhyt-section-heading"><div><p>Gợi ý để bắt đầu</p><h2 id="topics-title">Bạn đang quan tâm điều gì?</h2></div><span>Chọn một chủ đề để điền câu hỏi mẫu</span></div>
        <div className="bhyt-topic-grid">
          {topicCards.map((item) => (
            <button className={`bhyt-topic-card is-${item.tone}`} key={item.title} type="button" onClick={() => onChooseTopic(item.question)}>
              <span className="bhyt-topic-icon"><Icon name={item.icon} /></span>
              <span className="bhyt-topic-copy"><small>{item.eyebrow}</small><strong>{item.title}</strong><span>{item.description}</span></span>
              <span className="bhyt-topic-arrow"><Icon name="arrow" /></span>
            </button>
          ))}
        </div>
      </section>

      <div className="bhyt-trust-rail" aria-label="Các đặc điểm của hệ thống">
        <div><span>Nguồn pháp lý có kiểm chứng</span><i /> <span>Dữ liệu theo phiên bản hiệu lực</span><i /> <span>Giải thích bằng ngôn ngữ dễ hiểu</span><i /> <span>Bảo vệ thông tin người dùng</span><i /> <span>Nguồn pháp lý có kiểm chứng</span><i /> <span>Dữ liệu theo phiên bản hiệu lực</span></div>
      </div>
    </div>
  );
});

function AssistantMessage({ message, evidenceDrawerOpen, onEvidenceToggle }: { message: ChatMessage; evidenceDrawerOpen: boolean; onEvidenceToggle: (message: ChatMessage) => void }) {
  const parsed = splitAnswer(message.content);
  const messageRef = useRef<HTMLDivElement>(null);

  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.from(".bhyt-answer-tier", { opacity: 0, y: 22, duration: .55, stagger: .09, ease: "power3.out" });
    });
    return () => media.revert();
  }, { scope: messageRef });

  return (
    <div className="bhyt-message-row is-assistant" ref={messageRef}>
      <span className="bhyt-assistant-avatar" aria-hidden="true"><Icon name="spark" /></span>
      <div className="bhyt-assistant-message">
        <div className="bhyt-message-meta"><strong>Trợ lý BHYT</strong><span>Bảo hiểm y tế Việt Nam</span>{message.citations?.length ? <em><Icon name="shield" />Đã kiểm chứng</em> : null}</div>
        <div className="bhyt-assistant-content">
          <section className="bhyt-answer-tier bhyt-answer-callout" aria-label="Kết luận">
            <div className="bhyt-tier-heading"><span><Icon name="spark" /></span><p>Kết luận</p></div>
            <div>{formatInlineMarkdown(parsed.summary)}</div>
          </section>

          {parsed.details.length ? <section className="bhyt-answer-tier bhyt-rule-section" aria-label="Điều kiện và quy định">
            <div className="bhyt-tier-heading"><span><Icon name="book" /></span><p>Điều kiện và quy định</p></div>
            <ul>{parsed.details.map((detail, index) => <li key={`${detail}-${index}`}><span><Icon name="check" /></span><p>{formatInlineMarkdown(detail)}</p></li>)}</ul>
          </section> : null}

          {message.citations?.length ? <section className="bhyt-answer-tier bhyt-legal-section" aria-label="Căn cứ pháp lý">
            <div className="bhyt-tier-heading"><span><Icon name="document" /></span><p>Căn cứ pháp lý</p></div>
            <button className="bhyt-evidence-summary-button" type="button" aria-expanded={evidenceDrawerOpen} aria-controls="evidence-drawer" onClick={() => onEvidenceToggle(message)}><Icon name="book" /><span><strong>{message.citations.length}</strong> căn cứ pháp lý trích dẫn</span><Icon name="chevron" /></button>
          </section> : null}
        </div>
      </div>
    </div>
  );
}

function LoadingMessage({ stage, onCancel }: { stage: string; onCancel: () => void }) {
  const labels: Record<string, string> = {
    started: "Đang khởi động truy vấn...",
    retrieve_vectors: "Đang tìm văn bản liên quan...",
    assemble_context: "Đang tổng hợp căn cứ...",
    verify_evidence: "Đang kiểm tra nguồn...",
    generate: "Đang soạn câu trả lời...",
    guardrail: "Đang kiểm tra độ chính xác...",
    verified: "Đã kiểm chứng câu trả lời.",
    cancelled: "Đã hủy truy vấn.",
  };
  return <div className="bhyt-message-row is-assistant"><span className="bhyt-assistant-avatar" aria-hidden="true"><Icon name="spark" /></span><div className="bhyt-assistant-message"><div className="bhyt-message-meta"><strong>Trợ lý BHYT</strong><span>Đang tra cứu</span></div><div className="bhyt-typing"><span /><span /><span /><p>{labels[stage] ?? labels.started}</p><button type="button" onClick={onCancel}>Hủy</button></div></div></div>;
}

function CitationCard({ citation, expanded, onToggle }: { citation: ChatCitation & { evidenceId: string }; expanded: boolean; onToggle: () => void }) {
  return (
    <article className={`bhyt-evidence-card${expanded ? " is-expanded" : ""}`}>
      <button type="button" onClick={onToggle} aria-expanded={expanded} aria-controls={`evidence-${citation.evidenceId}`}>
        <span className="bhyt-evidence-icon"><Icon name="document" /></span>
        <span className="bhyt-evidence-copy"><small>{citation.document_number || "Nguồn pháp lý"}</small><strong>{citation.title || "Văn bản nguồn"}</strong><span>{citation.quote}</span></span>
        <span className="bhyt-evidence-chevron"><Icon name="chevron" /></span>
      </button>
      {expanded ? <div className="bhyt-evidence-content" id={`evidence-${citation.evidenceId}`}><p>{citation.quote}</p>{citation.section_title ? <strong>{citation.section_title}</strong> : null}{citation.source_url ? <a href={citation.source_url} target="_blank" rel="noreferrer">Mở nguồn chính thức</a> : null}</div> : null}
    </article>
  );
}

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    arrow: <><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></>,
    book: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5z"/></>,
    chat: <><path d="M21 12a8 8 0 0 1-8 8H6l-4 2 1.5-4A9 9 0 1 1 21 12Z"/><path d="M8 12h.01M12 12h.01M16 12h.01"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    close: <><path d="m6 6 12 12"/><path d="M18 6 6 18"/></>,
    document: <><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></>,
    help: <><circle cx="12" cy="12" r="9"/><path d="M9.7 9a2.5 2.5 0 1 1 3.4 2.3c-.8.4-1.1.9-1.1 1.7M12 17h.01"/></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></>,
    logout: <><path d="M10 5H5v14h5M14 8l4 4-4 4M8 12h10"/></>,
    menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
    new: <><path d="M12 5v14M5 12h14"/></>,
    review: <><path d="M5 3h14v18H5z"/><path d="M9 8h6M9 12h6M9 16h4"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    shield: <><path d="M12 2 20 5v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z"/><path d="m9 12 2 2 4-5"/></>,
    spark: <><path d="M12 2c.6 5 2 7.4 7 8-5 .6-6.4 3-7 8-.6-5-2-7.4-7-8 5-.6 6.4-3 7-8Z"/><path d="M19 16c.2 1.7.8 2.8 2.5 3-1.7.2-2.3 1.3-2.5 3-.2-1.7-.8-2.8-2.5-3 1.7-.2 2.3-1.3 2.5-3Z"/></>,
    user: <><circle cx="12" cy="8" r="3"/><path d="M5 21a7 7 0 0 1 14 0"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
