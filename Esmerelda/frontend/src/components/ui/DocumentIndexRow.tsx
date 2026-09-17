import { FileText, FileSpreadsheet, FileImage, File, Download, Loader2 } from "lucide-react";
import type { DocumentFile, IndexingStatus } from "@/types";
import { relativeTimeFromNow } from "@/lib/utils";
import { getDocumentDownloadUrl } from "@/lib/api";
import { StatusPill } from "./StatusPill";

const iconByType: Record<string, typeof FileText> = {
  pdf: FileText,
  doc: FileText,
  docx: FileText,
  ppt: FileImage,
  pptx: FileImage,
  xls: FileSpreadsheet,
  xlsx: FileSpreadsheet,
  csv: FileSpreadsheet,
};

const indexingMeta: Record<IndexingStatus, { label: string; tone: "success" | "cyan" | "neutral" }> = {
  indexed: { label: "Indexed", tone: "success" },
  indexing: { label: "Indexing", tone: "cyan" },
  queued: { label: "Queued", tone: "neutral" },
};

interface DocumentIndexRowProps {
  document: DocumentFile;
}

export function DocumentIndexRow({ document }: DocumentIndexRowProps) {
  const Icon = (document.fileType && iconByType[document.fileType.toLowerCase()]) || File;
  const indexing = indexingMeta[document.indexingStatus];

  return (
    <div className="group flex items-center gap-4 border-b border-graphite-700/50 py-3.5 last:border-b-0">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-graphite-700/70 bg-graphite-850 text-graphite-300">
        <Icon size={16} strokeWidth={1.75} />
      </div>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-warm-50">{document.name}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 truncate text-xs text-graphite-400">
          <span>{document.courseName ?? "Unassigned"}</span>
          {document.fileType && (
            <>
              <span className="text-graphite-600">·</span>
              <span>{document.fileType.toUpperCase()}</span>
            </>
          )}
          {document.updatedAt && (
            <>
              <span className="text-graphite-600">·</span>
              <span>{relativeTimeFromNow(document.updatedAt)}</span>
            </>
          )}
        </p>
      </div>

      <StatusPill tone={indexing.tone} dot={document.indexingStatus !== "indexing"} className="shrink-0">
        {document.indexingStatus === "indexing" && <Loader2 size={10} className="animate-spin" />}
        {indexing.label}
      </StatusPill>

      <a
        href={getDocumentDownloadUrl(document)}
        aria-label={`Download ${document.name}`}
        className="flex size-8 shrink-0 items-center justify-center rounded-lg text-graphite-400 opacity-0 transition-all hover:bg-graphite-700/50 hover:text-cyan-300 group-hover:opacity-100 focus-visible:opacity-100"
      >
        <Download size={15} />
      </a>
    </div>
  );
}
