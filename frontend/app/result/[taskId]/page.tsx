/**
 * PaperTrace 结果页 (v3 · sidebar-driven)
 * ============================================
 * 顶部栏: [← 返回] [状态胶囊] [研究问题] [元信息] [导出 ▾] [刷新]
 *
 * 改用左侧 sticky 导航 + 单板块视图:
 *   ◇ 概览       (MetricsGrid + OpinionBar)
 *   ✕ 核心矛盾   (ContradictionCards, 带矛盾对数量徽章)
 *   ◉ 矛盾网络   (DebateNetwork, ECharts 力导向)
 *   ✦ AI 智能解读(综述 + 研究方向建议合并)
 *   ▤ 论文列表   (Tab: 全部 / 高争议 / 核心)
 *   ≡ 全部主张   (展开列表, 主张数量徽章)
 *   ▦ 关系矩阵   (N×N 热力图)
 *
 * 桌面端 (md+): 220px 左侧 sticky 栏, 内容区独占右侧
 * 移动端 (< md): 顶部水平横向滚动 chip, 下方内容区
 *
 * 状态: 仅渲染当前活动板块, 切换时其他板块卸载. AiReview 后端已缓存,
 *      重新挂载会自动从缓存秒返回.
 */
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  AgentEvent,
  AgentRun,
  AgentTraceResponse,
  AnalysisResult,
  ReviewResponse,
  cancelAgentRun,
  exportReport,
  getAgentEvents,
  getAgentRun,
  getAgentTrace,
  getResult,
  retryAgentRun,
  startAnalysis,
  submitAgentInput,
  type Claim,
  type Paper,
  type Relation,
  type ExportFormat,
} from "@/lib/api";
import { useRouter } from "next/navigation";
import OpinionBar from "@/components/OpinionBar";
import ContradictionCards from "@/components/ContradictionCards";
import AiReview from "@/components/AiReview";
import RecommendationPanel from "@/components/RecommendationPanel";
import AgentTrace from "@/components/AgentTrace";

// ECharts 依赖 window, 禁用 SSR
const ContradictionMatrix = dynamic(
  () => import("@/components/ContradictionMatrix"),
  { ssr: false }
);
const DebateNetwork = dynamic(
  () => import("@/components/DebateNetwork"),
  { ssr: false }
);

interface PageProps {
  params: { taskId: string };
}

export default function ResultPage({ params }: PageProps) {
  const { taskId } = params;
  const router = useRouter();

  const [agentRun, setAgentRun] = useState<AgentRun | null>(null);
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);
  const [agentTrace, setAgentTrace] = useState<AgentTraceResponse | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const eventSequenceRef = useRef(0);

  // —— 轮询 ——
  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const run = await getAgentRun(taskId);
        if (!mounted) return;
        setAgentRun(run);

        const [newEvents, trace] = await Promise.all([
          getAgentEvents(taskId, eventSequenceRef.current),
          getAgentTrace(taskId),
        ]);
        if (!mounted) return;
        if (newEvents.length > 0) {
          eventSequenceRef.current = newEvents[newEvents.length - 1].sequence;
          setAgentEvents((previous) => [...previous, ...newEvents]);
        }
        setAgentTrace(trace);

        if (run.status === "completed") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          const r = await getResult(taskId);
          if (mounted) setResult(r);
        }
      } catch (e: unknown) {
        if (mounted)
          setFetchError(e instanceof Error ? e.message : "加载失败");
      }
    };
    tick();
    pollRef.current = setInterval(tick, 2000);
    return () => {
      mounted = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [taskId]);

  const handleAgentInput = async (content: string) => {
    const run = await submitAgentInput(taskId, content);
    setAgentRun(run);
  };

  const handleAgentCancel = async () => {
    const run = await cancelAgentRun(taskId);
    setAgentRun(run);
  };

  const handleAgentRetry = async () => {
    const run = await retryAgentRun(taskId);
    setFetchError(null);
    setAgentRun(run);
  };

  // —— 刷新数据: 新任务 + 跳转 ——
  const handleRefresh = async () => {
    if (!result) return;
    setRefreshing(true);
    try {
      const resp = await startAnalysis(result.query, result.papers.length, true);
      router.push(`/result/${resp.task_id}`);
    } catch (e: unknown) {
      setFetchError(e instanceof Error ? e.message : "刷新失败");
      setRefreshing(false);
    }
  };

  // —— 导出 ——
  // extras 由 ResultLayout 注入 (主要是 recommendations, 在子组件 state 里),
  // 这样 markdown 导出时能把"研究方向建议"也写进去
  const handleExport = async (
    format: ExportFormat,
    extras?: Parameters<typeof exportReport>[2],
  ) => {
    setExporting(format);
    try {
      const { blob, filename } = await exportReport(taskId, format, extras);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      setFetchError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(null);
    }
  };

  // —— 渲染 ——
  if (fetchError) {
    return <ErrorScreen message={fetchError} />;
  }
  if (!agentRun) {
    return <SkeletonScreen taskId={taskId} />;
  }
  if (!result) {
    return (
      <PendingScreen
        run={agentRun}
        events={agentEvents}
        trace={agentTrace}
        onSubmitInput={handleAgentInput}
        onCancel={handleAgentCancel}
        onRetry={handleAgentRetry}
      />
    );
  }

  return (
    <ResultLayout
      taskId={taskId}
      result={result}
      onRefresh={handleRefresh}
      refreshing={refreshing}
      onExport={handleExport}
      exporting={exporting}
      agentRun={agentRun}
      agentEvents={agentEvents}
      agentTrace={agentTrace}
    />
  );
}

