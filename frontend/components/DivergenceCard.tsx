/**
 * DivergenceCard —— 分歧指数大卡片
 * ====================================
 * 一句话总结整次分析的"观点冲突程度"。
 *
 * 公式:
 *   分歧指数 = 矛盾对 / (矛盾对 + 支持对)
 *   - 没有任何关系对时,显示 0
 *   - 只用"有意义的关系对"做分母,unrelated 不计入,
 *     这样指标才反映"凡是有交集的论文,有多大比例在打架"
 *
 * 视觉:
 *   - 72px 渐变大数字 (react-countup 从 0 滚动到目标值)
 *   - 紫色进度条 (width 用百分比)
 *   - 副标题: "X 篇论文 · Y 条主张冲突 · 低/中/高度分歧"
 *
 * 阈值:
 *   < 20%   → 低度分歧 (绿)
 *   20-50%  → 中度分歧 (紫)
 *   ≥ 50%   → 高度分歧 (粉)
 */

"use client";

import CountUp from "react-countup";

interface DivergenceCardProps {
  stats: {
    papers_count: number;
    claims_count: number;
    contradict_pairs: number;
    support_pairs: number;
  };
}

export default function DivergenceCard({ stats }: DivergenceCardProps) {
  const meaningful = stats.contradict_pairs + stats.support_pairs;
  // 防 0 除
  const ratio = meaningful > 0 ? stats.contradict_pairs / meaningful : 0;
  const percent = ratio * 100;

  // 等级判定
  let level: "低" | "中" | "高";
  let levelColor: string;
  if (percent < 20) {
    level = "低";
    levelColor = "text-emerald-300";
  } else if (percent < 50) {
    level = "中";
    levelColor = "text-purple-200";
  } else {
    level = "高";
    levelColor = "text-pink-300";
  }

  return (
    <div className="mb-8 rounded-2xl border border-purple-400/20 bg-gradient-to-br from-purple-500/10 via-indigo-500/5 to-transparent p-6 backdrop-blur-xl transition-all hover:border-purple-400/50 hover:shadow-xl hover:shadow-purple-500/20 md:p-8">
      <div className="mb-2 text-xs uppercase tracking-wider text-purple-300">
        分歧指数
      </div>

      {/* 大数字: 72px = text-7xl */}
      <div className="flex items-baseline gap-2">
        <span className="bg-gradient-to-r from-indigo-300 via-purple-300 to-pink-300 bg-clip-text text-7xl font-bold leading-none tracking-tight text-transparent">
          <CountUp end={percent} duration={1.6} decimals={1} />
        </span>
        <span className="text-2xl font-semibold text-purple-300">%</span>
      </div>

      {/* 紫色进度条 */}
      <div className="mt-5 h-2 w-full overflow-hidden rounded-full bg-white/5">
        <div
          className="h-full rounded-full bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 transition-all duration-1000 ease-out"
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>

      {/* 副标题 */}
      <div className="mt-4 text-sm text-slate-300">
        {stats.papers_count} 篇论文 · {stats.contradict_pairs} 条主张冲突 ·{" "}
        <span className={`font-semibold ${levelColor}`}>{level}度分歧</span>
      </div>
    </div>
  );
}
