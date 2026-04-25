/**
 * PaperTrace 结果页 (v2)
 * ==========================
 * 顶部栏: [← 返回] [状态胶囊] [研究问题] [元信息] [导出 ▾] [刷新]
 *
 * 模块顺序:
 *   01 · 四项指标 (矛盾对卡片带 2px 琥珀左条)
 *   02 · 观点分布条 (OpinionBar)
 *   03 · 核心矛盾对 (ContradictionCards)
 *   04 · 争论网络图 (DebateNetwork)
 *   05 · AI 智能解读 (综述段落 + 研究方向建议, 合并在同一容器)
 *   06 · 论文列表 (Tab: 全部 / 高争议 / 核心)
 *   07 · 所有主张 (可折叠)
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
  getTaskStatus,
  startAnalysis,
  type Claim,
  type Paper,
  type Relation,
  type ExportFormat,
} from "@/lib/api";
import { useRouter } from "next/navigation";
import LoadingSteps from "@/components/LoadingSteps";
import OpinionBar from "@/components/OpinionBar";
import ContradictionCards from "@/components/ContradictionCards";
import AiReview from "@/components/AiReview";
import RecommendationPanel from "@/components/RecommendationPanel";

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

  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // —— 轮询 ——
  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const s = await getTaskStatus(taskId);
        if (!mounted) return;
        setStatus(s);
        if (s.status === "done") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          const r = await getResult(taskId);
          if (mounted) setResult(r);
        } else if (s.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
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
  const handleExport = async (format: ExportFormat) => {
    setExporting(format);
    try {
      const { blob, filename } = await exportReport(taskId, format);
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
  if (!status) {
    return <SkeletonScreen taskId={taskId} />;
  }
  if (status.status === "failed") {
    return (
      <ErrorScreen
        message={status.error || "任务失败, 请返回首页重试"}
        backHref="/"
      />
    );
  }
  if (!result) {
    return <PendingScreen status={status} />;
  }

  return (
    <div className="min-h-screen bg-bg-base text-text-primary">
      <TopBar
        query={result.query}
        dataFetchedAt={result.data_fetched_at}
        onRefresh={handleRefresh}
        refreshing={refreshing}
        onExport={handleExport}
        exporting={exporting}
      />

      <main className="mx-auto w-full max-w-[1200px] space-y-10 px-6 py-10">
        {/* 模块 01 · 四项指标 */}
        <MetricsGrid result={result} />

        {/* 模块 02 · 观点分布条 */}
        <section className="rounded-lg border border-border bg-bg-surface p-6">
          <OpinionBar claims={result.claims} />
        </section>

        {/* 模块 03 · 核心矛盾对 */}
        <ContradictionCards
          claims={result.claims}
          matrix={result.matrix}
          papers={result.papers}
        />

        {/* 模块 04 · 争论网络图 */}
        <DebateNetwork
          claims={result.claims}
          matrix={result.matrix}
          papers={result.papers}
        />

        {/* 模块 05 · AI 智能解读 (综述 + 研究方向建议) */}
        <AiInsightsSection
          taskId={taskId}
          query={result.query}
          claims={result.claims}
          matrix={result.matrix}
          papers={result.papers}
        />

        {/* 模块 06 · 论文列表 */}
        <PapersList papers={result.papers} claims={result.claims} matrix={result.matrix} />

        {/* 模块 07 · 所有主张 (折叠) */}
        <AllClaims claims={result.claims} papers={result.papers} />

        {/* 关系矩阵 (保留, 放在最底方便答辩) */}
        <section className="rounded-lg border border-border bg-bg-surface">
          <header className="border-b border-border px-6 py-4">
            <h2 className="text-17 font-medium">N × N 关系矩阵</h2>
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
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1200px] items-center justify-between px-6 py-4 text-11 text-text-muted">
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
}: {
  taskId: string;
  query: string;
  claims: Claim[];
  matrix: Relation[][];
  papers: Paper[];
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
        <AiReview taskId={taskId} embedded />
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
        />
      </div>
    </section>
  );
}

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
function AllClaims({ claims, papers }: { claims: Claim[]; papers: Paper[] }) {
  const [open, setOpen] = useState(false);
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

  return (
    <section className="rounded-lg border border-border bg-bg-surface">
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
      {open && (
        <ol className="divide-y divide-border border-t border-border">
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
function PendingScreen({ status }: { status: TaskStatusResponse }) {
  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col items-start justify-center px-6">
      <div className="eyebrow">task {status.task_id.slice(0, 8)}</div>
      <h1 className="mt-2 text-24 font-medium">正在分析 ...</h1>
      <p className="mt-2 text-13 text-text-secondary">{status.progress}</p>
      <div className="mt-8 w-full">
        <LoadingSteps progress={status.progress} />
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