/* ====================================================== */
/* 结果页主布局: 顶栏 + (左 sidebar | 右内容) + 底栏           */
/* ====================================================== */
type SectionId =
  | "overview"
  | "trace"
  | "contradictions"
  | "network"
  | "ai"
  | "papers"
  | "claims"
  | "matrix";

function ResultLayout({
  taskId,
  result,
  onRefresh,
  refreshing,
  onExport,
  exporting,
  agentRun,
  agentEvents,
  agentTrace,
}: {
  taskId: string;
  result: AnalysisResult;
  onRefresh: () => void;
  refreshing: boolean;
  onExport: (
    fmt: ExportFormat,
    extras?: Parameters<typeof exportReport>[2],
  ) => void;
  exporting: ExportFormat | null;
  agentRun: AgentRun;
  agentEvents: AgentEvent[];
  agentTrace: AgentTraceResponse | null;
}) {
  const [active, setActive] = useState<SectionId>("overview");

  // ============================================================
  // 把 AI 综述与研究方向建议的状态提升到这里 (Bug 1 修复)
  // 子组件 AiReview / RecommendationPanel 卸载时数据不丢失,
  // 切回 ai 板块仍能完整看到之前生成的内容.
  // ============================================================
  const [review, setReview] = useState<ReviewResponse | null>(() =>
    result.review
      ? {
          task_id: taskId,
          review: result.review,
          cached: true,
          elapsed_ms: result.review_meta?.elapsed_ms ?? 0,
          input_tokens: result.review_meta?.input_tokens ?? 0,
          output_tokens: result.review_meta?.output_tokens ?? 0,
          model: result.review_meta?.model ?? "",
        }
      : null
  );
  const [recommendations, setRecommendations] = useState<{
    questions: Array<{
      title: string;
      desc: string;
      sources: string[];
      priority: "high" | "medium" | "low";
    }>;
    methods: Array<{
      title: string;
      desc: string;
      sources: string[];
      priority: "high" | "medium" | "low";
    }>;
  } | null>(result.recommendations ?? null);
  const [recommendMeta, setRecommendMeta] = useState<{
    ms: number;
    model: string;
  } | null>(
    result.recommendations?.meta
      ? {
          ms: result.recommendations.meta.elapsed_ms ?? 0,
          model: result.recommendations.meta.model ?? "",
        }
      : null
  );

  // 包一层导出, 自动把当前 recommendations 注入请求体 (Bug 2 修复)
  const handleExport = (fmt: ExportFormat) => {
    onExport(fmt, { recommendations });
  };

  // 计算每个 section 的徽章数 (只算高置信度矛盾)
  const counts = useMemo(() => {
    let contradictPairs = 0;
    const n = result.claims.length;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const cell = result.matrix?.[i]?.[j];
        if (cell?.relation === "contradict" && cell.confidence >= 0.5) {
          contradictPairs++;
        }
      }
    }
    return {
      contradictions: contradictPairs,
      papers: result.papers.length,
      claims: result.claims.length,
    };
  }, [result]);

  // 切换板块时滚动到内容顶部
  const onChangeSection = (id: SectionId) => {
    setActive(id);
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  return (
    <div className="min-h-screen bg-bg-base text-text-primary">
      <TopBar
        query={result.query}
        dataFetchedAt={result.data_fetched_at}
        onRefresh={onRefresh}
        refreshing={refreshing}
        onExport={handleExport}
        exporting={exporting}
      />

      <div className="mx-auto w-full max-w-[1400px] px-4 py-6 md:px-6 md:py-8">
        <div className="flex flex-col gap-6 md:flex-row md:items-start md:gap-8">
          {/* 左侧导航 */}
          <Sidebar active={active} onChange={onChangeSection} counts={counts} />

          {/* 右侧内容区 */}
          <main className="min-w-0 flex-1">
            {active === "overview" && <OverviewSection result={result} />}

            {active === "trace" && (
              <AgentTrace run={agentRun} events={agentEvents} trace={agentTrace} />
            )}

            {active === "contradictions" && (
              <ContradictionCards
                claims={result.claims}
                matrix={result.matrix}
                papers={result.papers}
              />
            )}

            {active === "network" && (
              <DebateNetwork
                claims={result.claims}
                matrix={result.matrix}
                papers={result.papers}
              />
            )}

            {/* AI 板块: 始终保持挂载, 但仅在 active==="ai" 时显示.
             *  这样切走再切回, AiReview / RecommendationPanel 的内部 loading
             *  / error / tab 状态都不会被重置. 数据本身已经提升到
             *  ResultLayout 的 review / recommendations state, 即便组件被
             *  卸载也不会丢. */}
            <div className={active === "ai" ? "" : "hidden"} aria-hidden={active !== "ai"}>
              <AiInsightsSection
                taskId={taskId}
                query={result.query}
                claims={result.claims}
                matrix={result.matrix}
                papers={result.papers}
                review={review}
                onReviewLoaded={setReview}
                recommendations={recommendations}
                recommendMeta={recommendMeta}
                onRecommendLoaded={(data, meta) => {
                  setRecommendations(data);
                  setRecommendMeta(meta);
                }}
              />
            </div>

            {active === "papers" && (
              <PapersList
                papers={result.papers}
                claims={result.claims}
                matrix={result.matrix}
              />
            )}

            {active === "claims" && (
              <AllClaims claims={result.claims} papers={result.papers} forceOpen />
            )}

            {active === "matrix" && (
              <section className="rounded-lg border border-border bg-bg-surface">
                <header className="border-b border-border px-6 py-4">
                  <h2 className="text-17 font-medium">N × N 关系矩阵</h2>
                  <p className="mt-1 text-11 text-text-muted">
                    每个格子表示一对主张的关系: 红=矛盾、绿=支持、灰=无关; 颜色深度即置信度
                  </p>
                </header>
                <div className="bg-bg-inset">
                  <ContradictionMatrix
                    claims={result.claims}
                    matrix={result.matrix}
                    paperTitleById={Object.fromEntries(
                      result.papers.map((p) => [p.id, p.title])
                    )}
                    papers={result.papers}
                  />
                </div>
              </section>
            )}
          </main>
        </div>
      </div>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between px-6 py-4 text-11 text-text-muted">
          <Link href="/" className="hover:text-text-primary">
            ← 返回首页
          </Link>
          <span className="num">PaperTrace · task {taskId.slice(0, 8)}</span>
        </div>
      </footer>
    </div>
  );
}

