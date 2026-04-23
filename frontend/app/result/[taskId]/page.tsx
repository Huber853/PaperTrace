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

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  AnalysisResult,
  TaskStatusResponse,
  exportReport,
  getResult,
  getReview,
  getTaskStatus,
  startAnalysis,
} from "@/lib/api";
import { useRouter } from "next/navigation";
import LoadingSteps from "@/components/LoadingSteps";
import DivergenceCard from "@/components/DivergenceCard";
import { ExternalLink } from "lucide-react";

// ECharts 依赖 window，禁用 SSR
const ContradictionMatrix = dynamic(
  () => import("@/components/ContradictionMatrix"),
  { ssr: false }
);
const TimelineEvolution = dynamic(
  () => import("@/components/TimelineEvolution"),
  { ssr: false }
);

interface PageProps {
  params: { taskId: string };
}

export default function ResultPage({ params }: PageProps) {
  const taskId = params.taskId;
  const router = useRouter();

  // 任务状态（轮询返回的）
  const [taskStatus, setTaskStatus] = useState<TaskStatusResponse | null>(null);
  // 完整结果（任务 done 后才取）
  const [result, setResult] = useState<AnalysisResult | null>(null);
  // 出错信息
  const [error, setError] = useState<string | null>(null);
  // "刷新数据"按钮的 loading 状态
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting] = useState(false);

  // 用 ref 存 interval id，避免触发额外渲染
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ===== 综述相关状态（切片 9）=====
  // reviewText: 完整综述文本；reviewDisplayed: 打字机当前显示到的字数对应的子串
  // reviewLoading: 正在调 /api/review；reviewError: 请求失败原因
  const [reviewText, setReviewText] = useState<string | null>(null);
  const [reviewDisplayed, setReviewDisplayed] = useState("");
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  // 打字机定时器 id
  const typewriterRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 给矩阵组件用的 paper_id → title 映射。
  // 必须在所有早 return 之前声明，遵守 React 的 rules-of-hooks
  const paperTitleById = useMemo(() => {
    if (!result) return {};
    const map: Record<number, string> = {};
    for (const p of result.papers) map[p.id] = p.title;
    return map;
  }, [result]);

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

  // ===== 打字机效果（切片 9）=====
  // 每当 reviewText 变化（生成成功），就启动一个 setInterval
  // 一次多吐一个字，直到整段出完就 clearInterval。
  // 用 useEffect 的清理函数确保组件卸载时不会泄漏定时器。
  useEffect(() => {
    if (!reviewText) return;
    setReviewDisplayed(""); // 先清空，避免叠加上一次的残留
    let i = 0;
    typewriterRef.current = setInterval(() => {
      i += 1;
      setReviewDisplayed(reviewText.slice(0, i));
      if (i >= reviewText.length) {
        if (typewriterRef.current) {
          clearInterval(typewriterRef.current);
          typewriterRef.current = null;
        }
      }
    }, 35); // 约每秒 28 字，节奏自然

    return () => {
      if (typewriterRef.current) {
        clearInterval(typewriterRef.current);
        typewriterRef.current = null;
      }
    };
  }, [reviewText]);

  /**
   * "刷新数据"按钮：带 refresh=true 重新提交分析 → 跳到新任务页面。
   * 为什么跳新 taskId 而不是在当前页面重新加载？
   *   因为后端任务是"新建"的——它有自己的 taskId 和全新的状态。
   *   如果留在原页面改 taskId，轮询清理逻辑会变得很复杂；
   *   不如直接跳转，简单又不会有残留状态。
   */
  const handleRefresh = async () => {
    if (!result) return;
    setRefreshing(true);
    try {
      const res = await startAnalysis(result.query, result.papers.length, true);
      router.push(`/result/${res.task_id}`);
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "刷新失败";
      setError(message);
      setRefreshing(false);
    }
  };

  const handleExport = async () => {
    if (!result) return;
    setExporting(true);
    try {
      const { blob, filename } = await exportReport(taskId, "md");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "导出失败";
      setError(message);
    } finally {
      setExporting(false);
    }
  };

  // 点击"生成综述"按钮：调后端 /api/review
  const handleGenerateReview = async () => {
    setReviewLoading(true);
    setReviewError(null);
    setReviewText(null);
    setReviewDisplayed("");
    try {
      const data = await getReview(taskId);
      setReviewText(data.review);
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "未知错误";
      setReviewError(message);
    } finally {
      setReviewLoading(false);
    }
  };

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
  // 用 LoadingSteps 组件替代以前的转圈 spinner，给用户讲一个 4 步的"故事"
  if (!result) {
    return (
      <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white">
        <div className="container mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center px-4 text-center">
          <AnimatedTitle />

          {/* 4 步进度叙事 —— 跟着后端 progress 字段自动推进 */}
          <LoadingSteps progress={taskStatus?.progress} />

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
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white">
      <div className="container mx-auto max-w-5xl px-4 py-10">
        {/* 顶部：query + 返回 + 获取时间 + 刷新按钮 */}
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
            {/* 数据获取时间：把 ISO 时间戳转为用户本地时间展示 */}
            {result.data_fetched_at && (
              <p className="mt-1 text-xs text-slate-500">
                数据获取于{" "}
                {new Date(result.data_fetched_at).toLocaleString("zh-CN", {
                  year: "numeric",
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <button
              type="button"
              onClick={handleExport}
              disabled={exporting}
              className="shrink-0 rounded-lg border border-emerald-400/40 bg-emerald-500/10 px-4 py-2 text-sm font-medium text-emerald-200 transition-all duration-200 hover:-translate-y-0.5 hover:border-emerald-300/60 hover:bg-emerald-500/20 hover:shadow-lg hover:shadow-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
            >
              {exporting ? (
                <span className="flex items-center gap-2">
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-emerald-300/40 border-t-emerald-300" />
                  导出中 ...
                </span>
              ) : (
                "📥 导出报告"
              )}
            </button>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing}
              className="shrink-0 rounded-lg border border-purple-400/40 bg-purple-500/10 px-4 py-2 text-sm font-medium text-purple-200 transition-all duration-200 hover:-translate-y-0.5 hover:border-purple-300/60 hover:bg-purple-500/20 hover:shadow-lg hover:shadow-purple-500/20 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
            >
              {refreshing ? (
                <span className="flex items-center gap-2">
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-purple-300/40 border-t-purple-300" />
                  刷新中 ...
                </span>
              ) : (
                "🔄 刷新数据"
              )}
            </button>
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
                className="rounded-xl border border-white/10 bg-white/5 p-4 backdrop-blur-xl transition-all duration-200 hover:-translate-y-0.5 hover:border-purple-400/50 hover:shadow-xl hover:shadow-purple-500/20"
              >
                {/* 标题 + 外链图标 */}
                <div className="flex items-start gap-1.5">
                  <span className="text-sm font-medium text-white">{p.title}</span>
                  <PaperLink paper={p} />
                </div>
                {/* 来源 + DOI */}
                <div className="mt-1 text-xs text-slate-500">
                  {p.source && <>📄 {p.source === "openalex" ? "OpenAlex" : "arXiv"}</>}
                  {p.doi && <> · DOI: {p.doi}</>}
                </div>
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
                  className="rounded-lg border border-white/10 bg-white/5 p-3 backdrop-blur-xl transition-all duration-200 hover:border-purple-400/50 hover:shadow-lg hover:shadow-purple-500/10"
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

        {/* 分歧指数大卡片 —— 一句话总结整次分析的"撕裂程度" */}
        <DivergenceCard stats={result.stats} />

        {/* 矛盾矩阵热力图 */}
        <section className="mb-10">
          <h2 className="mb-3 text-xl font-semibold text-slate-200">
            矛盾矩阵
          </h2>
          <ContradictionMatrix
            claims={result.claims}
            matrix={result.matrix}
            paperTitleById={paperTitleById}
            papers={result.papers}
          />
        </section>

        {/* 观点演化时间轴 */}
        {result.timeline && result.timeline.length > 0 && (
          <section className="mb-10">
            <h2 className="mb-3 text-xl font-semibold text-slate-200">
              观点演化时间轴
            </h2>
            <TimelineEvolution timeline={result.timeline} />
          </section>
        )}

        {/* ================ 自动综述（切片 9 亮点）================ */}
        <section className="mb-10">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-xl font-semibold text-slate-200">
              自动综述
            </h2>
            <button
              type="button"
              onClick={handleGenerateReview}
              disabled={reviewLoading}
              className="rounded-lg bg-gradient-to-r from-indigo-500 to-purple-500 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all duration-200 hover:-translate-y-0.5 hover:from-indigo-400 hover:to-purple-400 hover:shadow-xl hover:shadow-indigo-500/30 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
            >
              {reviewLoading
                ? "生成中 ..."
                : reviewText
                ? "重新生成"
                : "一键生成综述"}
            </button>
          </div>

          <div className="rounded-xl border border-white/10 bg-white/5 p-6 backdrop-blur-xl transition-all hover:border-purple-400/30">
            {reviewError && (
              <div className="mb-3 rounded-lg border border-red-400/30 bg-red-500/10 p-3 text-sm text-red-200">
                生成失败：{reviewError}
              </div>
            )}

            {!reviewText && !reviewLoading && !reviewError && (
              <p className="text-sm text-slate-400">
                点击右上角按钮，让 AI 根据上面已抽取的主张和矛盾矩阵，自动生成一段
                200-400 字的中文综述段落。引用编号与上方论文列表一一对应。
              </p>
            )}

            {reviewLoading && !reviewText && (
              <div className="flex items-center gap-3 text-sm text-slate-300">
                <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-indigo-400/30 border-t-indigo-400" />
                正在让 DeepSeek 编织段落，预计 10-20 秒 ...
              </div>
            )}

            {reviewText && (
              <div className="prose prose-invert max-w-none">
                {/* 打字机效果：reviewDisplayed 随 setInterval 逐字增长 */}
                <p className="whitespace-pre-wrap text-base leading-relaxed text-slate-100">
                  {reviewDisplayed}
                  {reviewDisplayed.length < reviewText.length && (
                    <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-indigo-300 align-middle" />
                  )}
                </p>
                {reviewDisplayed.length >= reviewText.length && (
                  <div className="mt-3 text-xs text-slate-500">
                    字数（含标点）：{reviewText.length}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

// ============== 子组件 ==============

/** 论文外链图标：按 doi > url > openalex_id 优先级选链接 */
function PaperLink({ paper }: { paper: import("@/lib/api").Paper }) {
  const href = paper.doi
    ? `https://doi.org/${paper.doi}`
    : paper.url
    ? paper.url
    : paper.paper_id
    ? `https://openalex.org/${paper.paper_id}`
    : null;

  if (!href) return null;

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title="查看原文"
      className="shrink-0 text-slate-400 transition-all duration-200 hover:text-purple-400 hover:translate-x-0.5 hover:-translate-y-0.5"
    >
      <ExternalLink size={14} />
    </a>
  );
}

/** 加载中标题："正在分析中" + 跳动省略号（.  ..  ...  循环） */
function AnimatedTitle() {
  const [dots, setDots] = useState("");

  useEffect(() => {
    const id = setInterval(() => {
      setDots((prev) => (prev.length >= 3 ? "" : prev + "."));
    }, 400);
    return () => clearInterval(id);
  }, []);

  return <h2 className="mb-6 text-2xl font-semibold">正在分析中{dots}</h2>;
}

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
      className={`rounded-xl border bg-gradient-to-br p-4 backdrop-blur-xl transition-all duration-200 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-purple-500/10 ${colorMap[color]}`}
    >
      <div className="text-xs uppercase tracking-wider text-slate-400">
        {label}
      </div>
      <div className="mt-1 text-3xl font-bold text-white">{value}</div>
    </div>
  );
}
