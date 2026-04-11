/**
 * PaperTrace - 切片 8：矛盾矩阵热力图
 * =====================================
 * 用 ECharts 把 N×N 的关系矩阵画成可交互热力图。
 *
 * 颜色编码：
 *   - 矛盾(contradict)：红色，confidence 越高越深
 *   - 支持(support):    绿色，confidence 越高越深
 *   - 无关(unrelated):  灰色（无差别）
 *
 * 编码技巧：
 *   ECharts 单系列热力图只能传一维数值。我们把 relation + confidence
 *   编码成一个有符号数：
 *     support    →  +confidence  ( 0 ~ +1 )
 *     contradict →  -confidence  (-1 ~  0 )
 *     unrelated  →  0            (落到中间的灰色 piece)
 *   再用 visualMap.pieces 把不同区间映射到不同颜色。
 *
 * 交互：
 *   - hover：tooltip 显示两条主张的完整内容 + 判定理由
 *   - click：弹出一个 Modal，更大的视图，方便答辩演示
 *
 * 响应式：
 *   - 外层容器 aspect-square + max-w，自动适应屏幕
 *   - mobile 上 ECharts 会自动按容器大小重排
 */

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import ReactECharts from "echarts-for-react";
import type { Claim, Relation } from "@/lib/api";

// ============== Props ==============
export interface ContradictionMatrixProps {
  /** 主张列表，与矩阵的行/列顺序一致 */
  claims: Claim[];
  /** N×N 矩阵 */
  matrix: Relation[][];
  /** 可选：论文标题映射，用于在标签里显示更友好的论文短名 */
  paperTitleById?: Record<number, string>;
}

// ============== 颜色常量（参考 Tailwind 调色板）==============
// support 系列（绿）
const SUPPORT_COLORS = {
  strong: "#15803d", // green-700
  medium: "#22c55e", // green-500
  weak: "#86efac",   // green-300
};
// contradict 系列（红）
const CONTRADICT_COLORS = {
  strong: "#b91c1c", // red-700
  medium: "#ef4444", // red-500
  weak: "#fca5a5",   // red-300
};
const UNRELATED_COLOR = "#334155"; // slate-700，深色背景下不显眼但仍可见

// ============== 工具函数 ==============

/** 给一条 claim 生成简短的轴标签，例如 "P1·C2" */
function shortLabel(claim: Claim, index: number): string {
  // 用全局序号 + paper 标记，避免重复
  return `#${index + 1}·P${claim.paper_id}`;
}

/** 把 relation + confidence 编码成有符号数（喂给 ECharts）*/
function encodeRelation(rel: Relation): number {
  if (rel.relation === "support") return rel.confidence;
  if (rel.relation === "contradict") return -rel.confidence;
  return 0; // unrelated
}

