import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import type { KnowledgeGraphData } from "../types";

interface Props {
  data: KnowledgeGraphData;
  onNodeClick: (slug: string) => void;
}

interface RFNode {
  id: string;
  title: string;
  tags: string[];
  color: string;
}

interface RFLink {
  source: string;
  target: string;
  kind: "wikilink" | "tag" | "doc";
}

// 첫 태그 기반 결정적 해시 색상
function colorForTag(tag: string | undefined): string {
  if (!tag) return "#888";
  let h = 0;
  for (let i = 0; i < tag.length; i++) h = (h * 31 + tag.charCodeAt(i)) % 360;
  return `hsl(${h}, 60%, 60%)`;
}

const LINK_COLOR: Record<RFLink["kind"], string> = {
  wikilink: "#7aa8ff",
  tag: "rgba(180,180,180,0.5)",
  doc: "#4caf84",
};

export default function NotesGraph({ data, onNodeClick }: Props): React.ReactElement {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 400, h: 300 });

  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const ro = new ResizeObserver(() => {
      setSize({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const graphData = useMemo(() => {
    const nodes: RFNode[] = data.nodes.map((n) => ({
      id: n.slug,
      title: n.title,
      tags: n.tags,
      color: colorForTag(n.tags[0]),
    }));
    const links: RFLink[] = data.edges.map((e) => ({
      source: e.source,
      target: e.target,
      kind: e.kind,
    }));
    return { nodes, links };
  }, [data]);

  return (
    <div ref={wrapRef} style={{ flex: 1, position: "relative", overflow: "hidden", background: "var(--color-bg)" }}>
      {graphData.nodes.length === 0 ? (
        <div style={{ padding: 20, color: "var(--color-text-muted)", fontSize: 12 }}>
          노트가 없습니다.
        </div>
      ) : (
        <ForceGraph2D
          width={size.w}
          height={size.h}
          graphData={graphData}
          nodeLabel={(n) => (n as unknown as RFNode).title}
          nodeColor={(n) => (n as unknown as RFNode).color}
          nodeRelSize={5}
          linkColor={(l) => LINK_COLOR[(l as unknown as RFLink).kind]}
          linkLineDash={(l) =>
            (l as unknown as RFLink).kind === "tag" ? [4, 4] : null
          }
          linkWidth={(l) =>
            (l as unknown as RFLink).kind === "wikilink" ? 1.6 : 0.8
          }
          onNodeClick={(node) => {
            const n = node as unknown as RFNode;
            onNodeClick(n.id);
          }}
          nodeCanvasObjectMode={() => "after"}
          nodeCanvasObject={(node, ctx, scale) => {
            const n = node as unknown as RFNode & { x?: number; y?: number };
            if (n.x === undefined || n.y === undefined) return;
            const label = n.title;
            const fontSize = 11 / scale;
            ctx.font = `${fontSize}px sans-serif`;
            ctx.fillStyle = "rgba(255,255,255,0.85)";
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillText(label, n.x, n.y + 7);
          }}
        />
      )}
    </div>
  );
}
