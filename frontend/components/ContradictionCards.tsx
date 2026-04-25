/**
 * ContradictionCards · 模块 3 · 矛盾对卡片列表
 * ------------------------------------------------
 * 从 N×N 矩阵抽出上三角的 contradict 对, 按置信度分 "强 / 中等" 两档
 * (confidence ≥ 0.75 强, 0.5 ≤ c < 0.75 中等, < 0.5 丢弃)
 *
 * 卡片结构:
 *   ┌────────────────────────────────────────────┐
 *   │ 强度色条 (2px, 琥珀 FFB547)                  │
 *   ├──────────────┬────────────┬──────────────┤
 *   │  支持方       │    VS      │   反对方       │
 *   │ 主张 #a       │ 琥珀点 72% │  主张 #b       │
 *   │ 论文标题      │            │  论文标题       │
 *   └──────────────┴────────────┴──────────────┘
 *   展开 → AI 判定理由
 *
 * 顶部筛选: 全部 / 仅强 / 仅中等
 */
"use client";

import { useMemo, useState } from "react";
import type { Claim, Paper, Relation } from "@/lib/api";

interface Props {
  claims: Claim[];
  matrix: Relation[][];
  papers: Paper[];
}

type Strength = "strong" | "medium";
type Filter = "all" | "strong" | "medium";

interface Pair {
  i: number;
  j: number;
  strength: Strength;
  relation: Relation;
}

const STRENGTH_LABEL: Record<Strength, string> = {
  strong: "强矛盾",
  medium: "中等矛盾",
};