// ============== 主组件 ==============
export default function ContradictionMatrix({
  claims,
  matrix,
  paperTitleById = {},
}: ContradictionMatrixProps) {
  // 选中的格子（点击后打开 modal 用）
  const [selected, setSelected] = useState<{
    i: number;
    j: number;
    cell: Relation;
  } | null>(null);

  // ECharts 实例引用，用于手动 resize
  const chartRef = useRef<ReactECharts | null>(null);

  // 边界处理
  const n = claims.length;
  const isEmpty = n === 0 || matrix.length === 0;

  // 标签数组
  const labels = useMemo(
    () => claims.map((c, i) => shortLabel(c, i)),
    [claims]
  );

  // 把矩阵展平成 ECharts 需要的格式：[[xIdx, yIdx, value], ...]
  // 注意 ECharts 热力图的坐标：x 是横轴(列)，y 是纵轴(行)
  // 我们让 x = 列索引(j), y = 行索引(i)
  // 但 ECharts y 轴默认是从下往上，要"翻转"才符合矩阵直觉（左上角是 [0,0]）
  const data = useMemo(() => {
    const points: [number, number, number][] = [];
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const cell = matrix[i]?.[j];
        if (!cell) continue;
        points.push([j, i, encodeRelation(cell)]);
      }
    }
    return points;
  }, [matrix, n]);

  // ECharts 配置项
  const option: EChartsOption = useMemo(
    () => ({
      backgroundColor: "transparent",
      // tooltip 显示两条 claim 的完整信息
      tooltip: {
        position: "top",
        backgroundColor: "rgba(15, 23, 42, 0.95)", // slate-900
        borderColor: "rgba(99, 102, 241, 0.4)", // indigo-500/40
        borderWidth: 1,
        textStyle: { color: "#e2e8f0", fontSize: 12 },
        extraCssText: "max-width: 360px; white-space: normal;",
        formatter: (params: unknown) => {
          // ECharts 类型在 formatter 里很难精确，做 narrow
          const p = params as { data: [number, number, number] };
          const [x, y] = p.data;
          const claimA = claims[y];
          const claimB = claims[x];
          const cell = matrix[y]?.[x];
          if (!claimA || !claimB || !cell) return "";

          const dirBadge = (d: string) =>
            d === "positive"
              ? '<span style="color:#86efac">●</span>'
              : d === "negative"
              ? '<span style="color:#fca5a5">●</span>'
              : '<span style="color:#94a3b8">●</span>';

          const relColor =
            cell.relation === "contradict"
              ? "#fca5a5"
              : cell.relation === "support"
              ? "#86efac"
              : "#94a3b8";

          return `
            <div style="font-weight:600;color:${relColor};margin-bottom:6px;">
              ${cell.relation.toUpperCase()} · ${(cell.confidence * 100).toFixed(0)}%
            </div>
            <div style="margin-bottom:6px;">
              <div style="color:#94a3b8;font-size:11px;">CLAIM #${y + 1}</div>
              <div>${dirBadge(claimA.direction)} ${escapeHtml(claimA.subject)} · ${escapeHtml(
                claimA.intervention
              )}</div>
              <div style="color:#cbd5e1;">${escapeHtml(claimA.conclusion)}</div>
            </div>
            <div style="margin-bottom:6px;">
              <div style="color:#94a3b8;font-size:11px;">CLAIM #${x + 1}</div>
              <div>${dirBadge(claimB.direction)} ${escapeHtml(claimB.subject)} · ${escapeHtml(
                claimB.intervention
              )}</div>
              <div style="color:#cbd5e1;">${escapeHtml(claimB.conclusion)}</div>
            </div>
            <div style="color:#94a3b8;font-size:11px;border-top:1px solid #334155;padding-top:4px;">
              ${escapeHtml(cell.reason)}
            </div>
          `;
        },
      },

      // 网格区域：留出空间给坐标轴标签
      grid: {
        top: 30,
        right: 20,
        bottom: 60,
        left: 60,
        containLabel: true,
      },

      // X 轴：列
      xAxis: {
        type: "category",
        data: labels,
        splitArea: { show: true },
        axisLabel: {
          color: "#94a3b8",
          fontSize: 10,
          interval: 0,
          rotate: 60,
        },
        axisLine: { lineStyle: { color: "#475569" } },
      },

      // Y 轴：行（反转，让 [0,0] 出现在左上角，符合矩阵习惯）
      yAxis: {
        type: "category",
        data: labels,
        inverse: true,
        splitArea: { show: true },
        axisLabel: {
          color: "#94a3b8",
          fontSize: 10,
          interval: 0,
        },
        axisLine: { lineStyle: { color: "#475569" } },
      },

      // 视觉映射：把数值映射成颜色
      visualMap: {
        type: "piecewise",
        show: true,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        textStyle: { color: "#94a3b8", fontSize: 10 },
        itemWidth: 14,
        itemHeight: 12,
        itemGap: 4,
        pieces: [
          { gt: 0.7, lte: 1.0, color: SUPPORT_COLORS.strong, label: "强支持" },
          { gt: 0.4, lte: 0.7, color: SUPPORT_COLORS.medium, label: "中支持" },
          { gt: 0.05, lte: 0.4, color: SUPPORT_COLORS.weak, label: "弱支持" },
          { gte: -0.05, lte: 0.05, color: UNRELATED_COLOR, label: "无关" },
          { gte: -0.4, lt: -0.05, color: CONTRADICT_COLORS.weak, label: "弱矛盾" },
          { gte: -0.7, lt: -0.4, color: CONTRADICT_COLORS.medium, label: "中矛盾" },
          { gte: -1.0, lt: -0.7, color: CONTRADICT_COLORS.strong, label: "强矛盾" },
        ],
      },

      series: [
        {
          name: "Relation",
          type: "heatmap",
          data,
          label: { show: false },
          itemStyle: {
            borderColor: "rgba(255,255,255,0.05)",
            borderWidth: 1,
          },
          emphasis: {
            itemStyle: {
              borderColor: "#a5b4fc", // indigo-300
              borderWidth: 2,
              shadowBlur: 10,
              shadowColor: "rgba(99, 102, 241, 0.5)",
            },
          },
          progressive: 1000, // 大矩阵分批渲染
        },
      ],
    }),
    [data, labels, claims, matrix]
  );

  // 点击事件：打开 modal
  const onEvents = useMemo(
    () => ({
      click: (params: unknown) => {
        const p = params as { data: [number, number, number] };
        const [x, y] = p.data;
        const cell = matrix[y]?.[x];
        if (!cell) return;
        setSelected({ i: y, j: x, cell });
      },
    }),
    [matrix]
  );

  // ESC 关 modal
  useEffect(() => {
    if (!selected) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selected]);

  // 容器大小变化时手动 resize（防止某些场景 chart 不重排）
  useEffect(() => {
    const handler = () => {
      chartRef.current?.getEchartsInstance?.()?.resize?.();
    };
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);

  // ============== 渲染 ==============
  if (isEmpty) {
    return (
      <div className="rounded-xl border border-dashed border-white/20 bg-white/5 p-8 text-center text-slate-400">
        没有可展示的主张。
      </div>
    );
  }

  return (
    <div className="relative">
      {/* 图例说明 */}
      <p className="mb-2 text-xs text-slate-400">
        提示：hover 单元格看完整内容；click 单元格打开判定详情
      </p>

      {/* 图表容器：响应式正方形 */}
      <div className="aspect-square w-full overflow-hidden rounded-xl border border-white/10 bg-slate-950/40 p-2 backdrop-blur-md">
        <ReactECharts
          ref={chartRef}
          option={option}
          style={{ height: "100%", width: "100%" }}
          onEvents={onEvents}
          opts={{ renderer: "canvas" }}
          notMerge={true}
        />
      </div>

      {/* Modal */}
      {selected && (
        <CellModal
          claimA={claims[selected.i]}
          claimB={claims[selected.j]}
          rel={selected.cell}
          onClose={() => setSelected(null)}
          paperTitleById={paperTitleById}
        />
      )}
    </div>
  );
}