/* ====================================================== */
/* 左侧 sidebar 导航                                         */
/* ====================================================== */
const SECTIONS: ReadonlyArray<{
  id: SectionId;
  label: string;
  icon: string;
  color: string;
  badgeKey?: "contradictions" | "papers" | "claims";
}> = [
  { id: "overview",       label: "概览",        icon: "◇", color: "#B4AEFF" },
  { id: "trace",          label: "执行轨迹",    icon: "↻", color: "#7DD3FC" },
  { id: "contradictions", label: "核心矛盾",    icon: "✕", color: "#FFB547", badgeKey: "contradictions" },
  { id: "network",        label: "矛盾网络",    icon: "◉", color: "#FFB547" },
  { id: "ai",             label: "AI 智能解读", icon: "✦", color: "#4ADE80" },
  { id: "papers",         label: "论文列表",    icon: "▤", color: "#7DD3FC", badgeKey: "papers" },
  { id: "claims",         label: "全部主张",    icon: "≡", color: "#A5A9C4", badgeKey: "claims" },
  { id: "matrix",         label: "关系矩阵",    icon: "▦", color: "#B4AEFF" },
] as const;

function Sidebar({
  active,
  onChange,
  counts,
}: {
  active: SectionId;
  onChange: (id: SectionId) => void;
  counts: { contradictions: number; papers: number; claims: number };
}) {
  return (
    <aside className="w-full md:w-[220px] md:shrink-0">
      <nav className="rounded-lg border border-border bg-bg-surface/60 backdrop-blur md:sticky md:top-[78px]">
        <div className="hidden border-b border-border px-4 py-3 md:block">
          <div className="eyebrow">navigate</div>
          <div className="mt-1 text-12 text-text-secondary">板块导航</div>
        </div>
        {/* 移动端: 横向滚动 chip; 桌面端: 纵向列表 */}
        <ul className="flex gap-1 overflow-x-auto p-2 md:flex-col md:gap-0 md:p-2">
          {SECTIONS.map((s) => {
            const isActive = active === s.id;
            const badge = s.badgeKey ? counts[s.badgeKey] : null;
            return (
              <li key={s.id} className="md:w-full">
                <button
                  type="button"
                  onClick={() => onChange(s.id)}
                  aria-current={isActive ? "page" : undefined}
                  className="group relative flex w-full items-center gap-2.5 whitespace-nowrap rounded-sm px-3 py-2 text-13 transition-colors"
                  style={{
                    background: isActive ? "rgba(180,174,255,0.10)" : "transparent",
                    color: isActive ? "#E8E9F3" : "#A5A9C4",
                  }}
                  onMouseEnter={(e) => {
                    if (!isActive) {
                      e.currentTarget.style.background = "rgba(180,174,255,0.05)";
                      e.currentTarget.style.color = "#E8E9F3";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isActive) {
                      e.currentTarget.style.background = "transparent";
                      e.currentTarget.style.color = "#A5A9C4";
                    }
                  }}
                >
                  {/* 左侧 2px accent 色条 (仅桌面端激活态) */}
                  {isActive && (
                    <span
                      aria-hidden
                      className="absolute left-0 top-1 hidden h-[calc(100%-8px)] w-[2px] rounded-sm md:block"
                      style={{ background: s.color }}
                    />
                  )}
                  <span
                    aria-hidden
                    className="text-14 leading-none"
                    style={{ color: s.color }}
                  >
                    {s.icon}
                  </span>
                  <span className="flex-1 text-left">{s.label}</span>
                  {badge !== null && badge > 0 && (
                    <span
                      className="num rounded-sm px-1.5 py-[1px] text-10"
                      style={{
                        background: isActive
                          ? `${s.color}22`
                          : "rgba(255,255,255,0.04)",
                        color: isActive ? s.color : "#6B6F8A",
                      }}
                    >
                      {badge}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}

/* ====================================================== */
/* 概览板块: MetricsGrid + OpinionBar 合并成一屏              */
/* ====================================================== */
function OverviewSection({ result }: { result: AnalysisResult }) {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-22 font-medium text-text-primary md:text-24">
          研究问题概览
        </h1>
        <p className="mt-1 text-12 text-text-muted">
          四项关键指标 + 主张方向分布,快速把握领域共识与分歧的整体格局
        </p>
      </header>
      <MetricsGrid result={result} />
      <section className="rounded-lg border border-border bg-bg-surface p-6">
        <OpinionBar claims={result.claims} />
      </section>
    </div>
  );
}

/* ====================================================== */
/* 顶部栏                                                    */
/* ====================================================== */
function TopBar({
  query,
  dataFetchedAt,
  onRefresh,
  refreshing,
  onExport,
  exporting,
}: {
  query: string;
  dataFetchedAt: string;
  onRefresh: () => void;
  refreshing: boolean;
  onExport: (fmt: ExportFormat) => void;
  exporting: ExportFormat | null;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="sticky top-0 z-20 border-b border-border bg-bg-base/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-4 px-6 py-3">
        <Link
          href="/"
          className="flex items-center gap-2 text-13 text-text-secondary transition-colors hover:text-text-primary"
        >
          ← 返回
        </Link>

        <span className="inline-flex items-center gap-2 rounded-sm border border-accent-support/40 bg-accent-support/10 px-2.5 py-1 text-11 text-accent-support">
          <span className="h-[6px] w-[6px] rounded-sm bg-accent-support" />
          已完成
        </span>

        <div className="min-w-0 flex-1">
          <div className="truncate text-15 text-text-primary">{query}</div>
          <div className="text-11 text-text-muted">
            数据获取于 {formatDate(dataFetchedAt)}
          </div>
        </div>

        {/* 导出菜单 */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            disabled={!!exporting}
            className="rounded border border-border px-3 py-1.5 text-12 text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary disabled:opacity-60"
          >
            {exporting ? "导出中 ..." : "导出 ▾"}
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 mt-1 w-44 overflow-hidden rounded border border-border bg-bg-surface text-12"
              onMouseLeave={() => setMenuOpen(false)}
            >
              <button
                type="button"
                className="block w-full px-3 py-2 text-left text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
                onClick={() => {
                  setMenuOpen(false);
                  onExport("md");
                }}
              >
                Markdown 报告 (.md)
              </button>
              <button
                type="button"
                className="block w-full border-t border-border px-3 py-2 text-left text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
                onClick={() => {
                  setMenuOpen(false);
                  onExport("bib");
                }}
              >
                BibTeX 文献 (.bib)
              </button>
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="rounded border border-border px-3 py-1.5 text-12 text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary disabled:opacity-60"
        >
          {refreshing ? "刷新中 ..." : "刷新数据"}
        </button>
      </div>
    </div>
  );
}

/* ====================================================== */
/* 指标卡片组                                                 */
/* ====================================================== */
function MetricsGrid({ result }: { result: AnalysisResult }) {
  const { stats } = result;
  const totalPairs = stats.contradict_pairs + stats.support_pairs;
  const divergence = totalPairs > 0 ? stats.contradict_pairs / totalPairs : 0;

  return (
    <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
      <MetricCard label="论文" value={stats.papers_count} unit="篇" />
      <MetricCard label="核心主张" value={stats.claims_count} unit="条" />
      <MetricCard
        label="矛盾对"
        value={stats.contradict_pairs}
        unit="对"
        accent
        secondary={`分歧率 ${Math.round(divergence * 100)}%`}
      />
      <MetricCard
        label="共识对"
        value={stats.support_pairs}
        unit="对"
        tone="support"
      />
    </section>
  );
}

function MetricCard({
  label,
  value,
  unit,
  secondary,
  accent,
  tone,
}: {
  label: string;
  value: number;
  unit?: string;
  secondary?: string;
  accent?: boolean;
  tone?: "support";
}) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-border bg-bg-surface p-5">
      {accent && (
        <span
          aria-hidden
          className="absolute left-0 top-0 h-full w-[2px] bg-accent-conflict"
        />
      )}
      {tone === "support" && (
        <span
          aria-hidden
          className="absolute left-0 top-0 h-full w-[2px] bg-accent-support/60"
        />
      )}
      <div className="eyebrow">{label}</div>
      <div className="mt-3 flex items-baseline gap-1">
        <span className="num text-28 font-medium text-text-primary">
          {value}
        </span>
        {unit && <span className="text-12 text-text-muted">{unit}</span>}
      </div>
      {secondary && (
        <div className="mt-1 text-11 text-text-muted">{secondary}</div>
      )}
    </div>
  );
}

/* ====================================================== */
/* 模块 05 · AI 智能解读 (综述 + 研究方向建议 合并)           */
/* ====================================================== */
function AiInsightsSection({
  taskId,
  query,
  claims,
  matrix,
  papers,
  review,
  onReviewLoaded,
  recommendations,
  recommendMeta,
  onRecommendLoaded,
}: {
  taskId: string;
  query: string;
  claims: Claim[];
  matrix: Relation[][];
  papers: Paper[];
  /** 受控: 综述结果 (由父级 ResultLayout 持有) */
  review: ReviewResponse | null;
  onReviewLoaded: (resp: ReviewResponse) => void;
  /** 受控: 研究方向建议 + 元数据 */
  recommendations:
    | {
        questions: Array<{ title: string; desc: string; sources: string[]; priority: "high" | "medium" | "low" }>;
        methods: Array<{ title: string; desc: string; sources: string[]; priority: "high" | "medium" | "low" }>;
      }
    | null;
  recommendMeta: { ms: number; model: string } | null;
  onRecommendLoaded: (
    data: NonNullable<AiInsightsRecommendations>,
    meta: { ms: number; model: string },
  ) => void;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-bg-surface">
      {/* 共享头部 */}
      <header className="border-b border-border px-6 py-4">
        <h2 className="text-17 font-medium text-text-primary">AI 智能解读</h2>
        <p className="mt-1 text-11 text-text-muted">
          基于本次分析自动生成的综述段落与研究方向建议
        </p>
      </header>

      {/* 上半: 综述段落 */}
      <div className="border-b border-border px-6 py-6">
        <AiReview
          taskId={taskId}
          embedded
          value={review}
          onLoaded={onReviewLoaded}
        />
      </div>

      {/* 下半: 研究方向建议 (淡紫径向光晕作背景层次) */}
      <div
        className="px-6 py-6"
        style={{
          background:
            "radial-gradient(ellipse at 30% 0%, rgba(139,127,255,0.05) 0%, transparent 60%)",
        }}
      >
        <RecommendationPanel
          query={query}
          claims={claims}
          matrix={matrix}
          papers={papers}
          embedded
          value={recommendations}
          meta={recommendMeta}
          onLoaded={onRecommendLoaded}
        />
      </div>
    </section>
  );
}

// 给 AiInsightsSection 抽个共享类型, 便于在父级 / 子级间通用
type AiInsightsRecommendations = {
  questions: Array<{ title: string; desc: string; sources: string[]; priority: "high" | "medium" | "low" }>;
  methods: Array<{ title: string; desc: string; sources: string[]; priority: "high" | "medium" | "low" }>;
} | null;

/* ====================================================== */
/* 模块 06 · 论文列表                                         */
/* ====================================================== */
type PapersTab = "all" | "high-contradict" | "core";

function PapersList({
  papers,
  claims,
  matrix,
}: {
  papers: Paper[];
  claims: Claim[];
  matrix: Relation[][];
}) {
  // 统计每篇论文参与的矛盾数, 为"高争议" tab 排序用
  const contradictByPaper = useMemo(() => {
    const counter = new Map<number, number>();
    const n = claims.length;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const cell = matrix?.[i]?.[j];
        if (!cell || cell.relation !== "contradict") continue;
        if (cell.confidence < 0.5) continue;
        const pi = claims[i].paper_id;
        const pj = claims[j].paper_id;
        counter.set(pi, (counter.get(pi) || 0) + 1);
        counter.set(pj, (counter.get(pj) || 0) + 1);
      }
    }
    return counter;
  }, [claims, matrix]);

  // 核心论文 = 引用数前 30%
  const coreSet = useMemo(() => {
    const sorted = [...papers].sort((a, b) => b.citation_count - a.citation_count);
    const n = Math.max(1, Math.ceil(sorted.length * 0.3));
    return new Set(sorted.slice(0, n).map((p) => p.id));
  }, [papers]);

  const [tab, setTab] = useState<PapersTab>("all");
  const [sort, setSort] = useState<"citation" | "year">("citation");

  const shown = useMemo(() => {
    let list = [...papers];
    if (tab === "high-contradict") {
      list = list.filter((p) => (contradictByPaper.get(p.id) || 0) > 0);
      list.sort(
        (a, b) =>
          (contradictByPaper.get(b.id) || 0) - (contradictByPaper.get(a.id) || 0)
      );
    } else if (tab === "core") {
      list = list.filter((p) => coreSet.has(p.id));
    }
    if (tab === "all" || tab === "core") {
      list.sort((a, b) => {
        if (sort === "citation") return b.citation_count - a.citation_count;
        return (b.year ?? 0) - (a.year ?? 0);
      });
    }
    return list;
  }, [papers, tab, sort, contradictByPaper, coreSet]);

  return (
    <section className="rounded-lg border border-border bg-bg-surface">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-6 py-4">
        <h2 className="text-17 font-medium">论文列表</h2>
        <div className="flex items-center gap-2 text-12">
          <div className="flex items-center rounded border border-border bg-bg-elevated p-1">
            <TabChip current={tab} value="all" onClick={setTab}>
              全部 · {papers.length}
            </TabChip>
            <TabChip current={tab} value="high-contradict" onClick={setTab}>
              高争议
            </TabChip>
            <TabChip current={tab} value="core" onClick={setTab}>
              核心
            </TabChip>
          </div>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as "citation" | "year")}
            className="rounded border border-border bg-bg-elevated px-2 py-1 text-12 text-text-secondary"
          >
            <option value="citation">按引用数</option>
            <option value="year">按年份</option>
          </select>
        </div>
      </header>

      <ul className="divide-y divide-border">
        {shown.length === 0 && (
          <li className="px-6 py-10 text-center text-13 text-text-muted">
            此筛选条件下没有论文。
          </li>
        )}
        {shown.map((p) => {
          const conflictN = contradictByPaper.get(p.id) || 0;
          return (
            <li key={p.id} className="relative px-6 py-4">
              {conflictN > 0 && (
                <span
                  aria-hidden
                  className="absolute left-0 top-0 h-full w-[2px] bg-accent-conflict"
                />
              )}
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="text-14 text-text-primary">{p.title}</div>
                  <div className="mt-1 text-12 text-text-muted">
                    {(p.authors || []).slice(0, 4).join(", ")}
                    {(p.authors?.length || 0) > 4 ? " 等" : ""}
                    {p.year ? <span className="num"> · {p.year}</span> : null}
                    {p.source ? ` · ${p.source}` : ""}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-11 text-text-muted">
                    <span className="num">
                      被引 <span className="text-text-primary">{p.citation_count}</span>
                    </span>
                    {conflictN > 0 && (
                      <span>
                        参与 <span className="num text-accent-conflict">{conflictN}</span> 对矛盾
                      </span>
                    )}
                    {coreSet.has(p.id) && (
                      <span className="text-accent-support">核心论文</span>
                    )}
                  </div>
                </div>

                {paperUrl(p) && (
                  <a
                    href={paperUrl(p)!}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0 rounded border border-border px-2.5 py-1 text-11 text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
                  >
                    打开 ↗
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function TabChip<T extends string>({
  current,
  value,
  onClick,
  children,
}: {
  current: T;
  value: T;
  onClick: (v: T) => void;
  children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      onClick={() => onClick(value)}
      className={`rounded-sm px-3 py-1 text-12 transition-colors ${
        active
          ? "bg-brand/15 text-brand"
          : "text-text-secondary hover:bg-bg-inset hover:text-text-primary"
      }`}
    >
      {children}
    </button>
  );
}

function paperUrl(p: Paper): string | null {
  if (p.doi) return `https://doi.org/${p.doi}`;
  if (p.url) return p.url;
  return null;
}

/* ====================================================== */
/* 模块 07 · 所有主张 (默认折叠)                              */
/* ====================================================== */
function AllClaims({
  claims,
  papers,
  forceOpen = false,
}: {
  claims: Claim[];
  papers: Paper[];
  /** sidebar 单板块视图下置 true, 默认展开且隐藏折叠按钮 */
  forceOpen?: boolean;
}) {
  const [open, setOpen] = useState(forceOpen);
  const paperById = useMemo(() => {
    const m = new Map<number, Paper>();
    papers.forEach((p) => m.set(p.id, p));
    return m;
  }, [papers]);

  const dirColor: Record<string, string> = {
    positive: "text-accent-support",
    negative: "text-accent-conflict",
    neutral: "text-text-secondary",
  };
  const dirLabel: Record<string, string> = {
    positive: "支持",
    negative: "反对",
    neutral: "中立",
  };

  const showList = forceOpen || open;
  return (
    <section className="rounded-lg border border-border bg-bg-surface">
      {forceOpen ? (
        <header className="border-b border-border px-6 py-4">
          <h2 className="text-17 font-medium">
            全部主张 · <span className="num">{claims.length}</span> 条
          </h2>
          <p className="mt-1 text-11 text-text-muted">
            按抽取顺序展示, 颜色标注每条主张的方向 (支持 / 反对 / 中立)
          </p>
        </header>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center justify-between px-6 py-4 text-left transition-colors hover:bg-bg-elevated/30"
        >
          <h2 className="text-17 font-medium">
            全部主张 · <span className="num">{claims.length}</span> 条
          </h2>
          <span className="text-12 text-text-muted">
            {open ? "收起 ▴" : "展开 ▾"}
          </span>
        </button>
      )}
      {showList && (
        <ol className={`divide-y divide-border ${forceOpen ? "" : "border-t border-border"}`}>
          {claims.map((c, i) => {
            const paper = paperById.get(c.paper_id);
            return (
              <li key={c.id} className="px-6 py-4">
                <div className="flex items-baseline gap-3">
                  <span className="num text-12 text-text-muted">#{i + 1}</span>
                  <span
                    className={`text-11 uppercase tracking-label ${dirColor[c.direction] || "text-text-secondary"}`}
                  >
                    {dirLabel[c.direction] || c.direction}
                  </span>
                </div>
                <p className="mt-2 text-14 text-text-primary">{c.conclusion}</p>
                <p className="mt-2 text-12 text-text-muted">
                  主题: {c.subject} · 干预: {c.intervention}
                </p>
                {paper && (
                  <p className="mt-1 text-11 text-text-muted">
                    <span className="italic">{paper.title}</span>
                    {paper.year ? (
                      <span className="num"> · {paper.year}</span>
                    ) : null}
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

/* ====================================================== */
/* Pending / Skeleton / Error                               */
/* ====================================================== */
function PendingScreen({
  run,
  events,
  trace,
  onSubmitInput,
  onCancel,
  onRetry,
}: {
  run: AgentRun;
  events: AgentEvent[];
  trace: AgentTraceResponse | null;
  onSubmitInput: (content: string) => Promise<void>;
  onCancel: () => Promise<void>;
  onRetry: () => Promise<void>;
}) {
  return (
    <div className="min-h-screen bg-bg-base px-4 py-10 text-text-primary md:px-6">
      <div className="mx-auto w-full max-w-5xl">
        <Link href="/" className="text-12 text-text-muted hover:text-text-primary">
          ← 返回首页
        </Link>
        <div className="mb-6 mt-8">
          <div className="eyebrow">task {run.task_id.slice(0, 8)}</div>
          <h1 className="mt-2 text-24 font-medium">
            {run.status === "waiting_input"
              ? "需要补充信息"
              : run.status === "failed"
                ? "分析暂停"
                : run.status === "cancelled"
                  ? "任务已取消"
                  : "正在分析"}
          </h1>
        </div>
        <AgentTrace
          run={run}
          events={events}
          trace={trace}
          onSubmitInput={onSubmitInput}
          onCancel={onCancel}
          onRetry={onRetry}
        />
      </div>
    </div>
  );
}

function SkeletonScreen({ taskId }: { taskId: string }) {
  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col items-start justify-center px-6">
      <div className="eyebrow">task {taskId.slice(0, 8)}</div>
      <h1 className="mt-2 text-24 font-medium">正在连接任务 ...</h1>
      <p className="mt-2 text-13 text-text-secondary">稍等片刻</p>
    </div>
  );
}

function ErrorScreen({
  message,
  backHref = "/",
}: {
  message: string;
  backHref?: string;
}) {
  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col items-start justify-center px-6">
      <div className="eyebrow text-accent-conflict">error</div>
      <h1 className="mt-2 text-24 font-medium">出错了</h1>
      <p className="mt-3 text-13 text-text-secondary">{message}</p>
      <Link
        href={backHref}
        className="mt-6 rounded border border-border px-3 py-1.5 text-12 text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
      >
        返回首页
      </Link>
    </div>
  );
}

function formatDate(iso: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