export default function ContradictionCards({ claims, matrix, papers }: Props) {
  const paperById = useMemo(() => {
    const m = new Map<number, Paper>();
    papers.forEach((p) => m.set(p.id, p));
    return m;
  }, [papers]);

  const pairs: Pair[] = useMemo(() => {
    const list: Pair[] = [];
    const n = claims.length;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const cell = matrix?.[i]?.[j];
        if (!cell || cell.relation !== "contradict") continue;
        if (cell.confidence >= 0.75) {
          list.push({ i, j, strength: "strong", relation: cell });
        } else if (cell.confidence >= 0.5) {
          list.push({ i, j, strength: "medium", relation: cell });
        }
      }
    }
    // 强 → 中; 同档内按置信度降序
    list.sort((a, b) => {
      if (a.strength !== b.strength) return a.strength === "strong" ? -1 : 1;
      return b.relation.confidence - a.relation.confidence;
    });
    return list;
  }, [claims, matrix]);

  const [filter, setFilter] = useState<Filter>("all");
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  // 整个板块的折叠状态。默认展开 (核心矛盾是页面主信息之一)
  const [sectionOpen, setSectionOpen] = useState(true);

  const shown = pairs.filter((p) => filter === "all" || p.strength === filter);

  const strongCount = pairs.filter((p) => p.strength === "strong").length;
  const mediumCount = pairs.filter((p) => p.strength === "medium").length;

  return (
    <section className="rounded-lg border border-border bg-bg-surface">
      {/* 头部: 标题区可点击折叠, 过滤器仅在展开时显示 */}
      <header
        className={`flex flex-wrap items-center justify-between gap-3 px-6 py-4 ${
          sectionOpen ? "border-b border-border" : ""
        }`}
      >
        <button
          type="button"
          onClick={() => setSectionOpen((v) => !v)}
          className="flex items-center gap-3 text-left transition-opacity hover:opacity-80"
          aria-expanded={sectionOpen}
        >
          <h2 className="text-17 font-medium text-text-primary">核心矛盾对</h2>
          <span className="num text-12 text-text-muted">· {pairs.length} 对</span>
          <span className="text-12 text-text-muted">
            {sectionOpen ? "收起 ▴" : "展开 ▾"}
          </span>
        </button>

        {sectionOpen && (
          <div className="flex items-center gap-1 rounded border border-border bg-bg-elevated p-1 text-12">
            <FilterChip current={filter} value="all" onClick={setFilter}>
              全部 · {pairs.length}
            </FilterChip>
            <FilterChip current={filter} value="strong" onClick={setFilter}>
              仅强 · {strongCount}
            </FilterChip>
            <FilterChip current={filter} value="medium" onClick={setFilter}>
              仅中等 · {mediumCount}
            </FilterChip>
          </div>
        )}
      </header>

      {/* 卡片列表 (折叠时整体不渲染) */}
      {sectionOpen && (
      <div className="divide-y divide-border">
        {shown.length === 0 ? (
          <div className="px-6 py-10 text-center text-13 text-text-muted">
            没有符合筛选条件的矛盾对。
          </div>
        ) : (
          shown.map((pair, idx) => {
            const claimA = claims[pair.i];
            const claimB = claims[pair.j];
            const paperA = paperById.get(claimA.paper_id);
            const paperB = paperById.get(claimB.paper_id);
            const conf = Math.round(pair.relation.confidence * 100);
            const isOpen = openIdx === idx;

            return (
              <article key={`${pair.i}-${pair.j}`} className="relative">
                {/* 左侧色条: 强 = 实心琥珀; 中等 = 虚线琥珀 */}
                <span
                  aria-hidden
                  className={`absolute left-0 top-0 h-full w-[2px] ${
                    pair.strength === "strong"
                      ? "bg-accent-conflict"
                      : "bg-accent-conflict/50"
                  }`}
                />

                <button
                  type="button"
                  onClick={() => setOpenIdx(isOpen ? null : idx)}
                  className="grid w-full grid-cols-1 gap-4 px-6 py-5 text-left transition-colors hover:bg-bg-elevated/30 md:grid-cols-[1fr_auto_1fr]"
                >
                  {/* 支持方 */}
                  <ClaimBlock
                    label="主张"
                    claim={claimA}
                    paperTitle={paperA?.title}
                    paperYear={paperA?.year ?? undefined}
                    align="left"
                  />

                  {/* VS + 置信度 */}
                  <div className="flex flex-row items-center justify-center gap-3 md:flex-col md:py-2">
                    <span className="text-11 uppercase tracking-label text-text-muted">
                      {STRENGTH_LABEL[pair.strength]}
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="h-[6px] w-[6px] rounded-sm bg-accent-conflict" />
                      <span className="num text-14 text-accent-conflict">
                        {conf}%
                      </span>
                    </span>
                    <span className="text-11 text-text-muted">
                      {isOpen ? "收起 ▴" : "展开 ▾"}
                    </span>
                  </div>

                  {/* 反对方 */}
                  <ClaimBlock
                    label="对立主张"
                    claim={claimB}
                    paperTitle={paperB?.title}
                    paperYear={paperB?.year ?? undefined}
                    align="right"
                  />
                </button>

                {/* 展开区: AI 判定理由 */}
                {isOpen && (
                  <div className="border-t border-border bg-bg-inset/60 px-6 py-4 text-13 leading-relaxed text-text-secondary">
                    <div className="eyebrow mb-2">AI 判定理由</div>
                    <p className="text-text-primary">
                      {pair.relation.reason || "（无附加理由）"}
                    </p>
                  </div>
                )}
              </article>
            );
          })
        )}
      </div>
      )}
    </section>
  );
}

function FilterChip({
  current,
  value,
  onClick,
  children,
}: {
  current: Filter;
  value: Filter;
  onClick: (v: Filter) => void;
  children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      onClick={() => onClick(value)}
      className={`rounded-sm px-3 py-1 transition-colors ${
        active
          ? "bg-brand/15 text-brand"
          : "text-text-secondary hover:bg-bg-inset hover:text-text-primary"
      }`}
    >
      {children}
    </button>
  );
}

function ClaimBlock({
  label,
  claim,
  paperTitle,
  paperYear,
  align,
}: {
  label: string;
  claim: Claim;
  paperTitle?: string;
  paperYear?: number;
  align: "left" | "right";
}) {
  return (
    <div className={align === "right" ? "md:text-right" : ""}>
      <div className="eyebrow">{label}</div>
      <p className="mt-2 text-14 leading-relaxed text-text-primary">
        {claim.conclusion}
      </p>
      {paperTitle && (
        <p className="mt-2 text-12 text-text-muted">
          <span className="italic">{paperTitle}</span>
          {paperYear ? <span className="num"> · {paperYear}</span> : null}
        </p>
      )}
    </div>
  );
}
