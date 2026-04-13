/**
 * PaperTrace - 矛盾矩阵热力图（7 档配色全显示版）
 * =================================================
 *
 * N × N 热力图，每个格子代表两条主张之间的关系判定。
 *
 * 7 档配色（按 confidence 分段）：
 *   强矛盾 → 中矛盾 → 弱矛盾 → 无关(深灰) → 弱支持 → 中支持 → 强支持
 *
 * 特点：
 *   - 所有格子全部渲染，包括"无关"和对角线
 *   - hover 浮层显示两条主张 + AI 判定理由
 *   - 左侧标签：#编号·P论文号
 *   - 底部标签：论文标题（截断）
 *   - 底部静态图例：7 个色块说明
 *
 * 编码规则（把关系映射成数值，给 visualMap 用）：
 *   support  →  +confidence（0 ~ +1）
 *   contradict → -confidence（-1 ~ 0）
 *   unrelated  →  0（灰色）
 *   对角线    →  2（特殊值，单独映射为暗紫色）
 */

"use client";

import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import type { EChartsOption } from "echarts";
import ReactECharts from "echarts-for-react";
import type { Claim, Paper, Relation } from "@/lib/api";
import { ExternalLink } from "lucide-react";

// ============== Props ==============
export interface ContradictionMatrixProps {
  claims: Claim[];
  matrix: Relation[][];
  paperTitleById?: Record<number, string>;
  /** 论文列表，侧栏里显示论文详情和"查看原文"链接 */
  papers?: Paper[];
}

// ============== 侧栏 Drawer 的数据结构 ==============
/** 点击某个格子后，存下来给 Drawer 用的全部信息 */
interface DrawerData {
  claimA: Claim;       // 行对应的主张
  claimB: Claim;       // 列对应的主张
  indexA: number;       // claimA 在 claims 数组中的原始索引
  indexB: number;       // claimB 的原始索引
  cell: Relation;       // 两者的关系判定（relation + confidence + reason）
}

// ============== 7 档配色 ==============
const COLOR = {
  contradict_strong: "#EF4444", // 强矛盾 - 亮红
  contradict_mid:    "#F87171", // 中矛盾
  contradict_weak:   "#FCA5A5", // 弱矛盾
  unrelated:         "#1E293B", // 无关 - 深灰，融入背景
  support_weak:      "#6EE7B7", // 弱支持 - 浅绿
  support_mid:       "#34D399", // 中支持
  support_strong:    "#10B981", // 强支持 - 亮绿
  diagonal:          "#312E81", // 对角线 - 暗紫
} as const;

// ============== 工具函数 ==============

/** HTML 转义（防 XSS，tooltip 里拼 HTML 时必须用） */
function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 生成左侧 Y 轴标签：#编号·P论文号 */
function shortLabel(claim: Claim, index: number): string {
  return `#${index + 1}·P${claim.paper_id}`;
}

/** 生成底部 X 轴标签：论文标题（截断到 8 字） */
function bottomLabel(claim: Claim, paperTitleById: Record<number, string>): string {
  const title = paperTitleById[claim.paper_id] || `P${claim.paper_id}`;
  return title.length > 8 ? title.slice(0, 8) + "…" : title;
}

/** 给论文生成外链 URL（优先级：doi > url > openalex_id） */
function paperUrl(paper?: Paper): string | null {
  if (!paper) return null;
  if (paper.doi) return `https://doi.org/${paper.doi}`;
  if (paper.url) return paper.url;
  if (paper.paper_id) return `https://openalex.org/${paper.paper_id}`;
  return null;
}

