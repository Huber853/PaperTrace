/**
 * PaperTrace 首页 (v3 · luxurious)
 * ==================================
 * 重构要点:
 *   1) Hero 标题用三色立场词 (争论琥珀 / 共识翡翠 / 分歧紫) 体现项目核心
 *   2) 副标题使用 Noto Serif SC, 衬线学术风
 *   3) 关键流程 chip 用 color-coded dot, 不再单色文本
 *   4) 输入卡加 hairline corner + 聚焦时的 brand 渐变光环
 *   5) "工作原理" 四步每步独立 accent 色
 *   6) 新增完整 #about 章节, 修复死链
 *   7) 顶部装饰横线网格 + 双径向光晕保留
 */
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { startAnalysis } from "@/lib/api";

// —— 可快速编辑的常量 —— //
const SUGGESTED_QUERIES = [
  "intermittent fasting health effects",
  "remote work productivity",
  "sleep deprivation cognition",
  "low-carb vs low-fat diet",
] as const;

const DEMO_QUERY = SUGGESTED_QUERIES[0];

// 大致估算: 每篇论文 ≈ 2 条主张, 矩阵规模 ≈ N^2/2 次判定, 每次 ≈ 500 tokens
function estimate(limit: number) {
  const secs = Math.round(limit * 1.2 + (limit * limit) / 10);
  const tokensK = Math.round((limit * 2 + (limit * limit) / 2) * 0.5);
  return { secs, tokensK };
}

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(20);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [focused, setFocused] = useState(false);
  const router = useRouter();

  const { secs, tokensK } = estimate(limit);

  const submit = async (overrideQuery?: string) => {
    const finalQuery = (overrideQuery ?? query).trim();
    if (!finalQuery) {
      setError("请先输入研究问题");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const res = await startAnalysis(finalQuery, limit);
      router.push(`/result/${res.task_id}`);
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "未知错误";
      setError(message);
      setSubmitting(false);
    }
  };

  return (
    <div
      className="relative flex min-h-screen flex-col text-text-primary"
      style={{
        background: `
          radial-gradient(ellipse at 18% 14%, rgba(139,127,255,0.10) 0%, transparent 55%),
          radial-gradient(ellipse at 82% 86%, rgba(255,181,71,0.06) 0%, transparent 55%),
          radial-gradient(ellipse at 50% 0%, rgba(74,222,128,0.04) 0%, transparent 40%),
          #111428
        `,
      }}
    >
      {/* 顶部装饰: 极淡的横线网格, 仅在 Hero 上方 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] opacity-[0.35]"
        style={{
          background:
            "repeating-linear-gradient(0deg, transparent, transparent 39px, rgba(255,255,255,0.025) 39px, rgba(255,255,255,0.025) 40px)",
          maskImage: "linear-gradient(to bottom, black 0%, transparent 100%)",
          WebkitMaskImage: "linear-gradient(to bottom, black 0%, transparent 100%)",
        }}
      />

      {/* ===== 顶部导航 ===== */}
      <nav className="relative z-10 border-b border-border/70">
        <div className="mx-auto flex max-w-[1200px] items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2.5">
            <LogoMark />
            <span className="text-15 font-medium tracking-wide">
              <span className="text-text-primary">Paper</span>
              <span className="text-brand">Trace</span>
            </span>
            <span
              className="ml-2 rounded-sm border border-brand/30 bg-brand/5 px-1.5 py-[1px] text-10 font-medium uppercase tracking-label text-brand"
            >
              v0.1
            </span>
          </div>
          <div className="hidden items-center gap-6 text-13 text-text-secondary md:flex">
            <a href="#how" className="transition-colors hover:text-text-primary">
              工作原理
            </a>
            <a href="#about" className="transition-colors hover:text-text-primary">
              关于
            </a>
            <a
              href="https://github.com"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 transition-colors hover:text-text-primary"
            >
              GitHub
              <span className="text-10 text-text-muted">↗</span>
            </a>
          </div>
        </div>
      </nav>

      {/* ===== 主内容 (整体居中) ===== */}
      <main className="relative z-10 mx-auto flex w-full max-w-[760px] flex-1 flex-col items-center px-6 py-12 md:py-20">
        {/* Hero · 居中 */}
        <section className="w-full text-center">
          {/* 顶部小标签 */}
          <div className="inline-flex items-center gap-2 rounded-full border border-border/80 bg-bg-surface/40 px-3 py-1 text-11 backdrop-blur-sm">
            <span className="h-[6px] w-[6px] rounded-full bg-accent-support shadow-[0_0_8px_rgba(74,222,128,0.6)]" />
            <span className="text-text-secondary">research tool</span>
            <span className="text-text-muted">·</span>
            <span className="num text-text-primary">v0.1</span>
          </div>

          {/* 主标题: 三色立场词 */}
          <h1 className="mt-6 text-28 font-medium tracking-tight md:text-36">
            发现学术论文之间
            <br />
            真正的
            <span className="text-accent-conflict">争论</span>
            <span className="text-text-muted">、</span>
            <span className="text-accent-support">共识</span>
            <span className="text-text-muted"> 与</span>
            <span className="text-brand">分歧</span>
          </h1>

          {/* 副标题: 衬线 + 关键词高亮 */}
          <p className="mx-auto mt-5 max-w-xl font-serif text-15 leading-relaxed text-text-secondary md:text-[16px] md:leading-[28px]">
            输入一个研究问题, 自动检索论文、抽取
            <span className="text-text-primary">核心主张</span>、识别彼此之间的
            <span className="text-accent-conflict">矛盾</span>与
            <span className="text-accent-support">共识</span>,
            并生成可追溯引用的
            <span className="text-brand">综述段落</span>。
          </p>

          {/* 一行流程指示 */}
          <div className="mt-7 flex flex-wrap items-center justify-center gap-x-1 gap-y-2 text-11 text-text-muted">
            <FlowChip color="#B4AEFF" label="检索" />
            <Arrow />
            <FlowChip color="#7DD3FC" label="抽取" />
            <Arrow />
            <FlowChip color="#FFB547" label="判定" />
            <Arrow />
            <FlowChip color="#4ADE80" label="综述" />
          </div>
        </section>

        {/* 输入卡 */}
        <section
          className="relative mt-12 w-full overflow-hidden rounded-lg border bg-bg-surface transition-colors"
          style={{
            borderColor: focused ? "rgba(180,174,255,0.45)" : "rgba(36,40,69,1)",
            boxShadow: focused
              ? "0 0 0 1px rgba(180,174,255,0.2), 0 0 40px -10px rgba(180,174,255,0.25)"
              : "none",
          }}
        >
          {/* 装饰角标: 四个 hairline 角 */}
          <CornerBracket pos="tl" />
          <CornerBracket pos="tr" />
          <CornerBracket pos="bl" />
          <CornerBracket pos="br" />

          <div className="border-b border-border px-6 py-4 text-left">
            <div className="flex items-center gap-2">
              <span className="h-[6px] w-[6px] rounded-sm bg-brand" />
              <div className="eyebrow text-brand">start</div>
            </div>
            <h2 className="mt-2 text-17 font-medium">研究问题</h2>
          </div>

          <div className="px-6 py-6 text-left">
            {/* 输入框 */}
            <label htmlFor="query" className="block text-12 text-text-secondary">
              建议使用<span className="text-text-primary">英文</span>, 论文库以英文文献为主
            </label>
            <input
              id="query"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !submitting) submit();
              }}
              placeholder="e.g. intermittent fasting health effects"
              className="mt-2 w-full rounded border border-border bg-bg-elevated px-4 py-3 text-15 text-text-primary placeholder:text-text-muted focus:border-brand focus:outline-none"
              disabled={submitting}
            />

            {/* 建议词: 每个前面一个色点 */}
            <div className="mt-3 flex flex-wrap gap-2">
              {SUGGESTED_QUERIES.map((q, idx) => {
                const dotColors = ["#B4AEFF", "#FFB547", "#4ADE80", "#7DD3FC"];
                return (
                  <button
                    key={q}
                    type="button"
                    onClick={() => setQuery(q)}
                    disabled={submitting}
                    className="group inline-flex items-center gap-1.5 rounded-sm border border-border bg-bg-elevated/40 px-2.5 py-1 text-12 text-text-secondary transition-colors hover:border-border-strong hover:bg-bg-elevated hover:text-text-primary disabled:opacity-60"
                  >
                    <span
                      aria-hidden
                      className="h-[5px] w-[5px] rounded-full"
                      style={{ background: dotColors[idx % dotColors.length] }}
                    />
                    {q}
                  </button>
                );
              })}
            </div>

            {/* 论文数量 */}
            <div className="mt-7">
              <div className="flex items-baseline justify-between">
                <label htmlFor="limit" className="text-13 text-text-secondary">
                  论文数量
                </label>
                <span className="num text-12 text-text-muted">
                  <span className="text-text-primary">{limit}</span> 篇
                  <span className="text-text-muted"> · </span>
                  <span className="text-accent-support">~{secs}s</span>
                  <span className="text-text-muted"> · </span>
                  <span className="text-brand">~{tokensK}k</span>
                  <span className="text-text-muted"> tokens</span>
                </span>
              </div>
              <input
                id="limit"
                type="range"
                min={3}
                max={30}
                step={1}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                disabled={submitting}
                className="mt-3 w-full accent-brand"
              />
              <div className="mt-1 flex justify-between text-10 text-text-muted">
                <span>3</span>
                <span className="text-text-secondary">推荐 10 - 20</span>
                <span>30</span>
              </div>
            </div>

            {/* 主按钮 + 演示 */}
            <div className="mt-7 flex flex-col gap-3 sm:flex-row">
              <button
                type="button"
                onClick={() => submit()}
                disabled={submitting}
                className="group relative flex-1 overflow-hidden rounded border border-brand/60 bg-brand/10 px-5 py-3 text-15 font-medium text-brand transition-all hover:bg-brand/20 hover:shadow-[0_0_24px_-6px_rgba(180,174,255,0.5)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {submitting ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="h-[10px] w-[10px] animate-pulse rounded-sm bg-brand" />
                    正在提交任务 ...
                  </span>
                ) : (
                  <span className="inline-flex items-center justify-center gap-2">
                    <span aria-hidden className="text-brand/70">✦</span>
                    开始分析
                  </span>
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  setQuery(DEMO_QUERY);
                  submit(DEMO_QUERY);
                }}
                disabled={submitting}
                className="rounded border border-border px-5 py-3 text-13 text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary disabled:opacity-60"
              >
                一键演示
              </button>
            </div>

            {error && (
              <div className="mt-4 rounded border border-accent-conflict/40 bg-accent-conflict/10 px-3 py-2 text-13 text-accent-conflict">
                {error}
              </div>
            )}
          </div>
        </section>

        {/* "工作原理" · 四步每步独立色 */}
        <section id="how" className="mt-24 w-full text-center scroll-mt-24">
          <div className="eyebrow text-brand/80">how it works</div>
          <h3 className="mt-2 text-20 font-medium">
            从一个<span className="text-brand">问题</span>到一篇<span className="text-accent-support">综述</span>
            <span className="text-text-muted"> · </span>
            四步流水线
          </h3>
          <ol className="mt-8 grid gap-4 md:grid-cols-4">
            {[
              {
                no: "01",
                title: "检索",
                desc: "OpenAlex + arXiv 合并去重",
                color: "#B4AEFF",
                en: "RETRIEVE",
              },
              {
                no: "02",
                title: "抽取",
                desc: "DeepSeek 结构化每条核心主张",
                color: "#7DD3FC",
                en: "EXTRACT",
              },
              {
                no: "03",
                title: "判定",
                desc: "两两对比: 支持 / 矛盾 / 无关",
                color: "#FFB547",
                en: "CLASSIFY",
              },
              {
                no: "04",
                title: "综述",
                desc: "反向生成带引用的学术段落",
                color: "#4ADE80",
                en: "REVIEW",
              },
            ].map((step) => (
              <li
                key={step.no}
                className="group relative overflow-hidden rounded-lg border border-border bg-bg-surface/60 p-4 text-left transition-all hover:border-border-strong hover:bg-bg-surface"
              >
                {/* 顶部 2px 色条 */}
                <span
                  aria-hidden
                  className="absolute left-0 top-0 h-[2px] w-full"
                  style={{ background: step.color }}
                />
                <div className="flex items-baseline justify-between">
                  <span className="num text-24 font-medium" style={{ color: step.color }}>
                    {step.no}
                  </span>
                  <span
                    className="text-10 uppercase tracking-label"
                    style={{ color: step.color, opacity: 0.7 }}
                  >
                    {step.en}
                  </span>
                </div>
                <div className="mt-3 text-15 text-text-primary">{step.title}</div>
                <div className="mt-1 text-12 text-text-secondary">{step.desc}</div>
              </li>
            ))}
          </ol>
        </section>

        {/* "关于" · 修复死链 */}
        <section id="about" className="mt-24 w-full scroll-mt-24">
          <div className="text-center">
            <div className="eyebrow text-brand/80">about</div>
            <h3 className="mt-2 text-20 font-medium">
              关于 <span className="text-brand">PaperTrace</span>
            </h3>
          </div>

          {/* 项目简介 (衬线) */}
          <p className="mx-auto mt-6 max-w-2xl text-center font-serif text-15 leading-[28px] text-text-secondary">
            PaperTrace 把"读完十几篇英文论文才能写出一段综述"这件事自动化:
            <br />
            它先用 <span className="text-text-primary">LLM 把每篇论文压成结构化主张</span>,
            再两两判定关系,
            <br />
            最后把整张<span className="text-accent-conflict">争论网络</span>展示给你 ——
            <span className="text-brand"> 5 分钟内</span>看清一个领域的共识与分歧。
          </p>

          {/* 三栏特性 */}
          <div className="mt-10 grid gap-4 md:grid-cols-3">
            <FeatureCard
              icon="◆"
              iconColor="#B4AEFF"
              title="结构化抽取"
              desc="主张拆成 subject / intervention / conclusion / direction 四元组, Pydantic 严格校验, 失败自动重试"
            />
            <FeatureCard
              icon="◇"
              iconColor="#FFB547"
              title="N×N 关系矩阵"
              desc="两级缓存 + 批量 LLM + 词汇过滤 + 异步并发, 60 claims 仅 ~120 次调用"
            />
            <FeatureCard
              icon="✦"
              iconColor="#4ADE80"
              title="可追溯综述"
              desc="生成 200-400 字段落, 每处引用带 [N] 标号, 可一键导出 Markdown / BibTeX"
            />
          </div>

          {/* 技术栈徽章 */}
          <div className="mt-10 rounded-lg border border-border bg-bg-surface/60 px-6 py-5">
            <div className="eyebrow text-text-muted">tech stack</div>
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-3 text-12">
              <TechItem color="#4ADE80" name="FastAPI" tag="Backend" />
              <TechItem color="#B4AEFF" name="Next.js 14" tag="Frontend" />
              <TechItem color="#7DD3FC" name="DeepSeek" tag="LLM" />
              <TechItem color="#FFB547" name="OpenAlex" tag="Data" />
              <TechItem color="#FFB547" name="arXiv" tag="Data" />
              <TechItem color="#A5A9C4" name="SQLAlchemy 2.0" tag="ORM" />
              <TechItem color="#A5A9C4" name="ECharts" tag="Viz" />
              <TechItem color="#A5A9C4" name="Tailwind" tag="Style" />
            </div>
          </div>

          {/* 三个数字, 强调"已工程化" */}
          <div className="mt-10 grid grid-cols-3 gap-4 text-center">
            <BigStat label="数据源" value="2" suffix="个" colored="#FFB547" />
            <BigStat label="LLM 调用优化层" value="5" suffix="层" colored="#B4AEFF" />
            <BigStat label="缓存层级" value="L1+L2" colored="#4ADE80" />
          </div>
        </section>
      </main>

      {/* ===== 底部 ===== */}
      <footer className="relative z-10 border-t border-border">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-2 px-6 py-4 text-11 text-text-muted">
          <span>
            数据源: <span className="text-text-secondary">OpenAlex</span>
            <span className="mx-1.5">+</span>
            <span className="text-text-secondary">arXiv</span>
            <span className="mx-1.5">·</span>
            推理: <span className="text-brand">DeepSeek</span>
          </span>
          <span className="num">PaperTrace · v0.1</span>
        </div>
      </footer>
    </div>
  );
}

