import { Check, Copy, Layers, Search } from "lucide-react";
import type React from "react";
import { useMemo, useState } from "react";

export interface OcrBlock {
  label: string;
  bbox?: number[];
  text: string;
  page?: number;
}

interface LayoutBlocksViewProps {
  blocks: OcrBlock[];
}

export const LayoutBlocksView: React.FC<LayoutBlocksViewProps> = ({ blocks }) => {
  const [selectedLabel, setSelectedLabel] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);

  // Group counts by label
  const labelCounts = useMemo(() => {
    const counts: Record<string, number> = { all: blocks.length };
    blocks.forEach((b) => {
      const l = b.label || "Text";
      counts[l] = (counts[l] || 0) + 1;
    });
    return counts;
  }, [blocks]);

  // Filter blocks
  const filteredBlocks = useMemo(() => {
    return blocks.filter((b) => {
      const matchLabel = selectedLabel === "all" || (b.label || "Text") === selectedLabel;
      const matchSearch =
        !searchQuery.trim() ||
        (b.text && b.text.toLowerCase().includes(searchQuery.toLowerCase())) ||
        (b.label && b.label.toLowerCase().includes(searchQuery.toLowerCase()));
      return matchLabel && matchSearch;
    });
  }, [blocks, selectedLabel, searchQuery]);

  const handleCopy = (text: string, idx: number) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 2000);
  };

  const getLabelColor = (label: string) => {
    const l = (label || "").toLowerCase();
    if (l.includes("header") || l.includes("title")) return "bg-blue-950/60 border-blue-700/60 text-blue-300";
    if (l.includes("table")) return "bg-amber-950/60 border-amber-700/60 text-amber-300";
    if (l.includes("list")) return "bg-emerald-950/60 border-emerald-700/60 text-emerald-300";
    if (l.includes("caption") || l.includes("footnote")) return "bg-purple-950/60 border-purple-700/60 text-purple-300";
    if (l.includes("form")) return "bg-cyan-950/60 border-cyan-700/60 text-cyan-300";
    return "bg-slate-800 border-slate-700 text-slate-300";
  };

  if (!blocks || blocks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-slate-400 text-sm italic">
        <Layers className="w-8 h-8 text-slate-600 mb-2" />
        No structured layout blocks detected. Select a layout-aware backend (like Indra OCR) to inspect blocks.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filter and Search Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-2 bg-slate-950/60 rounded-lg border border-slate-800">
        <div className="flex flex-wrap items-center gap-1.5">
          {Object.entries(labelCounts).map(([label, count]) => (
            <button
              key={label}
              onClick={() => setSelectedLabel(label)}
              className={`px-2.5 py-1 text-xs rounded-full border transition-all flex items-center gap-1.5 ${
                selectedLabel === label
                  ? "bg-blue-600 border-blue-500 text-white font-medium shadow-sm"
                  : "bg-slate-900 border-slate-800 text-slate-300 hover:bg-slate-800"
              }`}
            >
              <span>{label === "all" ? "All Blocks" : label}</span>
              <span className="text-[10px] opacity-75 font-mono">({count})</span>
            </button>
          ))}
        </div>

        <div className="relative min-w-[200px]">
          <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search block text..."
            className="w-full text-xs bg-slate-900 border border-slate-700/80 rounded-md pl-8 pr-3 py-1.5 text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>
      </div>

      {/* Block List */}
      <div className="grid gap-2.5 max-h-[460px] overflow-y-auto pr-1">
        {filteredBlocks.map((block, idx) => (
          <div
            key={idx}
            className="group p-3 rounded-lg border border-slate-800/80 bg-slate-950/40 hover:bg-slate-900/50 hover:border-slate-700 transition-all flex flex-col gap-2"
          >
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <span className={`px-2 py-0.5 rounded text-[11px] font-semibold border ${getLabelColor(block.label)}`}>
                  {block.label || "Text"}
                </span>
                {typeof block.page === "number" && (
                  <span className="text-[10px] text-slate-400 font-mono">Page {block.page + 1}</span>
                )}
                {block.bbox && block.bbox.length === 4 && (
                  <span className="text-[10px] text-slate-400 font-mono hidden sm:inline">
                    [{block.bbox.map((v) => Math.round(v)).join(", ")}]
                  </span>
                )}
              </div>
              <button
                onClick={() => handleCopy(block.text, idx)}
                className="opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-200"
                title="Copy block text"
              >
                {copiedIdx === idx ? (
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                ) : (
                  <Copy className="w-3.5 h-3.5" />
                )}
              </button>
            </div>
            <p className="text-xs sm:text-sm text-slate-200 font-mono whitespace-pre-wrap leading-relaxed">
              {block.text}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
};
