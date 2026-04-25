/**
 * DebateNetwork · 观点矛盾网络
 * ----------------------------------------
 * 节点 = 论文 (仅包含参与矛盾的)
 * 连线 = 论文之间的矛盾 (同一对论文内多个 claim 矛盾聚合为一条边, 取最大 confidence)
 *
 * 数据派生全部在前端 (useMemo), 后端无需改动。
 *
 * 过滤规则:
 *   · 默认只保留 conflictCount ≥ 1 的论文
 *   · 若 < 8 节点 → 放宽阈值, 显示所有论文
 *   · 若 > 30 节点 → 按 conflictCount 降序保留 Top 25
 *
 * stance 派生:
 *   所有该论文 claim.direction 全 positive → support
 *   全 negative → oppose
 *   其余 (混合 / 含 neutral / 无 claim) → mixed   ← 同时作为 fallback
 */
"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { Claim, Paper, Relation } from "@/lib/api";

interface Props {
  claims: Claim[];
  matrix: Relation[][];
  papers: Paper[];
}

type Stance = "support" | "oppose" | "mixed";
type Strength = "strong" | "medium" | "weak";

interface GraphNode {
  id: string;
  name: string;
  conflictCount: number;
  stance: Stance;
}

interface GraphEdge {
  source: string;
  target: string;
  confidence: number;
  strength: Strength;
}

/* ===== 辅助函数 ===== */
function shortName(p: Paper): string {
  const firstAuthor = (p.authors && p.authors[0]) || "";
  const lastName =
    firstAuthor.trim().split(/\s+/).pop() || firstAuthor || "anon";
  const year = p.year ?? "n.d.";
  return `${lastName} ${year}`;
}

function stanceOf(cs: Claim[]): Stance {
  if (!cs.length) return "mixed";
  const pos = cs.filter((c) => c.direction === "positive").length;
  const neg = cs.filter((c) => c.direction === "negative").length;
  if (pos > 0 && neg === 0) return "support";
  if (neg > 0 && pos === 0) return "oppose";
  return "mixed";
}

function strengthOf(conf: number): Strength {
  if (conf >= 0.75) return "strong";
  if (conf >= 0.5) return "medium";
  return "weak";
}

