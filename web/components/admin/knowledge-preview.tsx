"use client";

import { KnowledgeTab, ReviewDetail } from "../../lib/review-types";

type KnowledgePreviewProps = {
  review: ReviewDetail;
  activeTab: KnowledgeTab;
  onTabChange: (tab: KnowledgeTab) => void;
};

const tabs: { id: KnowledgeTab; label: string }[] = [
  { id: "chunks", label: "Chunks" },
  { id: "entities", label: "Entities" },
  { id: "relations", label: "Relations" },
  { id: "ocr", label: "OCR fields" },
];

function confidenceLabel(value: number) {
  return `${Math.round(value * 100)}%`;
}

export function KnowledgePreview({ review, activeTab, onTabChange }: KnowledgePreviewProps) {
  const entityById = new Map(review.entities.map((entity) => [entity.id, entity.label]));

  return (
    <section className="admin-knowledge-card" aria-labelledby="knowledge-heading">
      <div className="admin-section-heading">
        <div>
          <p className="admin-eyebrow">Preview GraphRAG</p>
          <h2 id="knowledge-heading">Tri thức dự kiến</h2>
        </div>
        <span className="admin-preview-chip">Chưa promote</span>
      </div>
      <div className="admin-knowledge-tabs" role="tablist" aria-label="Loại dữ liệu GraphRAG">
        {tabs.map((tab) => {
          const count = tab.id === "chunks" ? review.chunks.length : tab.id === "entities" ? review.entities.length : tab.id === "relations" ? review.relations.length : review.ocrFields.length;
          return <button className={activeTab === tab.id ? "is-active" : ""} key={tab.id} type="button" role="tab" aria-selected={activeTab === tab.id} onClick={() => onTabChange(tab.id)}>{tab.label} <span>{count}</span></button>;
        })}
      </div>

      <div className="admin-knowledge-body">
        {activeTab === "chunks" && review.chunks.map((chunk) => (
          <article className="admin-evidence-item" key={chunk.id}>
            <div className="admin-evidence-topline"><span className="admin-evidence-id">{chunk.id}</span><Confidence value={chunk.confidence} /></div>
            <p>{chunk.text}</p>
            <small>{chunk.sourceFile} · trang {chunk.page} · dòng {chunk.lineRange}</small>
          </article>
        ))}
        {activeTab === "entities" && review.entities.map((entity) => (
          <article className="admin-evidence-item" key={entity.id}>
            <div className="admin-entity-row"><strong>{entity.label}</strong><span className="admin-type-chip">{entity.type}</span></div>
            <div className="admin-evidence-topline"><small>{entity.chunkIds.join(" · ")}</small><Confidence value={entity.confidence} /></div>
          </article>
        ))}
        {activeTab === "relations" && review.relations.map((relation) => (
          <article className="admin-evidence-item" key={relation.id}>
            <div className="admin-relation-line"><strong>{entityById.get(relation.sourceEntityId)}</strong><span>→</span><strong>{entityById.get(relation.targetEntityId)}</strong></div>
            <p className="admin-relation-label">{relation.label}</p>
            <div className="admin-evidence-topline"><small>{relation.evidenceChunkIds.join(" · ")}</small><Confidence value={relation.confidence} /></div>
          </article>
        ))}
        {activeTab === "ocr" && (review.ocrFields.length ? review.ocrFields.map((field) => (
          <article className={`admin-evidence-item ${field.needsReview ? "needs-review" : ""}`} key={field.id}>
            <div className="admin-entity-row"><span><strong>{field.key}</strong><small> · raw: {field.rawValue}</small></span><Confidence value={field.confidence} /></div>
            <p className="admin-ocr-value">{field.value}</p>
            <small>Trang {field.sourcePage}{field.needsReview ? " · cần xác nhận thủ công" : " · đã chuẩn hóa"}</small>
          </article>
        )) : <p className="admin-empty-state">Review này không có trường OCR.</p>)}
      </div>
    </section>
  );
}

function Confidence({ value }: { value: number }) {
  const tone = value < 0.85 ? "low" : value < 0.93 ? "medium" : "high";
  return <span className={`admin-confidence is-${tone}`}><span aria-hidden="true">●</span> {confidenceLabel(value)}</span>;
}
