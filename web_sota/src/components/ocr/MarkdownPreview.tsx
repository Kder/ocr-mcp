import type React from "react";

interface MarkdownPreviewProps {
  content: string;
  className?: string;
}

/**
 * Lightweight, safe, zero-dependency Markdown & Math Preview component.
 * Renders headings, tables, lists, blockquotes, code blocks, bold/italics, and math formulas.
 */
export const MarkdownPreview: React.FC<MarkdownPreviewProps> = ({ content, className = "" }) => {
  if (!content || !content.trim()) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-slate-400 text-sm italic">
        No document content to display.
      </div>
    );
  }

  // Split content by lines for block processing
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // 1. Fenced Code Block
    if (trimmed.startsWith("```")) {
      const lang = trimmed.slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      elements.push(
        <div key={`code-${i}`} className="my-3 rounded-lg overflow-hidden border border-slate-800 bg-slate-950">
          {lang && (
            <div className="flex items-center justify-between px-3 py-1 bg-slate-900 border-b border-slate-800 text-xs font-mono text-slate-400">
              <span>{lang}</span>
            </div>
          )}
          <pre className="p-3 text-xs sm:text-sm font-mono text-emerald-300 overflow-x-auto whitespace-pre">
            {codeLines.join("\n")}
          </pre>
        </div>,
      );
      continue;
    }

    // 2. Math Block ($$...$$)
    if (trimmed.startsWith("$$")) {
      const mathLines: string[] = [];
      if (trimmed.endsWith("$$") && trimmed.length > 2) {
        mathLines.push(trimmed.slice(2, -2).trim());
        i++;
      } else {
        mathLines.push(trimmed.slice(2).trim());
        i++;
        while (i < lines.length && !lines[i].trim().endsWith("$$")) {
          mathLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) {
          mathLines.push(lines[i].trim().slice(0, -2).trim());
          i++;
        }
      }
      elements.push(
        <div
          key={`mathblock-${i}`}
          className="my-3 p-3 rounded-lg bg-amber-950/20 border border-amber-800/40 text-amber-200 text-center font-mono text-sm overflow-x-auto"
        >
          <div className="text-[10px] uppercase tracking-wider text-amber-400 font-sans font-bold mb-1">
            LaTeX Formula
          </div>
          {mathLines.join(" ")}
        </div>,
      );
      continue;
    }

    // 3. Markdown Table (| header | header |)
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|") && lines[i].trim().endsWith("|")) {
        tableLines.push(lines[i].trim());
        i++;
      }

      if (tableLines.length >= 2) {
        const headerRow = tableLines[0]
          .slice(1, -1)
          .split("|")
          .map((c) => c.trim());
        const isSep = tableLines[1].replace(/[\s|:-]/g, "").length === 0;
        const bodyRows = tableLines.slice(isSep ? 2 : 1).map((row) =>
          row
            .slice(1, -1)
            .split("|")
            .map((c) => c.trim()),
        );

        elements.push(
          <div key={`table-${i}`} className="my-4 overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/60">
            <table className="w-full text-left text-xs sm:text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-800 bg-slate-900/80">
                  {headerRow.map((h, idx) => (
                    <th
                      key={idx}
                      className="px-3 py-2 font-semibold text-slate-200 border-r border-slate-800 last:border-r-0"
                    >
                      {renderInline(h)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {bodyRows.map((row, rIdx) => (
                  <tr key={rIdx} className="hover:bg-slate-900/30 transition-colors">
                    {row.map((cell, cIdx) => (
                      <td key={cIdx} className="px-3 py-2 text-slate-300 border-r border-slate-800/40 last:border-r-0">
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>,
        );
        continue;
      }
    }

    // 4. Headings
    if (trimmed.startsWith("# ")) {
      elements.push(
        <h1
          key={`h1-${i}`}
          className="text-xl sm:text-2xl font-bold text-white mt-4 mb-2 pb-1 border-b border-slate-800 flex items-center gap-2"
        >
          {renderInline(trimmed.slice(2))}
        </h1>,
      );
      i++;
      continue;
    }
    if (trimmed.startsWith("## ")) {
      elements.push(
        <h2 key={`h2-${i}`} className="text-lg sm:text-xl font-bold text-slate-100 mt-3 mb-1.5 flex items-center gap-2">
          <span className="w-1.5 h-4 bg-blue-500 rounded-full inline-block" />
          {renderInline(trimmed.slice(3))}
        </h2>,
      );
      i++;
      continue;
    }
    if (trimmed.startsWith("### ")) {
      elements.push(
        <h3 key={`h3-${i}`} className="text-base sm:text-lg font-semibold text-slate-200 mt-2.5 mb-1">
          {renderInline(trimmed.slice(4))}
        </h3>,
      );
      i++;
      continue;
    }
    if (trimmed.startsWith("#### ")) {
      elements.push(
        <h4 key={`h4-${i}`} className="text-sm font-semibold text-slate-300 mt-2 mb-1">
          {renderInline(trimmed.slice(5))}
        </h4>,
      );
      i++;
      continue;
    }

    // 5. Blockquote
    if (trimmed.startsWith(">")) {
      elements.push(
        <blockquote
          key={`quote-${i}`}
          className="my-2 pl-3 py-1 border-l-4 border-blue-500 bg-blue-950/15 text-slate-300 text-xs sm:text-sm italic rounded-r"
        >
          {renderInline(trimmed.slice(1).trim())}
        </blockquote>,
      );
      i++;
      continue;
    }

    // 6. Horizontal Rule
    if (trimmed === "---" || trimmed === "***" || trimmed === "___") {
      elements.push(<hr key={`hr-${i}`} className="my-4 border-slate-800" />);
      i++;
      continue;
    }

    // 7. Bullet List
    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      elements.push(
        <div key={`bullet-${i}`} className="flex items-start gap-2 my-1 text-xs sm:text-sm text-slate-300">
          <span className="text-blue-400 mt-1">•</span>
          <span>{renderInline(trimmed.slice(2))}</span>
        </div>,
      );
      i++;
      continue;
    }

    // 8. Numbered List
    const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (numMatch) {
      elements.push(
        <div key={`num-${i}`} className="flex items-start gap-2 my-1 text-xs sm:text-sm text-slate-300">
          <span className="font-mono text-xs text-blue-400 font-semibold mt-0.5">{numMatch[1]}.</span>
          <span>{renderInline(numMatch[2])}</span>
        </div>,
      );
      i++;
      continue;
    }

    // 9. Empty line
    if (!trimmed) {
      elements.push(<div key={`empty-${i}`} className="h-2" />);
      i++;
      continue;
    }

    // 10. Normal Paragraph
    elements.push(
      <p key={`p-${i}`} className="my-1.5 text-xs sm:text-sm leading-relaxed text-slate-300">
        {renderInline(line)}
      </p>,
    );
    i++;
  }

  return <div className={`markdown-preview font-sans text-slate-200 ${className}`}>{elements}</div>;
};

/**
 * Helper to parse inline formatting: bold, italic, inline code, inline math ($...$), and links.
 */
function renderInline(text: string): React.ReactNode {
  if (!text) return null;

  // Split on code, math, bold, italic tokens
  const regex = /(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|`[^`]+`|\*\*[^*]+?\*\*|\*[^*]+?\*)/g;
  const parts = text.split(regex);

  return parts.map((part, index) => {
    if (!part) return null;

    // Inline Math ($...$) or ($$...$$)
    if (part.startsWith("$") && part.endsWith("$") && part.length > 2) {
      const formula = part.startsWith("$$") ? part.slice(2, -2) : part.slice(1, -1);
      return (
        <span
          key={index}
          className="inline-block px-1.5 py-0.5 mx-0.5 rounded bg-amber-950/40 border border-amber-800/50 text-amber-300 font-mono text-xs"
          title="LaTeX Math"
        >
          {formula}
        </span>
      );
    }

    // Inline code (`...`)
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={index}
          className="px-1.5 py-0.5 mx-0.5 rounded bg-slate-800 border border-slate-700/60 font-mono text-xs text-emerald-300"
        >
          {part.slice(1, -1)}
        </code>
      );
    }

    // Bold (**...**)
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={index} className="font-semibold text-white">
          {part.slice(2, -2)}
        </strong>
      );
    }

    // Italic (*...*)
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return (
        <em key={index} className="italic text-slate-200">
          {part.slice(1, -1)}
        </em>
      );
    }

    return part;
  });
}