/* ===== 组件 ===== */
export default function DebateNetwork({ claims, matrix, papers }: Props) {
  const { nodes, edges, metrics } = useMemo(() => {
    /* ---- 1. 按论文聚合 claim ---- */
    const claimsByPaper = new Map<number, Claim[]>();
    for (const c of claims) {
      if (!claimsByPaper.has(c.paper_id)) claimsByPaper.set(c.paper_id, []);
      claimsByPaper.get(c.paper_id)!.push(c);
    }

    /* ---- 2. 扫描矩阵, 聚合为论文-论文边 ---- */
    // key = sortedPair, 取该对之间的最大 confidence
    const pairMap = new Map<
      string,
      { source: string; target: string; maxConf: number }
    >();

    const n = claims.length;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const cell = matrix?.[i]?.[j];
        if (!cell || cell.relation !== "contradict") continue;
        const a = claims[i].paper_id;
        const b = claims[j].paper_id;
        if (a === b) continue; // 同一篇论文内部矛盾不画
        const lo = Math.min(a, b);
        const hi = Math.max(a, b);
        const key = `${lo}_${hi}`;
        const prev = pairMap.get(key);
        if (!prev) {
          pairMap.set(key, {
            source: String(lo),
            target: String(hi),
            maxConf: cell.confidence,
          });
        } else if (cell.confidence > prev.maxConf) {
          prev.maxConf = cell.confidence;
        }
      }
    }

    /* ---- 3. 每篇论文的 conflictCount ---- */
    const pairList = Array.from(pairMap.values());
    const conflictCount = new Map<number, number>();
    pairList.forEach((e) => {
      const a = Number(e.source);
      const b = Number(e.target);
      conflictCount.set(a, (conflictCount.get(a) || 0) + 1);
      conflictCount.set(b, (conflictCount.get(b) || 0) + 1);
    });

    /* ---- 4. 节点过滤: ≥1 → 放宽 → 截断 ---- */
    let visiblePapers = papers.filter(
      (p) => (conflictCount.get(p.id) || 0) >= 1
    );
    if (visiblePapers.length < 8) {
      visiblePapers = [...papers];
    }
    if (visiblePapers.length > 30) {
      visiblePapers = [...visiblePapers]
        .sort(
          (a, b) =>
            (conflictCount.get(b.id) || 0) - (conflictCount.get(a.id) || 0)
        )
        .slice(0, 25);
    }

    const visibleSet = new Set(visiblePapers.map((p) => String(p.id)));

    /* ---- 5. 组装 nodes ---- */
    const nodes: GraphNode[] = visiblePapers.map((p) => ({
      id: String(p.id),
      name: shortName(p),
      conflictCount: conflictCount.get(p.id) || 0,
      stance: stanceOf(claimsByPaper.get(p.id) || []),
    }));

    /* ---- 6. 过滤 edges 到可见节点 ---- */
    const edges: GraphEdge[] = pairList
      .filter((e) => visibleSet.has(e.source) && visibleSet.has(e.target))
      .map((e) => ({
        source: e.source,
        target: e.target,
        confidence: e.maxConf,
        strength: strengthOf(e.maxConf),
      }));

    /* ---- 7. 指标卡 ---- */
    const edgeCount = edges.length;
    const avgConf =
      edgeCount > 0
        ? edges.reduce((s, e) => s + e.confidence, 0) / edgeCount
        : 0;
    const center =
      nodes.length > 0
        ? nodes.reduce(
            (best, cur) => (cur.conflictCount > best.conflictCount ? cur : best),
            nodes[0]
          )
        : null;

    return {
      nodes,
      edges,
      metrics: {
        edgeCount,
        avgConf,
        centerName: center?.name || "—",
      },
    };
  }, [claims, matrix, papers]);

  /* ===== ECharts option ===== */
  const option = useMemo(() => {
    const stanceIdx: Record<Stance, number> = {
      support: 0,
      oppose: 1,
      mixed: 2,
    };

    return {
      backgroundColor: "transparent",
      tooltip: {
        backgroundColor: "#14162A",
        borderColor: "rgba(255,255,255,0.1)",
        borderWidth: 0.5,
        textStyle: { color: "#E8E6F5", fontSize: 12 },
        formatter: (p: {
          dataType?: string;
          name?: string;
          value?: number | string;
        }) => {
          if (p.dataType === "edge") return String(p.value ?? "");
          return `${p.name}<br/><span style="color:#9FA5B8;font-size:11px;">参与 ${p.value} 组矛盾</span>`;
        },
      },
      legend: { show: false }, // 右上 legend 由外部 HTML 实现
      series: [
        {
          type: "graph",
          layout: "force",
          data: nodes.map((n) => ({
            id: n.id,
            name: n.name,
            value: n.conflictCount,
            symbolSize: Math.max(20, 18 + n.conflictCount * 5),
            category: stanceIdx[n.stance],
            label: {
              show: true,
              position: "bottom",
              distance: 6,
              color: "#9FA5B8",
              fontSize: 10,
            },
          })),
          links: edges.map((e) => ({
            source: e.source,
            target: e.target,
            value: `${
              e.strength === "strong"
                ? "强"
                : e.strength === "medium"
                ? "中"
                : "弱"
            }矛盾 · ${e.confidence.toFixed(2)}`,
            lineStyle: {
              color: "#FFB547",
              width: 1 + e.confidence * 2.5,
              opacity: 0.4 + e.confidence * 0.5,
              curveness: 0.15,
            },
          })),
          categories: [
            {
              name: "支持立场",
              itemStyle: {
                color: "#4ADE80",
                borderColor: "rgba(74,222,128,0.3)",
                borderWidth: 3,
              },
            },
            {
              name: "反对立场",
              itemStyle: {
                color: "#FFB547",
                borderColor: "rgba(255,181,71,0.3)",
                borderWidth: 3,
              },
            },
            {
              name: "混合立场",
              itemStyle: {
                color: "#B4AEFF",
                borderColor: "rgba(180,174,255,0.3)",
                borderWidth: 3,
              },
            },
          ],
          roam: true,
          draggable: true,
          force: {
            repulsion: 650,
            edgeLength: [100, 200],
            gravity: 0.08,
            layoutAnimation: true,
          },
          itemStyle: { opacity: 0.92 },
          emphasis: {
            focus: "adjacency",
            scale: 1.15,
            lineStyle: { width: 4, opacity: 1 },
            label: { fontSize: 12, color: "#E8E6F5" },
          },
          zoom: 1.1,
        },
      ],
    };
  }, [nodes, edges]);

  if (nodes.length < 2) {
    return (
      <section className="rounded-lg border border-border bg-bg-surface p-6 text-13 text-text-muted">
        参与矛盾的论文不足 2 篇, 无法构建网络图。
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-border bg-bg-surface">
      {/* 标题行 */}
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-6 py-4">
        <div>
          <h2 className="text-17 font-medium text-text-primary">观点矛盾网络</h2>
          <p className="mt-1 text-11" style={{ color: "#6B6985" }}>
            显示参与矛盾的 {nodes.length} 篇论文 · 连线为观点冲突 · 粗细为置信度
          </p>
        </div>

        {/* 右上角 stance legend */}
        <div
          className="flex items-center gap-4 text-11"
          style={{ color: "#9FA5B8" }}
        >
          <StanceDot color="#4ADE80" label="支持立场" />
          <StanceDot color="#FFB547" label="反对立场" />
          <StanceDot color="#B4AEFF" label="混合立场" />
        </div>
      </header>

      {/* 图表容器: 按 spec 用 #0A0D20 + 圆角 6px */}
      <div className="px-6 pt-4">
        <div
          style={{
            background: "#0A0D20",
            borderRadius: "6px",
            overflow: "hidden",
          }}
        >
          <ReactECharts
            option={option}
            style={{ height: 520, width: "100%" }}
            notMerge
            lazyUpdate
            opts={{ renderer: "canvas" }}
          />
        </div>
      </div>

      {/* 底部 3 指标卡 */}
      <div className="grid grid-cols-3 gap-2 px-6 pb-6 pt-4">
        <MetricCard
          label="冲突边数"
          value={String(metrics.edgeCount)}
          valueColor="#FFB547"
        />
        <MetricCard
          label="平均置信度"
          value={metrics.avgConf.toFixed(2)}
          valueColor="#E8E6F5"
        />
        <MetricCard
          label="争论中心"
          value={metrics.centerName}
          valueColor="#E8E6F5"
          compact
        />
      </div>
    </section>
  );
}

/* ===== 子组件 ===== */
function StanceDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className="inline-block rounded-full"
        style={{ width: 8, height: 8, background: color }}
      />
      {label}
    </span>
  );
}

function MetricCard({
  label,
  value,
  valueColor,
  compact,
}: {
  label: string;
  value: string;
  valueColor: string;
  compact?: boolean;
}) {
  return (
    <div
      className="rounded-sm"
      style={{
        background: "#14162A",
        border: "0.5px solid rgba(255,255,255,0.06)",
        padding: "10px 14px",
      }}
    >
      <div
        className="uppercase"
        style={{
          fontSize: "10px",
          letterSpacing: "0.08em",
          color: "#6B6985",
        }}
      >
        {label}
      </div>
      <div
        className="num mt-1 truncate"
        style={{
          fontSize: compact ? "13px" : "16px",
          fontWeight: 500,
          color: valueColor,
        }}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}
