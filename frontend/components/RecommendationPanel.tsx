"use client";

import { useMemo, useState } from "react";
import { FlaskConical, Lightbulb } from "lucide-react";
import type {
  Claim,
  Paper,
  Relation,
  Recommendation,
  Recommendations,
} from "@/lib/api";

interface Props {
  query: string;
  claims: Claim[];
  matrix: Relation[][];
  papers: Paper[];
  embedded?: boolean;
  value?: Recommendations | null;
  meta?: { ms: number; model: string } | null;
  onLoaded?: (data: Recommendations, meta: { ms: number; model: string }) => void;
}

export default function RecommendationPanel({
  claims,
  matrix,
  embedded = false,
  value,
  meta,
}: Props) {
  const [tab, setTab] = useState<"questions" | "methods">("questions");
  const conflictCount = useMemo(() => {
    let count = 0;
    for (let i = 0; i < claims.length; i++) {
      for (let j = i + 1; j < claims.length; j++) {
        const cell = matrix?.[i]?.[j];
        if (cell?.relation === "contradict" && cell.confidence >= 0.5) count++;
      }
    }
    return count;
  }, [claims, matrix]);

  const body = value ? (
    <div>
      <header className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Lightbulb className="h-4 w-4 text-[#B4AEFF]" aria-hidden />
            <h3 className="text-15 font-medium text-text-primary">研究方向建议</h3>
          </div>
          <p className="mt-2 text-11 text-text-muted">
            基于 {conflictCount} 组矛盾观点生成
          </p>
        </div>
        {meta && (
          <span className="num text-10 text-text-muted">
            {(meta.ms / 1000).toFixed(1)}s · {meta.model}
          </span>
        )}
      </header>

      <div className="mt-4 flex rounded-sm bg-bg-inset p-[3px]">
        <TabButton active={tab === "questions"} onClick={() => setTab("questions")}>
          研究问题 · {value.questions.length}
        </TabButton>
        <TabButton active={tab === "methods"} onClick={() => setTab("methods")}>
          方法路线 · {value.methods.length}
        </TabButton>
      </div>

      <div className="mt-4 space-y-3">
        {(tab === "questions" ? value.questions : value.methods).map((item, index) => (
          <RecommendationItem key={`${item.title}-${index}`} item={item} />
        ))}
      </div>
    </div>
  ) : (
    <div className="py-8 text-center">
      <FlaskConical className="mx-auto h-5 w-5 text-text-muted" aria-hidden />
      <p className="mt-3 text-12 text-text-muted">暂无研究方向建议</p>
    </div>
  );

  if (embedded) return body;
  return <section className="rounded-lg border border-border bg-bg-surface p-6">{body}</section>;
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex-1 rounded-sm py-1.5 text-12 transition-colors"
      style={{
        background: active ? "rgba(180,174,255,0.12)" : "transparent",
        color: active ? "#B4AEFF" : "#9FA5B8",
      }}
    >
      {children}
    </button>
  );
}

function RecommendationItem({ item }: { item: Recommendation }) {
  const priorityColor =
    item.priority === "high"
      ? "#FFB547"
      : item.priority === "medium"
        ? "#B4AEFF"
        : "#6B6985";
  const priorityLabel =
    item.priority === "high"
      ? "高优先级"
      : item.priority === "medium"
        ? "中优先级"
        : "低优先级";

  return (
    <article className="relative overflow-hidden rounded-sm border border-border bg-bg-inset px-4 py-3">
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-[2px]"
        style={{ background: priorityColor }}
      />
      <div className="flex items-start gap-3">
        <h4 className="min-w-0 flex-1 text-13 font-medium leading-snug text-text-primary">
          {item.title}
        </h4>
        <span className="shrink-0 text-10" style={{ color: priorityColor }}>
          {priorityLabel}
        </span>
      </div>
      <p className="mt-2 text-12 leading-6 text-text-secondary">{item.desc}</p>
      {item.sources.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {item.sources.map((source) => (
            <span
              key={source}
              className="rounded-sm bg-[rgba(255,181,71,0.08)] px-2 py-0.5 text-10 text-[#FFB547]"
            >
              {source}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