/* ============== 小组件 ============== */

function LogoMark() {
  // 极简 Logo: 两个错位的小方块 + 微小 brand 圆点
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden>
      <rect x="1" y="3" width="10" height="10" rx="2.5" fill="#4ADE80" opacity="0.92" />
      <rect x="9" y="9" width="10" height="10" rx="2.5" fill="#FFB547" opacity="0.92" />
      <circle cx="11" cy="11" r="1.5" fill="#B4AEFF" />
    </svg>
  );
}

function FlowChip({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className="h-[6px] w-[6px] rounded-full"
        style={{ background: color }}
      />
      <span className="text-text-secondary">{label}</span>
    </span>
  );
}

function Arrow() {
  return <span className="px-1 text-text-muted">→</span>;
}

function CornerBracket({ pos }: { pos: "tl" | "tr" | "bl" | "br" }) {
  const cls = {
    tl: "left-0 top-0 border-l border-t",
    tr: "right-0 top-0 border-r border-t",
    bl: "left-0 bottom-0 border-l border-b",
    br: "right-0 bottom-0 border-r border-b",
  }[pos];
  return (
    <span
      aria-hidden
      className={`pointer-events-none absolute h-3 w-3 ${cls}`}
      style={{ borderColor: "rgba(180,174,255,0.4)" }}
    />
  );
}

