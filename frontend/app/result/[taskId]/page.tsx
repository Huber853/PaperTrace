/**
 * PaperTrace 结果页
 * ====================
 * URL：/result/{taskId}
 *
 * 工作流程：
 *   1. 进入页面 → 立刻开始轮询 /api/task/{taskId}（每 2 秒一次）
 *   2. 看到 status === "done" → 调 /api/result/{taskId} 拿完整结果 → 停止轮询
 *   3. 看到 status === "failed" → 显示错误 → 停止轮询
 *
 * App Router 的动态路由：
 *   文件路径 app/result/[taskId]/page.tsx 中的 [taskId] 是动态段。
 *   组件会接收到 props.params.taskId（在 client component 里这样取）。
 *
 * 关键 React 知识点：
 *   - useEffect 的清理函数：组件卸载时一定要 clearInterval，不然会内存泄漏
 *   - useRef vs useState：定时器 id 用 ref 存，因为它变化不需要触发重渲染
 */

"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AnalysisResult,
  TaskStatusResponse,
  getResult,
  getTaskStatus,
} from "@/lib/api";

interface PageProps {
  params: { taskId: string };
}

export default function ResultPage({ params }: PageProps) {
  const taskId = params.taskId;

  // 任务状态（轮询返回的）
  const [taskStatus, setTaskStatus] = useState<TaskStatusResponse | null>(null);
  // 完整结果（任务 done 后才取）
  const [result, setResult] = useState<AnalysisResult | null>(null);
  // 出错信息
  const [error, setError] = useState<string | null>(null);

  // 用 ref 存 interval id，避免触发额外渲染
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      try {
        const status = await getTaskStatus(taskId);
        if (cancelled) return;

        setTaskStatus(status);

        if (status.status === "done") {
          // 拉完整结果
          const data = await getResult(taskId);
          if (cancelled) return;
          setResult(data);
          // 停止轮询
          if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
        } else if (status.status === "failed") {
          setError(status.error || "任务失败，原因未知");
          if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
        }
      } catch (e: unknown) {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : "未知错误";
        setError(message);
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      }
    };

    // 立即跑一次（不等 2 秒）
    tick();
    // 然后每 2 秒一次
    intervalRef.current = setInterval(tick, 2000);

    // 清理函数：组件卸载或 taskId 变化时停止轮询
    return () => {
      cancelled = true;
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [taskId]);

  // ===== 渲染分支：错误 =====
  if (error) {
    return (
      <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white">
        <div className="container mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center px-4">
          <div className="w-full rounded-2xl border border-red-400/30 bg-red-500/10 p-8 backdrop-blur-md">
            <h2 className="mb-3 text-2xl font-semibold text-red-200">任务失败</h2>
            <p className="mb-4 text-sm text-red-200/80 break-words">{error}</p>
            <Link
              href="/"
              className="inline-block rounded-lg bg-white/10 px-4 py-2 text-sm hover:bg-white/20"
            >
              ← 返回首页
            </Link>
          </div>
        </div>
      </main>
    );
  }

  // ===== 渲染分支：进行中 =====
  if (!result) {
    return (
      <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white">
        <div className="container mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center px-4 text-center">
          {/* 转圈 loading */}
          <div className="mb-8 h-16 w-16 animate-spin rounded-full border-4 border-indigo-400/30 border-t-indigo-400" />

          <h2 className="mb-2 text-2xl font-semibold">正在分析中</h2>
          <p className="mb-6 text-sm text-slate-400">
            任务 ID：<code className="rounded bg-white/10 px-2 py-0.5 text-xs">{taskId}</code>
          </p>

          {/* 进度文本 */}
          <div className="w-full max-w-md rounded-xl border border-white/10 bg-white/5 p-4 backdrop-blur-md">
            <div className="mb-1 text-xs uppercase tracking-wider text-indigo-300">
              当前阶段
            </div>
            <div className="text-base text-slate-200">
              {taskStatus?.progress || "排队中 ..."}
            </div>
          </div>

          {/* 状态徽章 */}
          {taskStatus && (
            <div className="mt-4 inline-block rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">
              status: {taskStatus.status}
            </div>
          )}
        </div>
      </main>
    );
  }

  // ===== 渲染分支：完成 =====
  // 切片 8 会在这里加 ContradictionMatrix 组件，现在先用基础卡片把数据展示出来
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white">
      <div className="container mx-auto max-w-5xl px-4 py-10">
        {/* 顶部：query + 返回 */}
        <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
          <div>
            <Link
              href="/"
              className="mb-2 inline-block text-sm text-indigo-300 hover:text-indigo-200"
            >
              ← 返回首页
            </Link>
            <h1 className="text-3xl font-bold">分析结果</h1>
            <p className="mt-1 text-slate-400">
              query：<span className="text-slate-200">{result.query}</span>
            </p>
          </div>
        </div>

        {/* 统计卡片 */}
        <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard label="论文" value={result.stats.papers_count} color="indigo" />
          <StatCard label="主张" value={result.stats.claims_count} color="purple" />
          <StatCard label="支持对" value={result.stats.support_pairs} color="emerald" />
          <StatCard label="矛盾对" value={result.stats.contradict_pairs} color="rose" />
        </div>

        {/* 论文列表 */}
        <section className="mb-10">
          <h2 className="mb-3 text-xl font-semibold text-slate-200">论文</h2>
          <div className="space-y-3">
            {result.papers.map((p) => (
              <div
                key={p.id}
                className="rounded-xl border border-white/10 bg-white/5 p-4 backdrop-blur-md"
              >
                <div className="text-sm font-medium text-white">{p.title}</div>
                <div className="mt-1 text-xs text-slate-400">
                  {p.year || "—"} · {p.authors.slice(0, 3).join(", ")}
                  {p.authors.length > 3 ? " et al." : ""} · 引用 {p.citation_count}
                </div>
                <p className="mt-2 line-clamp-3 text-sm text-slate-300">
                  {p.abstract}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* 主张列表 */}
        <section className="mb-10">
          <h2 className="mb-3 text-xl font-semibold text-slate-200">
            抽取出的主张
          </h2>
          <div className="space-y-2">
            {result.claims.map((c) => {
              const dirColor =
                c.direction === "positive"
                  ? "text-emerald-300 bg-emerald-500/10 border-emerald-400/30"
                  : c.direction === "negative"
                  ? "text-rose-300 bg-rose-500/10 border-rose-400/30"
                  : "text-slate-300 bg-slate-500/10 border-slate-400/30";
              return (
                <div
                  key={c.id}
                  className="rounded-lg border border-white/10 bg-white/5 p-3 backdrop-blur-md"
                >
                  <div className="flex items-start gap-2">
                    <span
                      className={`shrink-0 rounded border px-2 py-0.5 text-xs ${dirColor}`}
                    >
                      {c.direction}
                    </span>
                    <div className="flex-1 text-sm">
                      <span className="text-slate-300">{c.subject}</span>
                      <span className="mx-1 text-slate-500">·</span>
                      <span className="text-slate-400">{c.intervention}</span>
                      <div className="mt-0.5 text-slate-200">{c.conclusion}</div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* 矩阵占位符（切片 8 会用 ECharts 替换）*/}
        <section className="mb-10">
          <h2 className="mb-3 text-xl font-semibold text-slate-200">
            矛盾矩阵
          </h2>
          <div className="rounded-xl border border-dashed border-white/20 bg-white/5 p-8 text-center text-slate-400 backdrop-blur-md">
            <p className="text-sm">
              矩阵已构建（{result.matrix.length}×{result.matrix.length}），
              热力图可视化将在切片 8 加入 ECharts 后展示。
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}

// ============== 子组件 ==============

interface StatCardProps {
  label: string;
  value: number;
  color: "indigo" | "purple" | "emerald" | "rose";
}

function StatCard({ label, value, color }: StatCardProps) {
  // Tailwind 不能拼接 class 名（JIT 会丢），所以预先列出所有可能
  const colorMap = {
    indigo: "from-indigo-500/20 to-indigo-500/5 border-indigo-400/30",
    purple: "from-purple-500/20 to-purple-500/5 border-purple-400/30",
    emerald: "from-emerald-500/20 to-emerald-500/5 border-emerald-400/30",
    rose: "from-rose-500/20 to-rose-500/5 border-rose-400/30",
  } as const;

  return (
    <div
      className={`rounded-xl border bg-gradient-to-br p-4 backdrop-blur-md ${colorMap[color]}`}
    >
      <div className="text-xs uppercase tracking-wider text-slate-400">
        {label}
      </div>
      <div className="mt-1 text-3xl font-bold text-white">{value}</div>
    </div>
  );
}
