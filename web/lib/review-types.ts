export type ReviewDomain = "legal_document" | "hospital_fee_ocr";
export type ReviewStatus = "pending" | "accepted" | "rejected";
export type DiffLineKind = "context" | "addition" | "deletion";
export type KnowledgeTab = "chunks" | "entities" | "relations" | "ocr";

export type ReviewListItem = {
  id: string;
  domain: ReviewDomain;
  title: string;
  sourceName: string;
  submittedAt: string;
  status: ReviewStatus;
  confidence: number;
  changedFileCount: number;
  flags: string[];
};

export type DiffLine = {
  lineNumber: number;
  kind: DiffLineKind;
  text: string;
  evidenceIds?: string[];
};

export type ChangedFile = {
  path: string;
  beforeLabel: string;
  afterLabel: string;
  lines: DiffLine[];
  additions: number;
  deletions: number;
};

export type ReviewChunk = {
  id: string;
  text: string;
  sourceFile: string;
  page?: number;
  lineRange?: string;
  confidence: number;
};

export type ReviewEntity = {
  id: string;
  label: string;
  type: string;
  confidence: number;
  chunkIds: string[];
};

export type ReviewRelation = {
  id: string;
  sourceEntityId: string;
  targetEntityId: string;
  label: string;
  confidence: number;
  evidenceChunkIds: string[];
};

export type OcrField = {
  id: string;
  key: string;
  value: string;
  rawValue: string;
  confidence: number;
  sourcePage?: number;
  needsReview: boolean;
};

export type AuditEvent = {
  id: string;
  action: "submitted" | "accepted" | "rejected";
  actor: string;
  at: string;
  note?: string;
};

export type ReviewDetail = ReviewListItem & {
  summary: string;
  branchLabel: string;
  submittedBy: string;
  files: ChangedFile[];
  chunks: ReviewChunk[];
  entities: ReviewEntity[];
  relations: ReviewRelation[];
  ocrFields: OcrField[];
  audit: AuditEvent[];
};