function FeatureCard({
  icon,
  iconColor,
  title,
  desc,
}: {
  icon: string;
  iconColor: string;
  title: string;
  desc: string;
}) {
  return (
    <article className="rounded-lg border border-border bg-bg-surface/60 p-5 transition-colors hover:border-border-strong">
      <div className="flex items-center gap-2">
        <span
          className="flex h-[26px] w-[26px] items-center justify-center rounded-sm text-14"
          style={{
            background: `${iconColor}1A`,
            color: iconColor,
          }}
          aria-hidden
        >
          {icon}
        </span>
        <h4 className="text-14 font-medium text-text-primary">{title}</h4>
      </div>
      <p className="mt-3 text-12 leading-[20px] text-text-secondary">{desc}</p>
    </article>
  );
}

function TechItem({
  color,
  name,
  tag,
}: {
  color: string;
  name: string;
  tag: string;
}) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden
        className="h-[6px] w-[6px] rounded-full"
        style={{ background: color }}
      />
      <span className="text-text-primary">{name}</span>
      <span className="text-10 uppercase tracking-label text-text-muted">{tag}</span>
    </span>
  );
}

function BigStat({
  label,
  value,
  suffix,
  colored,
}: {
  label: string;
  value: string;
  suffix?: string;
  colored: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-bg-surface/40 px-4 py-5">
      <div className="eyebrow text-text-muted">{label}</div>
      <div className="mt-2 flex items-baseline justify-center gap-1">
        <span className="num text-28 font-medium" style={{ color: colored }}>
          {value}
        </span>
        {suffix && <span className="text-12 text-text-muted">{suffix}</span>}
      </div>
    </div>
  );
}
