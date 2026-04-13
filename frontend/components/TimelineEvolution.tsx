/**
 * PaperTrace - 观点演化时间轴
 * ============================
 *
 * 功能：展示某个研究议题下，支持 / 中立 / 反对三种立场的论文数量
 *       如何随年份变化，用 ECharts 堆叠面积图呈现。
 *
 * 顶部有一个"AI 洞察"提示条：
 *   自动检测"主流立场翻转"——比如 2019 年之前大多数论文支持某观点，
 *   2019 年之后反对声音变成主流——就会高亮提醒。
 *
 * ⚠️ X 轴 Bug 修复记录：
 *   后端返回的 timeline 可能有年份缺口（比如只有 2018、2020、2023），
 *   如果直接拿这些年份当 category，中间会"跳过"缺失年份，
 *   ECharts 不知道中间断了几年，曲线看着像连续的但其实不等距。
 *   解决方案：前端自动从 minYear 到 maxYear 逐年补全，
 *   缺失年份填 0，保证 X 轴是连续等距的真实年份。
 */

"use client";

import { useMemo, useRef, useEffect } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

// ============== 类型定义 ==============

/**
 * 后端返回的时间轴数据点（和 api.ts 中的 TimelinePoint 保持一致）
 * 每个对象代表"某一年"的论文立场统计。
 */
export interface TimelinePoint {
  year: number;      // 年份，如 2021
  positive: number;  // 该年"支持"立场的论文/主张数量
  negative: number;  // "反对"数量
  neutral: number;   // "中立"数量
  total: number;     // 三者之和（后端已算好，前端也会自己算一遍兜底）
}

/** 组件只需要一个 props：后端返回的 timeline 数组 */
interface TimelineEvolutionProps {
  timeline: TimelinePoint[];
}

// ============== 颜色常量 ==============
// 三种立场的主色，用于折线、图例圆点、tooltip 里的小圆点
const C = {
  positive: "#10B981", // 绿色 - 支持
  neutral:  "#94A3B8", // 灰蓝 - 中立
  negative: "#EF4444", // 红色 - 反对
} as const;

// ============== 年份补全工具函数 ==============

/**
 * fillYearGaps - 把后端返回的"稀疏"年份数组补全成"连续"年份数组
 *
 * 举例：
 *   输入 [{ year:2018, ... }, { year:2020, ... }, { year:2023, ... }]
 *   输出 [2018, 2019, 2020, 2021, 2022, 2023] 对应的完整 TimelinePoint[]
 *   其中 2019、2021、2022 这三年的 positive/negative/neutral 都是 0
 *
 * 为什么要这么做？
 *   ECharts 的 category 轴会把 data 数组里的每一项等距排列，
 *   如果我们直接传 ["2018","2020","2023"]，图上看起来三年紧挨着，
 *   用户会以为 2018→2020 和 2020→2023 的间隔一样长——但其实差了 2 年 vs 3 年。
 *   补全后变成 6 个点等距排列，视觉上才是真实的时间尺度。
 */
function fillYearGaps(sparse: TimelinePoint[]): TimelinePoint[] {
  // 数据太少就直接返回，没法补
  if (sparse.length <= 1) return sparse;

  // 第一步：把后端数据按年份存进一个 Map，方便 O(1) 查找
  const byYear = new Map<number, TimelinePoint>();
  for (const tp of sparse) {
    byYear.set(tp.year, tp);
  }

  // 第二步：找出最小年份和最大年份
  const minYear = Math.min(...sparse.map((t) => t.year));
  const maxYear = Math.max(...sparse.map((t) => t.year));

  // 第三步：逐年生成完整数组
  const filled: TimelinePoint[] = [];
  for (let y = minYear; y <= maxYear; y++) {
    if (byYear.has(y)) {
      // 这一年有数据，直接用
      filled.push(byYear.get(y)!);
    } else {
      // 这一年没数据，填一个全 0 的占位
      filled.push({ year: y, positive: 0, negative: 0, neutral: 0, total: 0 });
    }
  }

  return filled;
}

// ============== 观点反转检测 ==============

/** 转折点信息：在哪一年、前后各是什么立场、占比多少 */
interface TurningPoint {
  year: number;
  beforeLabel: string; // 例如 "支持"
  beforePct: number;   // 例如 72（代表 72%）
  afterLabel: string;  // 例如 "反对"
  afterPct: number;
}

/**
 * detectTurningPoints - 检测时间线上是否存在"主流立场翻转"
 *
 * 算法思路（累积窗口法）：
 *   对于每个可能的分割年份 i，把时间线分成"前半段 [0..i)" 和"后半段 [i..n)"，
 *   分别统计两段中支持/反对/中立的总数，看哪种立场占多数。
 *   如果前半段主流是 A、后半段主流变成了 B，说明在第 i 年附近发生了翻转。
 *
 * 最后只返回"变化最显著"的那一个转折点（按前后主流占比之和排序）。
 */
