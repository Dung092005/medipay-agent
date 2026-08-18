import { ChangedFile, DiffLineKind } from "../../lib/review-types";

type DiffViewerProps = {
  file: ChangedFile;
};

const linePrefix: Record<DiffLineKind, string> = { context: " ", addition: "+", deletion: "−" };

export function DiffViewer({ file }: DiffViewerProps) {
  return (
    <section className="admin-diff-card" aria-labelledby="diff-heading">
      <div className="admin-section-heading">
        <div>
          <p className="admin-eyebrow">Thay đổi nguồn</p>
          <h2 id="diff-heading">{file.path}</h2>
        </div>
        <div className="admin-diff-stats" aria-label={`${file.additions} dòng thêm, ${file.deletions} dòng xóa`}>
          <span className="is-addition">+{file.additions}</span>
          <span className="is-deletion">−{file.deletions}</span>
        </div>
      </div>
      <div className="admin-diff-meta"><span>{file.beforeLabel}</span><span>→</span><span>{file.afterLabel}</span></div>
      <div className="admin-diff-lines" role="region" aria-label="Nội dung thay đổi" tabIndex={0}>
        {file.lines.map((line, index) => (
          <div className={`admin-diff-line is-${line.kind}`} key={`${file.path}-${line.lineNumber}-${index}`}>
            <span className="admin-line-number">{line.lineNumber}</span>
            <span className="admin-line-prefix" aria-hidden="true">{linePrefix[line.kind]}</span>
            <code>{line.text}</code>
            {line.evidenceIds?.length ? <span className="admin-evidence-pin" title="Có dữ liệu GraphRAG liên kết">●</span> : null}
          </div>
        ))}
      </div>
      <p className="admin-diff-note"><span>●</span> Dòng có chấm được liên kết với bằng chứng ở bảng bên phải.</p>
    </section>
  );
}
