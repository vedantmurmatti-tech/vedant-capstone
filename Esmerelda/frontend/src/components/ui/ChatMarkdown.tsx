import { Fragment } from "react";

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*(.+?)\*\*|`([^`]+)`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;

  while ((match = regex.exec(text))) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    if (match[2] !== undefined) {
      parts.push(
        <strong key={`${keyPrefix}-b-${i++}`} className="font-semibold text-warm-50">
          {match[2]}
        </strong>
      );
    } else if (match[3] !== undefined) {
      parts.push(
        <code
          key={`${keyPrefix}-c-${i++}`}
          className="rounded bg-graphite-800 px-1.5 py-0.5 font-mono text-[13px] text-cyan-300"
        >
          {match[3]}
        </code>
      );
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

interface Block {
  type: "code" | "list" | "paragraph";
  content: string;
  lang?: string;
  items?: string[];
}

function parseBlocks(markdown: string): Block[] {
  const blocks: Block[] = [];
  const segments = markdown.split(/```(\w*)\n([\s\S]*?)```/g);

  // split() with capturing groups yields [text, lang, code, text, lang, code, ..., text]
  for (let i = 0; i < segments.length; i += 3) {
    const text = segments[i];
    const lang = segments[i + 1];
    const code = segments[i + 2];

    if (text && text.trim()) {
      for (const para of text.trim().split(/\n\n+/)) {
        const lines = para.split("\n").filter(Boolean);
        if (lines.every((l) => l.trim().startsWith("- "))) {
          blocks.push({ type: "list", content: para, items: lines.map((l) => l.replace(/^- /, "")) });
        } else {
          blocks.push({ type: "paragraph", content: para });
        }
      }
    }
    if (code !== undefined) {
      blocks.push({ type: "code", content: code.replace(/\n$/, ""), lang: lang || "text" });
    }
  }

  return blocks;
}

export function ChatMarkdown({ content }: { content: string }) {
  const blocks = parseBlocks(content);

  return (
    <div className="space-y-3">
      {blocks.map((block, idx) => {
        if (block.type === "code") {
          return (
            <div key={idx} className="overflow-hidden rounded-xl border border-graphite-700/70 bg-graphite-950/80">
              <div className="flex items-center justify-between border-b border-graphite-700/70 px-3.5 py-2">
                <span className="font-mono text-[11px] uppercase tracking-wide text-graphite-500">{block.lang}</span>
              </div>
              <pre className="overflow-x-auto px-3.5 py-3 font-mono text-[13px] leading-relaxed text-graphite-200">
                <code>{block.content}</code>
              </pre>
            </div>
          );
        }
        if (block.type === "list") {
          return (
            <ul key={idx} className="space-y-1.5 pl-1">
              {block.items?.map((item, i) => (
                <li key={i} className="flex gap-2 text-[14px] leading-relaxed text-graphite-200">
                  <span className="mt-2 size-1 shrink-0 rounded-full bg-cyan-400/80" />
                  <span>{renderInline(item, `${idx}-${i}`)}</span>
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={idx} className="text-[14px] leading-relaxed text-graphite-200">
            {block.content.split("\n").map((line, i, arr) => (
              <Fragment key={i}>
                {renderInline(line, `${idx}-${i}`)}
                {i < arr.length - 1 && <br />}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