function detectTurningPoints(timeline: TimelinePoint[]): TurningPoint[] {
  // 数据点太少，不足以判断趋势变化
  if (timeline.length < 3) return [];

  const points: TurningPoint[] = [];

  // 方向标签的中英文映射
  const labelMap: Record<string, string> = {
    positive: "支持",
    negative: "反对",
    neutral: "中立",
  };

  // 遍历每个可能的分割位置
  for (let i = 1; i < timeline.length; i++) {
    // ---------- 累积前 i 个年份的三种立场总数 ----------
    let bPos = 0, bNeg = 0, bNeu = 0;
    for (let j = 0; j < i; j++) {
      bPos += timeline[j].positive;
      bNeg += timeline[j].negative;
      bNeu += timeline[j].neutral;
    }
    const bTotal = bPos + bNeg + bNeu;
    if (bTotal === 0) continue; // 前半段全是 0，跳过

    // ---------- 累积后 (n-i) 个年份的三种立场总数 ----------
    let aPos = 0, aNeg = 0, aNeu = 0;
    for (let j = i; j < timeline.length; j++) {
      aPos += timeline[j].positive;
      aNeg += timeline[j].negative;
      aNeu += timeline[j].neutral;
    }
    const aTotal = aPos + aNeg + aNeu;
    if (aTotal === 0) continue; // 后半段全是 0，跳过

    // ---------- 判断前/后各自的"主流立场" ----------
    const beforeDom = bPos >= bNeg && bPos >= bNeu ? "positive"
      : bNeg >= bPos && bNeg >= bNeu ? "negative" : "neutral";
    const afterDom = aPos >= aNeg && aPos >= aNeu ? "positive"
      : aNeg >= aPos && aNeg >= aNeu ? "negative" : "neutral";

    // ---------- 如果前后主流不同，记录为一个转折点 ----------
    if (beforeDom !== afterDom) {
      const beforePct = Math.round(
        (beforeDom === "positive" ? bPos : beforeDom === "negative" ? bNeg : bNeu) / bTotal * 100
      );
      const afterPct = Math.round(
        (afterDom === "positive" ? aPos : afterDom === "negative" ? aNeg : aNeu) / aTotal * 100
      );
      points.push({
        year: timeline[i].year,
        beforeLabel: labelMap[beforeDom],
        beforePct,
        afterLabel: labelMap[afterDom],
        afterPct,
      });
    }
  }

  // 没有检测到任何翻转
  if (points.length === 0) return [];

  // 按"显著程度"排序（前后主流占比之和越大越显著），只取最显著的一个
  points.sort((a, b) => (b.beforePct + b.afterPct) - (a.beforePct + a.afterPct));
  return [points[0]];
}

// ============== 主组件 ==============

