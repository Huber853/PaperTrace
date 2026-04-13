/**
 * LoadingSteps —— 4 步进度叙事组件
 * ====================================
 * 替代结果页那个孤零零的转圈 spinner。
 * 用 4 个图标 + 4 行文字告诉用户"我现在在做什么",
 * 比"加载中..."有信息量,也更有质感。
 *
 * 状态判定逻辑:
 *   后端的 progress 字段是一句中文,例如:
 *     "正在搜索论文 ..." / "正在抽取主张 ..." / "正在判定关系 ..." / "正在写入数据库 ..."
 *   我们用关键词匹配把它映射到 0/1/2/3 步。
 *   < currentStep    → ✓ 已完成 (绿色)
 *   == currentStep   → ⏳ 进行中 (紫色 + 心跳动画)
 *   > currentStep    → 灰色等待
 */

"use client";

import { useMemo } from "react";

interface LoadingStepsProps {
  /** 后端 task.progress 字段;可能是 undefined */
  progress?: string;
}

interface Step {
  icon: string;
  label: string;
  /** 命中这些关键词就认为走到这一步了 */
  keywords: string[];
}

const STEPS: Step[] = [
  { icon: "🔍", label: "OpenAlex 检索论文", keywords: ["搜索", "检索", "拉取", "fetch"] },
  { icon: "🧠", label: "DeepSeek 抽取主张", keywords: ["抽取", "主张", "claim"] },
  { icon: "⚖️", label: "判定支持 / 矛盾", keywords: ["判定", "关系", "矛盾", "支持", "relation"] },
  { icon: "📊", label: "构建矛盾矩阵", keywords: ["矩阵", "写入", "matrix", "保存"] },
];

export default function LoadingSteps({ progress }: LoadingStepsProps) {
  // 用 useMemo 避免每次渲染都重新计算当前步
  const currentStep = useMemo(() => {
    if (!progress) return 0;
    // 从后往前找:命中关键词的最靠后的那一步,就是当前步
    for (let i = STEPS.length - 1; i >= 0; i--) {
      if (STEPS[i].keywords.some((kw) => progress.includes(kw))) {
        return i;
      }
    }
    return 0;
  }, [progress]);

  return (
    <div className="w-full max-w-md rounded-2xl border border-white/10 bg-white/5 p-6 backdrop-blur-xl">
      <div className="mb-4 text-xs uppercase tracking-wider text-indigo-300">
        当前阶段
      </div>
      <ul className="space-y-3">
        {STEPS.map((step, i) => {
          const done = i < currentStep;
          const active = i === currentStep;
          // 颜色 / 图标根据状态切换
          const stateClass = done
            ? "text-emerald-300"
            : active
            ? "text-purple-200"
            : "text-slate-500";
          const indicator = done ? "✓" : active ? "⏳" : "·";
          return (
            <li key={i} className={`flex items-center gap-3 text-sm ${stateClass}`}>
              {/* 圆圈状态图标 */}
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs ${
                  done
                    ? "border-emerald-400/40 bg-emerald-500/10"
                    : active
                    ? "border-purple-400/50 bg-purple-500/10 animate-breathe"
                    : "border-white/10 bg-white/5"
                }`}
              >
                {indicator}
              </span>
              {/* 表情 + 文案 */}
              <span className="text-lg">{step.icon}</span>
              <span className="font-medium">{step.label}</span>
            </li>
          );
        })}
      </ul>
      {/* 后端原始 progress 文字,放在底部小字一行,既不抢戏又能给开发者看 */}
      {progress && (
        <div className="mt-4 border-t border-white/5 pt-3 text-xs text-slate-500">
          {progress}
        </div>
      )}
    </div>
  );
}
