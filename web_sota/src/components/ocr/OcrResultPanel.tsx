import {
  BookOpen,
  Check,
  Clock,
  Code2,
  Copy,
  Download,
  ExternalLink,
  FileCode,
  FileText,
  Gauge,
  Layers,
  Sparkles,
  Zap,
} from "lucide-react";
import type React from "react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LayoutBlocksView, type OcrBlock } from "./LayoutBlocksView";
import { MarkdownPreview } from "./MarkdownPreview";

interface OcrResultPanelProps {
  text: string;
  onChangeText: (newText: string) => void;
  resultData?: any;
  jobId?: string | null;
  status: "idle" | "processing" | "completed" | "failed";
  onCopyText?: () => void;
  onDownloadTxt?: () => void;
  onDownloadMd?: () => void;
}

export const OcrResultPanel: React.FC<OcrResultPanelProps> = ({
  text,
  onChangeText,
  resultData,
  jobId,
  status,
  onCopyText,
  onDownloadTxt,
  onDownloadMd,
}) => {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<string>("formatted");
  const [copiedKind, setCopiedKind] = useState<string | null>(null);

  // Compute document metrics
  const metrics = useMemo(() => {
    if (!text) return { words: 0, chars: 0, readTimeMinutes: 0 };
    const words = text.trim() ? text.trim().split(/\s+/).length : 0;
    const chars = text.length;
    const readTimeMinutes = Math.max(1, Math.ceil(words / 200));
    return { words, chars, readTimeMinutes };
  }, [text]);

  const backendName = resultData?.backend || resultData?.model || "OCR";
  const confidence = resultData?.confidence;
  const processingTime = resultData?.processing_time;
  const blocks: OcrBlock[] = resultData?.blocks || [];

  const handleCopyKind = (kind: "raw" | "markdown" | "llm" | "json") => {
    let contentToCopy = text;
    if (kind === "llm") {
      contentToCopy = `Below is the OCR text extracted from the document:\n\n\`\`\`markdown\n${text}\n\`\`\``;
    } else if (kind === "json") {
      contentToCopy = JSON.stringify(resultData || { text }, null, 2);
    }

    navigator.clipboard.writeText(contentToCopy);
    setCopiedKind(kind);
    if (onCopyText && kind === "raw") onCopyText();
    setTimeout(() => setCopiedKind(null), 2000);
  };

  const handleDownloadJson = () => {
    const payload = JSON.stringify(resultData || { text }, null, 2);
    const blob = new Blob([payload], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ocr-result-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const getConfidenceBadge = (conf: number) => {
    const pct = Math.round(conf * (conf <= 1 ? 100 : 1));
    let color = "bg-emerald-950/60 border-emerald-700/60 text-emerald-300";
    if (pct < 75) color = "bg-amber-950/60 border-amber-700/60 text-amber-300";
    if (pct < 50) color = "bg-red-950/60 border-red-700/60 text-red-300";

    return (
      <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${color}`}>
        <Gauge className="w-3 h-3" />
        {pct}% Confidence
      </span>
    );
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 backdrop-blur-md overflow-hidden shadow-xl">
      {/* Top Header & Stat Badges */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 border-b border-slate-800 bg-slate-950/40">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 text-slate-100 font-semibold text-base mr-2">
            <FileText className="h-5 w-5 text-blue-400" />
            <span>OCR Result</span>
          </div>

          {/* Engine Tag */}
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-xs font-mono font-medium bg-blue-950/50 border border-blue-800/60 text-blue-300">
            {backendName.toLowerCase().includes("indra") ? (
              <Zap className="w-3.5 h-3.5 text-amber-400" />
            ) : (
              <Sparkles className="w-3 h-3 text-blue-400" />
            )}
            {backendName}
          </span>

          {/* Confidence Badge */}
          {typeof confidence === "number" && confidence > 0 && getConfidenceBadge(confidence)}

          {/* Latency */}
          {typeof processingTime === "number" && (
            <span className="inline-flex items-center gap-1 text-xs text-slate-400 font-mono">
              <Clock className="w-3 h-3" />
              {processingTime < 1 ? `${Math.round(processingTime * 1000)}ms` : `${processingTime.toFixed(2)}s`}
            </span>
          )}

          {/* Reading Time & Words */}
          {metrics.words > 0 && (
            <span className="inline-flex items-center gap-1 text-xs text-slate-400 font-sans hidden sm:inline-flex">
              <BookOpen className="w-3 h-3" />
              {metrics.words.toLocaleString()} words ({metrics.chars.toLocaleString()} chars)
            </span>
          )}
        </div>

        {/* Action Toolbar */}
        <div className="flex flex-wrap items-center gap-1.5">
          {/* Copy Plain Text / Markdown */}
          <button
            onClick={() => handleCopyKind("raw")}
            disabled={!text}
            data-testid="export-copy"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-slate-700 bg-slate-800 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-40 transition-colors shadow-sm"
            title="Copy document text"
          >
            {copiedKind === "raw" ? (
              <Check className="w-3.5 h-3.5 text-emerald-400" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
            <span>{copiedKind === "raw" ? "Copied!" : "Copy"}</span>
          </button>

          {/* Copy Prompt for LLM */}
          <button
            onClick={() => handleCopyKind("llm")}
            disabled={!text}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-purple-800/80 bg-purple-950/40 text-xs text-purple-300 hover:bg-purple-900/50 disabled:opacity-40 transition-colors shadow-sm hidden md:inline-flex"
            title="Copy as Markdown Prompt block for LLM"
          >
            {copiedKind === "llm" ? (
              <Check className="w-3.5 h-3.5 text-purple-300" />
            ) : (
              <Sparkles className="w-3.5 h-3.5" />
            )}
            <span>For LLM</span>
          </button>

          {/* Download TXT */}
          <button
            onClick={onDownloadTxt}
            disabled={!text}
            data-testid="export-txt"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-slate-700 bg-slate-800 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-40 transition-colors shadow-sm"
          >
            <Download className="w-3.5 h-3.5" />
            .txt
          </button>

          {/* Download MD */}
          <button
            onClick={onDownloadMd}
            disabled={!text}
            data-testid="export-md"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-slate-700 bg-slate-800 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-40 transition-colors shadow-sm"
          >
            <Download className="w-3.5 h-3.5" />
            .md
          </button>

          {/* Download JSON */}
          {resultData && (
            <button
              onClick={handleDownloadJson}
              disabled={!text}
              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-slate-700 bg-slate-800 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-40 transition-colors shadow-sm hidden sm:inline-flex"
            >
              <Code2 className="w-3.5 h-3.5" />
              .json
            </button>
          )}

          {/* Full Editor */}
          {jobId && (
            <button
              onClick={() => navigate(`/editor?job_id=${jobId}`)}
              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-blue-700/80 bg-blue-900/40 text-xs text-blue-200 hover:bg-blue-800/60 transition-colors shadow-sm ml-1"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Full Editor
            </button>
          )}
        </div>
      </div>

      {/* Tabs Bar & Content Area */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <div className="px-4 pt-3 pb-1 border-b border-slate-800/80 bg-slate-950/20">
          <TabsList className="bg-slate-950 border border-slate-800/80 p-0.5 rounded-lg">
            <TabsTrigger
              value="formatted"
              className="flex items-center gap-1.5 text-xs data-[state=active]:bg-slate-800 data-[state=active]:text-white data-[state=active]:shadow-sm px-3 py-1.5"
            >
              <FileText className="w-3.5 h-3.5 text-blue-400" />
              <span>Formatted Document</span>
            </TabsTrigger>
            <TabsTrigger
              value="raw"
              className="flex items-center gap-1.5 text-xs data-[state=active]:bg-slate-800 data-[state=active]:text-white data-[state=active]:shadow-sm px-3 py-1.5"
            >
              <FileCode className="w-3.5 h-3.5 text-emerald-400" />
              <span>Raw / Editor</span>
            </TabsTrigger>
            {blocks && blocks.length > 0 && (
              <TabsTrigger
                value="blocks"
                className="flex items-center gap-1.5 text-xs data-[state=active]:bg-slate-800 data-[state=active]:text-white data-[state=active]:shadow-sm px-3 py-1.5"
              >
                <Layers className="w-3.5 h-3.5 text-amber-400" />
                <span>Layout Blocks ({blocks.length})</span>
              </TabsTrigger>
            )}
            <TabsTrigger
              value="json"
              className="flex items-center gap-1.5 text-xs data-[state=active]:bg-slate-800 data-[state=active]:text-white data-[state=active]:shadow-sm px-3 py-1.5 hidden sm:flex"
            >
              <Code2 className="w-3.5 h-3.5 text-purple-400" />
              <span>JSON Tree</span>
            </TabsTrigger>
          </TabsList>
        </div>

        {/* Tab 1: Formatted Document */}
        <TabsContent value="formatted" className="p-4 focus:outline-none">
          <div className="min-h-[220px] max-h-[550px] overflow-y-auto p-4 rounded-lg bg-slate-950/50 border border-slate-800/60">
            <MarkdownPreview content={text} />
          </div>
        </TabsContent>

        {/* Tab 2: Raw / Editor */}
        <TabsContent value="raw" className="p-4 focus:outline-none">
          <textarea
            data-testid="ocr-result"
            className="w-full min-h-[220px] max-h-[550px] rounded-lg border border-slate-800 bg-slate-950 p-4 text-slate-100 font-mono text-xs sm:text-sm resize-y focus:outline-none focus:ring-2 focus:ring-blue-600 transition-all leading-relaxed"
            value={text}
            onChange={(e) => onChangeText(e.target.value)}
            placeholder={
              status === "completed" && !text
                ? "OCR completed but no text was extracted."
                : "Scan or upload a document, then click Quick Scan & OCR. Result appears here."
            }
          />
        </TabsContent>

        {/* Tab 3: Layout Blocks */}
        {blocks && blocks.length > 0 && (
          <TabsContent value="blocks" className="p-4 focus:outline-none">
            <div className="min-h-[220px] max-h-[550px] overflow-y-auto p-2">
              <LayoutBlocksView blocks={blocks} />
            </div>
          </TabsContent>
        )}

        {/* Tab 4: JSON Tree */}
        <TabsContent value="json" className="p-4 focus:outline-none">
          <pre className="min-h-[220px] max-h-[550px] overflow-auto p-4 rounded-lg bg-slate-950 border border-slate-800 font-mono text-xs text-slate-300">
            {JSON.stringify(resultData || { text }, null, 2)}
          </pre>
        </TabsContent>
      </Tabs>
    </div>
  );
};
