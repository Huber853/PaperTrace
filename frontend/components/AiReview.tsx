/**
 * AiReview · 模块 6 · AI 综述
 * ---------------------------------
 * - 衬线正文 (Noto Serif SC) 15/28
 * - 小号大写副标题
 * - 段末统计条: 耗时 · token · 模型 · 缓存状态
 * - 支持"点击生成"与"加载中"两种态
 * - 段落中的 [N] 引用保留原样, 仅做视觉弱化
 */
"use client";

import { useEffect, useState } from "react";
import type { ReviewResponse } from "@/lib/api";
import { getReview } from "@/lib/api";

interface Props {
  taskId: string;
  /** 受控值: 由父组件持有, 用于在 sidebar 切换板块后保留生成结果 */
  value?: ReviewResponse | null;
  /** 父组件在综述回来后会被通知, 用来更新受控值或做其它派生 */
  onLoaded?: (resp: ReviewResponse) => void;
  /** 是否自动触发生成 (默认需要用户点击) */
  autoGenerate?: boolean;
  /** 嵌入模式:不渲染外层 section / 边框, 由父级提供容器 */
  embedded?: boolean;
}

export default function AiReview({
  taskId,
  value,
  onLoaded,
  autoGenerate = false,
  embedded = false,
}: Props) {
  // 内部 state 仅作为 uncontrolled 模式 fallback;
  // 一旦父级传了 value, data 永远以 value 为准
  const [internal, setInternal] = useState<ReviewResponse | null>(null);
  const data = value !== undefined ? value : internal;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getReview(taskId);
      setInternal(resp);
      onLoaded?.(resp);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (autoGenerate && !data && !loading) {
      const timer = window.setTimeout(() => void generate(), 0);
      return () => window.clearTimeout(timer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoGenerate, taskId]);

  // 内层主体: 与 embedded 无关的内容
  const inner = (
    <>
      {!data && !loading && !error && <EmptyState onGenerate={generate} />}

      {loading && (
        <div className="flex items-center gap-3 text-13 text-text-secondary">
          <span className="h-[10px] w-[10px] animate-pulse rounded-sm bg-brand" />
          正在调用 DeepSeek 生成综述, 约 15-30 秒 ...
        </div>
      )}

      {error && (
        <div className="rounded border border-accent-conflict/40 bg-accent-conflict/10 p-4 text-13 text-accent-conflict">
          {error}
          <button
            type="button"
            onClick={generate}
            className="ml-3 underline"
          >
            重试
          </button>
        </div>
      )}

      {data && (
        <>
          <ReviewProse text={data.review} />

          {/* 段末统计条 */}
          <div className="mt-6 flex flex-wrap gap-x-6 gap-y-2 border-t border-border pt-4 text-11 text-text-muted">
            <Stat label="生成耗时" value={formatMs(data.elapsed_ms)} />
            <Stat label="输入" value={`${data.input_tokens} tok`} />
            <Stat label="输出" value={`${data.output_tokens} tok`} />
            <Stat label="模型" value={data.model || "—"} />
          </div>
        </>
      )}
    </>
  );

  // 嵌入模式: 只渲染一个轻量小标题 + 内容, 让父级控制外壳
  if (embedded) {
    return (
      <div>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-14 font-medium text-text-primary">综述段落</h3>
          {data && (
            <span className="text-11 text-text-muted">
              {data.cached ? "缓存结果" : "本次生成"}
            </span>
          )}
        </div>
        {inner}
      </div>
    );
  }

  // 独立模式: 完整 section
  return (
    <section className="rounded-lg border border-border bg-bg-surface">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <h2 className="text-17 font-medium text-text-primary">AI 综述段落</h2>
        {data && (
          <span className="text-11 text-text-muted">
            {data.cached ? "缓存结果" : "本次生成"}
          </span>
        )}
      </header>
      <div className="px-6 py-6">{inner}</div>
    </section>
  );
}

function EmptyState({ onGenerate }: { onGenerate: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-13 text-text-secondary">
        综述会根据已识别的矛盾对生成 200-400 字的学术段落, 并在段末标注出耗时与 token 用量。
      </p>
      <button
        type="button"
        onClick={onGenerate}
        className="rounded border border-brand/50 bg-brand/10 px-4 py-2 text-13 text-brand transition-colors hover:bg-brand/20"
      >
        生成综述
      </button>
    </div>
  );
}

/**
 * 段落正文: 把 [N] 引用标记包成视觉弱化的小号上标色块
 */
function ReviewProse({ text }: { text: string }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <p className="prose-serif">
      {parts.map((part, idx) => {
        const m = /^\[(\d+)\]$/.exec(part);
        if (m) {
          return (
            <sup
              key={idx}
              className="mx-[2px] inline-block rounded-sm bg-brand/10 px-[4px] py-[1px] align-super text-[10px] font-medium text-brand"
            >
              {m[1]}
            </sup>
          );
        }
        return <span key={idx}>{part}</span>;
      })}
    </p>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-baseline gap-2">
      <span className="eyebrow !text-[10px]">{label}</span>
      <span className="num text-12 text-text-primary">{value}</span>
    </span>
  );
}

function formatMs(ms: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
