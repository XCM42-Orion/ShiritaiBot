import type { ReactNode } from "react";

/** A small, safe Markdown subset for release notes: no raw HTML is ever rendered. */
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let list: { ordered: boolean; items: string[] } | null = null;
  let paragraph: string[] = [];

  function flush() {
    if (paragraph.length) {
      blocks.push(<p key={blocks.length}>{inline(paragraph.join(" "))}</p>);
      paragraph = [];
    }
    if (list) {
      const items = list.items.map((item, i) => (
        <li key={i}>{inline(item)}</li>
      ));
      blocks.push(
        list.ordered ? (
          <ol key={blocks.length}>{items}</ol>
        ) : (
          <ul key={blocks.length}>{items}</ul>
        ),
      );
      list = null;
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const code: string[] = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i]))
        code.push(lines[i]);
      blocks.push(<pre key={blocks.length}>{code.join("\n")}</pre>);
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      const level = Math.min(heading[1].length + 2, 6);
      const Tag = `h${level}` as "h3" | "h4" | "h5" | "h6";
      blocks.push(<Tag key={blocks.length}>{inline(heading[2])}</Tag>);
      continue;
    }
    const item = /^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(line);
    if (item) {
      const ordered = Boolean(item[2]);
      if (paragraph.length || (list && list.ordered !== ordered)) flush();
      list ??= { ordered, items: [] };
      list.items.push(item[3]);
      continue;
    }
    if (!line.trim() || /^\s*([-*_])\1{2,}\s*$/.test(line)) {
      flush();
      continue;
    }
    if (list && /^\s{2,}/.test(line)) {
      list.items[list.items.length - 1] += " " + line.trim();
      continue;
    }
    if (list) flush();
    paragraph.push(line.trim());
  }
  flush();
  return <div className="markdown">{blocks}</div>;
}

const TOKEN = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\[[^\]]+\]\([^)\s]+\))/g;

function inline(text: string): ReactNode[] {
  return text.split(TOKEN).map((part, i) => {
    if (!part) return null;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1)
      return <code key={i}>{part.slice(1, -1)}</code>;
    if (/^(\*\*|__).+\1$/.test(part))
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
    if (link) {
      return /^https?:\/\//i.test(link[2]) ? (
        <a key={i} href={link[2]} target="_blank" rel="noreferrer noopener">
          {link[1]}
        </a>
      ) : (
        link[1]
      );
    }
    return part;
  });
}
