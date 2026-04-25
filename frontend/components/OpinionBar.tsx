/**
 * OpinionBar · 单行 6px 立场分布条
 * ----------------------------------------
 * 把 N 条 claim 按 direction 聚合成三段横向堆叠条:
 *   positive (支持) → 翡翠
 *   neutral  (中立) → 灰蓝
 *   negative (反对) → 琥珀 (表示分歧/对立, 不用红色)
 *
 * 用法:
 *   <OpinionBar claims={result.claims} />
 */
"use client";

import type { Claim } from "@/lib/api";

interface Props {
  claims: Claim[];
  className?: string;
}

export default function OpinionBar({ claims, className = "" }: Props) {
  const total = claims.length || 1;
  const pos = claims.filter((c) => c.direction === "positive").length;
  const neg = claims.filter((c) => c.direction === "negative").length;
  const neu = Math.max(0, total - pos - neg);

  const pct = (n: number) => (n / total) * 100;

  return (
    <div className={className}>
      <div className="flex items-center justify-between text-12 text-text-secondary mb-2">
        <span className="eyebrow">观点分布</span>
        <span className="num">共 {claims.length} 条主张</span>
      </div>

      {/* 6px 堆叠条 · 使用 border-radius 4px */}
      <div className="flex h-[6px] w-full overflow-hidden rounded-sm bg-bg-elevated">
        {pos > 0 && (
          <div
            title={`支持 · ${pos} 条 (${Math.round(pct(pos))}%)`}
            style={{ width: `${pct(pos)}%` }}
            className="bg-accent-support"
          />
        )}
        {neu > 0 && (
          <div
            title={`中立 · ${neu} 条 (${Math.round(pct(neu))}%)`}
            style={{ width: `${pct(neu)}%` }}
            className="bg-accent-neutral"
          />
        )}
        {neg > 0 && (
          <div
            title={`反对 · ${neg} 条 (${Math.round(pct(neg))}%)`}
            style={{ width: `${pct(neg)}%` }}
            className="bg-accent-conflict"
          />
        )}
      </div>

      {/* 图例 · 数字+小方块, 无纯色胶囊 */}
      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-12 text-text-secondary">
        <LegendDot color="bg-accent-support" label="支持" count={pos} total={total} />
        <LegendDot color="bg-accent-neutral" label="中立" count={neu} total={total} />
        <LegendDot color="bg-accent-conflict" label="反对" count={neg} total={total} />
      </div>
    </div>
  );
}

function LegendDot({
  color,
  label,
  count,
  total,
}: {
  color: string;
  label: string;
  count: number;
  total: number;
}) {
  const pct = total ? Math.round((count / total) * 100) : 0;
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`inline-block h-[8px] w-[8px] rounded-sm ${color}`} />
      <span className="text-text-secondary">{label}</span>
      <span className="num text-text-primary">{count}</span>
      <span className="num text-text-muted">· {pct}%</span>
    </span>
  );
}