// ============== 主组件 ==============
export default function ContradictionMatrix({
  claims,
  matrix,
  paperTitleById = {},
  papers = [],
}: ContradictionMatrixProps) {
  const chartRef = useRef<ReactECharts | null>(null);

  // ----- 侧栏状态：null 表示关闭，有值表示打开并显示对应数据 -----
  const [drawer, setDrawer] = useState<DrawerData | null>(null);

  // paper.id → Paper 的映射，方便 O(1) 查找
  const paperById = useMemo(() => {
    const m: Record<number, Paper> = {};
    for (const p of papers) m[p.id] = p;
    return m;
  }, [papers]);
  const n = claims.length;
  const isEmpty = n === 0 || matrix.length === 0;

  // ----- Y 轴标签（左侧）：#1·P3 格式 -----
  const yLabels = useMemo(
    () => claims.map((c, i) => shortLabel(c, i)),
    [claims]
  );

  // ----- X 轴标签（底部）：论文标题截断 -----
  const xLabels = useMemo(
    () => claims.map((c) => bottomLabel(c, paperTitleById)),
    [claims, paperTitleById]
  );

  // ----- 展平矩阵为 ECharts heatmap data -----
  // 每个格子编码为 [x, y, val]：
  //   对角线 → val = 2（特殊标记）
  //   contradict → val = -confidence（-1 ~ 0）
  //   support → val = +confidence（0 ~ +1）
  //   unrelated → val = 0
  const chartData = useMemo(() => {
    const points: [number, number, number][] = [];
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if (i === j) {
          // 对角线：自己 vs 自己，用特殊值 2
          points.push([j, i, 2]);
          continue;
        }
        const cell = matrix[i]?.[j];
        if (!cell) {
          points.push([j, i, 0]);
          continue;
        }
        let val = 0;
        if (cell.relation === "contradict") val = -cell.confidence;
        else if (cell.relation === "support") val = cell.confidence;
        points.push([j, i, val]);
      }
    }
    return points;
  }, [matrix, n]);

  // ----- ECharts 配置 -----
  const option: EChartsOption = useMemo(
    () => ({
      backgroundColor: "transparent",

      // tooltip：hover 浮层
      tooltip: {
        position: "top",
        backgroundColor: "rgba(15, 23, 42, 0.95)",
        borderColor: "rgba(139, 92, 246, 0.4)",
        borderWidth: 1,
        textStyle: { color: "#e2e8f0", fontSize: 12 },
        extraCssText: "max-width: 400px; white-space: normal;",
        formatter: (params: unknown) => {
          // 防御：tooltip 可能触发在坐标轴、空白区域等非数据点位置，
          // 此时 params.value 是 undefined，解构会报 TypeError。
          const p = params as { value?: unknown };
          if (!Array.isArray(p.value) || p.value.length < 3) return "";
          const [x, y, val] = p.value as [number, number, number];

          // 对角线不显示 tooltip
          if (val === 2) return "";

          const claimA = claims[y];
          const claimB = claims[x];
          const cell = matrix[y]?.[x];
          if (!claimA || !claimB || !cell) return "";

          // 关系名称和颜色
          const relLabel =
            cell.relation === "contradict" ? "矛盾"
            : cell.relation === "support" ? "支持"
            : "无关";
          const relColor =
            cell.relation === "contradict" ? "#FCA5A5"
            : cell.relation === "support" ? "#6EE7B7"
            : "#94a3b8";

          // 论文标题
          const titleA = paperTitleById[claimA.paper_id] || `论文 P${claimA.paper_id}`;
          const titleB = paperTitleById[claimB.paper_id] || `论文 P${claimB.paper_id}`;

          return `
            <div style="font-weight:600;color:${relColor};margin-bottom:6px;font-size:13px;">
              ${relLabel} · 置信度 ${(cell.confidence * 100).toFixed(0)}%
            </div>
            <div style="margin-bottom:8px;">
              <div style="font-size:10px;color:#64748b;margin-bottom:2px;">
                #${y + 1} · ${esc(titleA.slice(0, 40))}${titleA.length > 40 ? "…" : ""}
              </div>
              <div style="font-size:11px;color:#cbd5e1;">
                ${esc(claimA.conclusion.slice(0, 80))}${claimA.conclusion.length > 80 ? "…" : ""}
              </div>
            </div>
            <div style="margin-bottom:8px;">
              <div style="font-size:10px;color:#64748b;margin-bottom:2px;">
                #${x + 1} · ${esc(titleB.slice(0, 40))}${titleB.length > 40 ? "…" : ""}
              </div>
              <div style="font-size:11px;color:#cbd5e1;">
                ${esc(claimB.conclusion.slice(0, 80))}${claimB.conclusion.length > 80 ? "…" : ""}
              </div>
            </div>
            <div style="border-top:1px solid rgba(148,163,184,0.2);padding-top:6px;font-size:11px;color:#94a3b8;">
              💡 ${esc(cell.reason.slice(0, 120))}${cell.reason.length > 120 ? "…" : ""}
            </div>
          `;
        },
      },

      grid: {
        top: 10,
        right: 10,
        bottom: 80,  // 给底部 X 轴标签留空间
        left: 10,
        containLabel: true,
      },

      // X 轴（底部）：论文标题
      xAxis: {
        type: "category",
        data: xLabels,
        splitArea: { show: false },
        axisLabel: {
          color: "#64748b",
          fontSize: 10,
          rotate: 45,
          overflow: "truncate",
          width: 60,
        },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: "#334155" } },
      },

      // Y 轴（左侧）：#编号·P论文号
      yAxis: {
        type: "category",
        data: yLabels,
        inverse: true,
        splitArea: { show: false },
        axisLabel: {
          color: "#94a3b8",
          fontSize: 11,
        },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: "#334155" } },
      },

      // 7 档颜色映射（按数值分段）
      visualMap: {
        show: false,
        type: "piecewise",
        pieces: [
          // 对角线（特殊值 2）
          { gte: 1.5, lte: 2.5, color: COLOR.diagonal },
          // 支持（3 档，按 confidence 分）
          { gte: 0.7,  lt: 1.5,  color: COLOR.support_strong },
          { gte: 0.4,  lt: 0.7,  color: COLOR.support_mid },
          { gte: 0.05, lt: 0.4,  color: COLOR.support_weak },
          // 无关（编码 0 附近）
          { gte: -0.05, lt: 0.05, color: COLOR.unrelated },
          // 矛盾（3 档，confidence 越高值越负）
          { gte: -0.4, lt: -0.05, color: COLOR.contradict_weak },
          { gte: -0.7, lt: -0.4,  color: COLOR.contradict_mid },
          { gte: -1.1, lt: -0.7,  color: COLOR.contradict_strong },
        ],
      },

      series: [
        {
          name: "Relation",
          type: "heatmap",
          data: chartData,
          label: { show: false },
          itemStyle: {
            borderColor: "rgba(255,255,255,0.04)",
            borderWidth: 0.5,
          },
          emphasis: {
            itemStyle: {
              borderColor: "#a78bfa",
              borderWidth: 2,
              shadowBlur: 10,
              shadowColor: "rgba(139, 92, 246, 0.5)",
            },
          },
          progressive: 1000,
        },
      ],

      // 滚轮缩放（矩阵大时方便查看局部）
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "inside", yAxisIndex: 0 },
      ],
    }),
    [chartData, xLabels, yLabels, claims, matrix, paperTitleById]
  );

  // ----- 点击格子 → 打开侧栏 -----
  const onChartClick = useCallback(
    (params: unknown) => {
      // 防御：点击坐标轴、图例等非数据区域时 value 不存在
      const p = params as { value?: unknown };
      if (!Array.isArray(p.value) || p.value.length < 3) return;
      const [x, y, val] = p.value as [number, number, number];

      // 对角线和无关格子不弹侧栏
      if (val === 2) return;

      const claimA = claims[y];
      const claimB = claims[x];
      const cell = matrix[y]?.[x];
      if (!claimA || !claimB || !cell) return;

      setDrawer({ claimA, claimB, indexA: y, indexB: x, cell });
    },
    [claims, matrix]
  );

  const onEvents = useMemo(
    () => ({ click: onChartClick }),
    [onChartClick]
  );

  // ESC 关闭侧栏
  useEffect(() => {
    if (!drawer) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawer(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [drawer]);

  // 窗口 resize 时让图表自适应
  useEffect(() => {
    const handler = () => chartRef.current?.getEchartsInstance?.()?.resize?.();
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
    <div>
      {/* 图表 */}
      <div className="aspect-square w-full overflow-hidden rounded-xl border border-white/10 bg-slate-950/40 p-2 backdrop-blur-md">
        <ReactECharts
          ref={chartRef}
          option={option}
          style={{ height: "100%", width: "100%" }}
          onEvents={onEvents}
          opts={{ renderer: "canvas" }}
        />
      </div>

      {/* 提示文字 */}
      <p className="mt-1 text-xs text-slate-500">
        提示：hover 查看摘要 · 点击格子查看完整详情 · 滚轮缩放
      </p>

      {/* 底部静态图例：7 档色块 */}
      <div className="mt-3 flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5">
        <LegendItem color={COLOR.contradict_strong} label="强矛盾" />
        <LegendItem color={COLOR.contradict_mid}    label="中矛盾" />
        <LegendItem color={COLOR.contradict_weak}   label="弱矛盾" />
        <LegendItem color={COLOR.unrelated}          label="无关" border />
        <LegendItem color={COLOR.support_weak}       label="弱支持" />
        <LegendItem color={COLOR.support_mid}        label="中支持" />
        <LegendItem color={COLOR.support_strong}     label="强支持" />
      </div>

      {/* ===== 右侧滑出 Drawer ===== */}
      {drawer && (
        <DetailDrawer
          data={drawer}
          paperTitleById={paperTitleById}
          paperById={paperById}
          onClose={() => setDrawer(null)}
        />
      )}
    </div>
  );
}

// ============== 子组件：图例色块 ==============
function LegendItem({
  color,
  label,
  border = false,
}: {
  color: string;
  label: string;
  border?: boolean;
}) {
  return (
    <div className="flex items-center gap-1.5 text-xs text-slate-400">
      <span
        className={`inline-block h-3 w-3 rounded-sm ${border ? "border border-white/20" : ""}`}
        style={{ backgroundColor: color }}
      />
      {label}
    </div>
  );
}

// ============== 子组件：详情侧栏 Drawer ==============
function DetailDrawer({
  data,
  paperTitleById,
  paperById,
  onClose,
}: {
  data: DrawerData;
  paperTitleById: Record<number, string>;
  paperById: Record<number, Paper>;
  onClose: () => void;
}) {
  const { claimA, claimB, indexA, indexB, cell } = data;

  // 关系徽章的配色
  const badgeStyle =
    cell.relation === "contradict"
      ? "border-red-400/40 bg-red-500/10 text-red-200"
      : cell.relation === "support"
      ? "border-emerald-400/40 bg-emerald-500/10 text-emerald-200"
      : "border-slate-400/40 bg-slate-500/10 text-slate-200";

  const relLabel =
    cell.relation === "contradict" ? "矛盾"
    : cell.relation === "support" ? "支持"
    : "无关";

  return (
    <>
      {/* 半透明遮罩：点击关闭 */}
      <div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* 右侧面板：400px 宽，从右侧滑入 */}
      <div className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[400px] flex-col border-l border-white/10 bg-slate-900 shadow-2xl animate-in slide-in-from-right duration-200">
        {/* 头部：关系徽章 + 关闭按钮 */}
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
          <span className={`rounded-full border px-3 py-1 text-sm font-medium ${badgeStyle}`}>
            {relLabel} · {(cell.confidence * 100).toFixed(0)}%
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-white/10 hover:text-white"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {/* 可滚动内容区 */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* 主张 A 卡片 */}
          <ClaimCard
            label={`主张 #${indexA + 1}`}
            claim={claimA}
            paper={paperById[claimA.paper_id]}
            paperTitle={paperTitleById[claimA.paper_id]}
          />

          {/* VS 分隔线 */}
          <div className="flex items-center gap-3">
            <div className="flex-1 border-t border-white/10" />
            <span className="text-xs font-medium text-slate-500">VS</span>
            <div className="flex-1 border-t border-white/10" />
          </div>

          {/* 主张 B 卡片 */}
          <ClaimCard
            label={`主张 #${indexB + 1}`}
            claim={claimB}
            paper={paperById[claimB.paper_id]}
            paperTitle={paperTitleById[claimB.paper_id]}
          />

          {/* AI 判定理由 */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-4">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-indigo-300">
              AI 判定理由
            </div>
            <p className="text-sm leading-relaxed text-slate-300">
              {cell.reason}
            </p>
          </div>
        </div>
      </div>
    </>
  );
}

// ============== 子组件：Drawer 内的主张卡片 ==============
function ClaimCard({
  label,
  claim,
  paper,
  paperTitle,
}: {
  label: string;
  claim: Claim;
  paper?: Paper;
  paperTitle?: string;
}) {
  const url = paperUrl(paper);

  // 主张方向的配色
  const dirColor =
    claim.direction === "positive"
      ? "border-emerald-400/40 text-emerald-300"
      : claim.direction === "negative"
      ? "border-red-400/40 text-red-300"
      : "border-slate-400/40 text-slate-300";

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-4">
      {/* 标题行：标签 + 方向徽章 */}
      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs font-semibold text-slate-400">{label}</span>
        <span className={`rounded border px-2 py-0.5 text-xs ${dirColor}`}>
          {claim.direction}
        </span>
      </div>

      {/* 论文信息：标题 + 年份 + 作者 + 查看原文 */}
      {paper && (
        <div className="mb-3 space-y-1">
          <div className="flex items-start gap-1.5">
            <span className="text-xs leading-relaxed text-slate-300">
              📄 {paperTitle || paper.title}
            </span>
            {url && (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                title="查看原文"
                className="shrink-0 text-slate-400 transition-colors hover:text-purple-400"
              >
                <ExternalLink size={12} />
              </a>
            )}
          </div>
          <div className="text-xs text-slate-500">
            {paper.year || "—"} · {paper.authors?.slice(0, 3).join(", ")}
            {(paper.authors?.length || 0) > 3 ? " et al." : ""}
            {paper.citation_count > 0 && ` · 引用 ${paper.citation_count}`}
          </div>
        </div>
      )}

      {/* 主张内容 */}
      <div className="text-sm font-medium text-slate-200">{claim.subject}</div>
      <div className="mt-0.5 text-xs text-slate-400">{claim.intervention}</div>
      <div className="mt-2 text-sm leading-relaxed text-slate-200">{claim.conclusion}</div>
    </div>
  );
}