export default function TimelineEvolution({
  timeline,
}: TimelineEvolutionProps) {
  /**
   * chartRef 用来拿到 ECharts 实例，目的是在浏览器窗口大小变化时
   * 手动调一下 resize()，让图表自适应新宽度。
   * 用 useRef 而不是 useState，因为 ref 变化不会触发重新渲染。
   */
  const chartRef = useRef<ReactECharts | null>(null);

  // 监听窗口 resize 事件，让 ECharts 图表跟着缩放
  useEffect(() => {
    const handler = () => chartRef.current?.getEchartsInstance?.()?.resize?.();
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);

  // ---------- 年份补全：把稀疏数据变成连续数据 ----------
  const filledTimeline = useMemo(() => fillYearGaps(timeline), [timeline]);

  // ---------- 转折点检测（用补全后的数据，更准确）----------
  const turningPoints = useMemo(
    () => detectTurningPoints(filledTimeline),
    [filledTimeline]
  );

  // ===== 堆叠面积图的 ECharts 配置 =====
  const areaOption: EChartsOption = useMemo(() => {
    // X 轴的类目数组：["2015", "2016", ..., "2024"]
    // 必须是字符串，因为 xAxis.type = "category"
    const years = filledTimeline.map((t) => String(t.year));

    return {
      // 透明背景（由外层 div 的 bg-slate-950/40 提供底色）
      backgroundColor: "transparent",

      // ---------- 鼠标悬停时的提示框 ----------
      tooltip: {
        trigger: "axis", // 整条竖线联动（不是只高亮一个点）
        backgroundColor: "rgba(15, 23, 42, 0.95)",
        borderColor: "rgba(139, 92, 246, 0.4)",
        borderWidth: 1,
        textStyle: { color: "#e2e8f0", fontSize: 12 },
        /**
         * 自定义 tooltip 内容：
         * 显示年份 + 三项具体数量和百分比 + 总计
         */
        formatter: (params: unknown) => {
          const items = params as Array<{
            axisValue: string;
            marker: string;      // ECharts 自动生成的小彩色圆点 HTML
            seriesName: string;  // "支持" / "中立" / "反对"
            value: number;
          }>;
          if (!items.length) return "";

          const year = items[0].axisValue;
          const total = items.reduce((sum, it) => sum + (it.value || 0), 0);

          // 拼 HTML：每行一个立场
          let html = `<div style="font-weight:600;margin-bottom:6px">${year} 年</div>`;
          for (const it of items) {
            const pct = total > 0 ? ((it.value / total) * 100).toFixed(0) : "0";
            html += `<div style="margin:2px 0">${it.marker} ${it.seriesName}：${it.value} 篇（${pct}%）</div>`;
          }
          html += `<div style="margin-top:6px;padding-top:4px;border-top:1px solid rgba(148,163,184,0.2);font-size:11px;color:#94a3b8">共 ${total} 篇</div>`;
          return html;
        },
      },

      // ---------- 图例：放在右上角 ----------
      legend: {
        top: 4,
        right: 12,
        textStyle: { color: "#94a3b8", fontSize: 12 },
        itemWidth: 12,
        itemHeight: 8,
      },

      // ---------- 图表区域留白 ----------
      grid: { top: 40, right: 20, bottom: 30, left: 45 },

      // ---------- X 轴：类目轴，显示年份 ----------
      xAxis: {
        type: "category",   // ← 关键！必须是 category 不能是 value
        data: years,         // ← ["2015","2016",...] 已补全的连续年份
        boundaryGap: false,  // 折线图从最左边开始，不留空白
        axisLabel: { color: "#94A3B8" },
        axisLine: { lineStyle: { color: "rgba(148,163,184,0.3)" } },
      },

      // ---------- Y 轴：数值轴，自动计算范围 ----------
      yAxis: {
        type: "value",
        // minInterval: 1 → 保证刻度是整数（论文数不会有 0.5 篇）
        minInterval: 1,
        axisLabel: { color: "#94A3B8" },
        splitLine: { lineStyle: { color: "rgba(148,163,184,0.15)" } },
      },

      // ---------- 三条堆叠面积线 ----------
      series: [
        {
          name: "支持",
          type: "line",
          stack: "total",       // 三条线共用一个 stack，面积会叠起来
          smooth: true,         // 平滑曲线（贝塞尔插值）
          symbol: "circle",     // 数据点形状
          symbolSize: 6,
          lineStyle: { color: C.positive, width: 2 },
          itemStyle: { color: C.positive },
          areaStyle: {
            // 从上到下的线性渐变：绿色从半透明渐变到几乎全透明
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(16,185,129,0.6)" },
                { offset: 1, color: "rgba(16,185,129,0.05)" },
              ],
            },
          },
          data: filledTimeline.map((t) => t.positive),
        },
        {
          name: "中立",
          type: "line",
          stack: "total",
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { color: C.neutral, width: 2 },
          itemStyle: { color: C.neutral },
          areaStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(148,163,184,0.6)" },
                { offset: 1, color: "rgba(148,163,184,0.05)" },
              ],
            },
          },
          data: filledTimeline.map((t) => t.neutral),
        },
        {
          name: "反对",
          type: "line",
          stack: "total",
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { color: C.negative, width: 2 },
          itemStyle: { color: C.negative },
          areaStyle: {
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(239,68,68,0.6)" },
                { offset: 1, color: "rgba(239,68,68,0.05)" },
              ],
            },
          },
          data: filledTimeline.map((t) => t.negative),
        },
      ],
    };
  }, [filledTimeline]);

  // ===== 空数据兜底 =====
  if (!timeline.length) {
    return (
      <div className="rounded-xl border border-dashed border-white/20 bg-white/5 p-8 text-center text-slate-400">
        没有足够的时间数据来展示演化趋势。
      </div>
    );
  }

  // ===== 正常渲染 =====
  return (
    <div>
      {/* ===== AI 洞察提示条 ===== */}
      {turningPoints.length > 0 ? (
        <div className="mb-3 rounded-xl border border-yellow-400/30 bg-yellow-500/5 px-4 py-3 text-sm text-yellow-200">
          ⚡ 观点转折：{turningPoints[0].year} 年前主流
          <span className="font-semibold">{turningPoints[0].beforeLabel}</span>
          （{turningPoints[0].beforePct}%），此后
          <span className="font-semibold">{turningPoints[0].afterLabel}</span>
          成为主流（{turningPoints[0].afterPct}%）
        </div>
      ) : (
        <div className="mb-3 rounded-xl border border-slate-400/20 bg-slate-500/5 px-4 py-3 text-sm text-slate-400">
          📊 该议题观点随时间演化相对稳定
        </div>
      )}

      {/* ===== 堆叠面积图 ===== */}
      <div className="w-full overflow-hidden rounded-xl border border-white/10 bg-slate-950/40 p-2 backdrop-blur-md">
        <ReactECharts
          ref={chartRef}
          option={areaOption}
          style={{ height: "400px", width: "100%" }}
          opts={{ renderer: "canvas" }}
        />
      </div>
    </div>
  );
}