// ============== Modal 组件 ==============

interface CellModalProps {
  claimA: Claim;
  claimB: Claim;
  rel: Relation;
  paperTitleById: Record<number, string>;
  onClose: () => void;
}

function CellModal({
  claimA,
  claimB,
  rel,
  paperTitleById,
  onClose,
}: CellModalProps) {
  const relStyle =
    rel.relation === "contradict"
      ? "border-rose-400/40 bg-rose-500/10 text-rose-200"
      : rel.relation === "support"
      ? "border-emerald-400/40 bg-emerald-500/10 text-emerald-200"
      : "border-slate-400/40 bg-slate-500/10 text-slate-200";

  return (
    <div
      // 整个屏幕的暗色背板
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        // 阻止点击 modal 内部时关闭
        onClick={(e) => e.stopPropagation()}
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-white/10 bg-slate-900 p-6 shadow-2xl"
      >
        {/* 头部：关系徽章 + 关闭 */}
        <div className="mb-4 flex items-start justify-between gap-2">
          <div className={`rounded-full border px-3 py-1 text-sm ${relStyle}`}>
            {rel.relation.toUpperCase()} · 置信度{" "}
            {(rel.confidence * 100).toFixed(0)}%
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-white/10 hover:text-white"
            aria-label="关闭"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>

        {/* 两条主张 */}
        <div className="grid gap-4 md:grid-cols-2">
          <ClaimCard claim={claimA} index={1} title={paperTitleById[claimA.paper_id]} />
          <ClaimCard claim={claimB} index={2} title={paperTitleById[claimB.paper_id]} />
        </div>

        {/* 判定理由 */}
        <div className="mt-4 rounded-xl border border-white/10 bg-white/5 p-4">
          <div className="mb-1 text-xs uppercase tracking-wider text-indigo-300">
            判定理由
          </div>
          <div className="text-sm text-slate-200">{rel.reason}</div>
        </div>
      </div>
    </div>
  );
}

function ClaimCard({
  claim,
  index,
  title,
}: {
  claim: Claim;
  index: number;
  title?: string;
}) {
  const dirColor =
    claim.direction === "positive"
      ? "border-emerald-400/40 text-emerald-300"
      : claim.direction === "negative"
      ? "border-rose-400/40 text-rose-300"
      : "border-slate-400/40 text-slate-300";

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-xs text-slate-500">CLAIM #{index}</span>
        <span className={`rounded border px-2 py-0.5 text-xs ${dirColor}`}>
          {claim.direction}
        </span>
      </div>
      {title && (
        <div className="mb-2 line-clamp-2 text-xs text-slate-400">📄 {title}</div>
      )}
      <div className="text-sm font-medium text-slate-200">{claim.subject}</div>
      <div className="text-xs text-slate-400">{claim.intervention}</div>
      <div className="mt-2 text-sm text-slate-200">{claim.conclusion}</div>
    </div>
  );
}

// ============== 工具：HTML 转义（防 XSS）==============
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
