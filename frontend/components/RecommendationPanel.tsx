/**
 * RecommendationPanel · 研究方向建议
 * ----------------------------------------
 * 三态: 未生成 / 加载中 / 已生成
 *
 * · 未生成: 中央一个主按钮 "生成研究方向建议"
 * · 加载中: 4 张骨架卡片 + 旋转图标
 * · 已生成: 标题行 + Tab (研究问题 / 方法路线) + 卡片列表 + 元信息条
 *
 * 调用:
 *   前端拼 prompt → POST /api/chat (response_format=json_object)
 *   后端是轻量 DeepSeek 代理
 *
 * 不修改任何现有 API 或 state, 仅作为一个独立展示+生成组件插入结果页
 */
"use client";

import { useMemo, useState } from "react";
import { chatCompletion } from "@/lib/api";
import type { Claim, Paper, Relation } from "@/lib/api";

/* ============== 类型定义 ============== */
interface Recommendation {
  title: string;
  desc: string;
  sources: string[];
  priority: "high" | "medium" | "low";
}

interface RecommendResponse {
  questions: Recommendation[];
  methods: Recommendation[];
}

interface ConflictInput {
  id: string;            // "分歧1" · "分歧2" ...
  topic: string;         // 主题关键词
  claimA: string;        // 一方观点
  claimB: string;        // 另一方观点
  confidence: number;    // 0-100
  reason: string;        // AI 判定理由 (可为空)
}

interface Props {
  query: string;
  claims: Claim[];
  matrix: Relation[][];
  papers: Paper[];
  /** 嵌入模式:不渲染外层 section / 边框 / 渐变背景, 由父级提供容器 */
  embedded?: boolean;
}

/* ============== 组件 ============== */
export default function RecommendationPanel({
  query,
  claims,
  matrix,
  embedded = false,
}: Props) {
  // 从矩阵抽取 contradict 对 → 作为 prompt 输入
  const conflicts: ConflictInput[] = useMemo(() => {
    const out: ConflictInput[] = [];
    const n = claims.length;
    let idx = 1;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const cell = matrix?.[i]?.[j];
        if (!cell || cell.relation !== "contradict") continue;
        if (cell.confidence < 0.5) continue;
        out.push({
          id: `分歧${idx++}`,
          topic: claims[i].subject || claims[i].intervention || "",
          claimA: claims[i].conclusion,
          claimB: claims[j].conclusion,
          confidence: Math.round(cell.confidence * 100),
          reason: cell.reason || "",
        });
      }
    }
    return out;
  }, [claims, matrix]);

  // 精简一份 claim 列表给 prompt (不丢太多 token)
  const claimsForPrompt = useMemo(
    () =>
      claims.map((c, i) => ({
        id: `C${i}`,
        subject: c.subject,
        conclusion: c.conclusion,
        direction: c.direction,
      })),
    [claims]
  );

  const [data, setData] = useState<RecommendResponse | null>(null);
  const [meta, setMeta] = useState<{ ms: number; model: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"questions" | "methods">("questions");

  const buildPrompt = (): string => {
    return `你是学术研究方向推导助手。基于以下矛盾观点数据，为研究者推荐下一步可探索的方向。

研究主题：${query}
识别出的矛盾组：${JSON.stringify(conflicts)}
核心观点列表：${JSON.stringify(claimsForPrompt)}

严格按以下 JSON 结构输出，不要 markdown 包裹，不要解释：

{
  "questions": [
    { "title": "一句话研究问题", "desc": "80-150 字说明 why 值得做 + how 切入", "sources": ["分歧X 主题名"], "priority": "high" | "medium" | "low" }
  ],
  "methods": [
    { "title": "方法名", "desc": "为什么适用当前分歧，给具体建议", "sources": ["适用于分歧X"], "priority": "high" | "medium" | "low" }
  ]
}

质量要求 (按重要性排序)：
1. **语言要求**：title / desc / sources 中的所有文本必须使用简体中文输出。
   - 即使输入的 conflicts / claims 是英文，也要把研究对象、方法名、概念名翻译成中文学术表达。
   - 仅在必要时保留专有名词的英文（如算法缩写 BERT / 机构名），且需在第一次出现时附上中文解释。
   - sources 字段中的"分歧X"前缀直接保留中文不译。
2. questions 必须从具体矛盾推导，禁止"需要更多研究"这类空话。
3. methods 必须明确适用于哪一组分歧。
4. sources 严格引用输入的矛盾组（保留输入里的 "分歧1" / "分歧2" 这类 id），不能编造。
5. questions 生成 3-5 条，methods 生成 2-4 条。
6. 按 priority 降序排列。
7. desc 控制在 80-150 字。`;
  };

  const run = async () => {
    if (conflicts.length === 0) {
      setError("没有识别出矛盾对, 无法推导研究方向。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await chatCompletion(
        [{ role: "user", content: buildPrompt() }],
        {
          temperature: 0.6,
          response_format: { type: "json_object" },
          timeoutMs: 30_000,
        }
      );

      let parsed: RecommendResponse;
      try {
        parsed = JSON.parse(resp.content);
      } catch {
        setError("生成格式异常, 点击重试");
        return;
      }
      if (!Array.isArray(parsed.questions) || !Array.isArray(parsed.methods)) {
        setError("生成格式异常, 点击重试");
        return;
      }

      // 按 priority 降序再保险一次
      const order: Record<string, number> = { high: 0, medium: 1, low: 2 };
      parsed.questions.sort((a, b) => order[a.priority] - order[b.priority]);
      parsed.methods.sort((a, b) => order[a.priority] - order[b.priority]);

      setData(parsed);
      setMeta({ ms: resp.elapsed_ms, model: resp.model });
    } catch (e: unknown) {
      if (e instanceof Error) {
        if (e.name === "CanceledError" || e.message.includes("abort")) {
          setError("请求超时, 点击重试");
        } else {
          setError(`生成失败: ${e.message}`);
        }
      } else {
        setError("未知错误");
      }
    } finally {
      setLoading(false);
    }
  };

  /* ============== 内层主体 (无外壳) ============== */
  const body = (
    <>
      {/* 未生成态 */}
      {!data && !loading && (
        <InitialState
          onGenerate={run}
          conflictCount={conflicts.length}
          error={error}
        />
      )}

      {/* 加载态 */}
      {loading && <LoadingState />}

      {/* 已生成态 */}
      {data && !loading && (
        <GeneratedState
          data={data}
          meta={meta}
          tab={tab}
          setTab={setTab}
          conflictCount={conflicts.length}
          onRegenerate={run}
          error={error}
        />
      )}
    </>
  );

  /* ============== 嵌入模式: 由父级控制容器, 这里只负责内容 ============== */
  if (embedded) {
    return <div>{body}</div>;
  }

  /* ============== 独立模式: 自带带渐变的外层 section ============== */
  return (
    <section
      className="rounded-lg border border-border"
      style={{
        padding: "1.5rem",
        background:
          "radial-gradient(ellipse at 30% 20%, rgba(139,127,255,0.06) 0%, transparent 50%), #111428",
      }}
    >
      {body}
    </section>
  );
}

/* ================================================================ */
/* 未生成态                                                           */
/* ================================================================ */
function InitialState({
  onGenerate,
  conflictCount,
  error,
}: {
  onGenerate: () => void;
  conflictCount: number;
  error: string | null;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-center">
      <button
        type="button"
        onClick={onGenerate}
        disabled={conflictCount === 0}
        className="rounded border px-5 py-3 text-14 font-medium text-brand transition-colors hover:bg-brand/10 disabled:cursor-not-allowed disabled:opacity-50"
        style={{
          borderColor: "rgba(180,174,255,0.25)",
          borderWidth: "0.5px",
          background: "transparent",
        }}
      >
        <span className="mr-2 text-brand">✦</span>
        生成研究方向建议
      </button>
      <div className="mt-3 text-11" style={{ color: "#6B6985" }}>
        {conflictCount === 0
          ? "本次分析未识别到矛盾对, 暂无法推导研究方向"
          : `基于本次 ${conflictCount} 组矛盾 · 预计耗时 ~15s`}
      </div>
      {error && (
        <div className="mt-4 rounded border border-accent-conflict/40 bg-accent-conflict/10 px-3 py-2 text-12 text-accent-conflict">
          {error}
        </div>
      )}
    </div>
  );
}

/* ================================================================ */
/* 加载态                                                             */
/* ================================================================ */
function LoadingState() {
  return (
    <div>
      <div className="mb-4 flex items-center gap-2 text-13 text-text-secondary">
        <Spinner />
        AI 正在推导研究机会 ...
      </div>
      <div className="space-y-3">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className="animate-pulse rounded-sm border"
            style={{
              padding: "14px 16px",
              background: "#14162A",
              borderColor: "rgba(180,174,255,0.08)",
              borderWidth: "0.5px",
            }}
          >
            <div className="h-[14px] w-1/2 rounded-sm bg-bg-elevated/80" />
            <div className="mt-3 h-[10px] w-full rounded-sm bg-bg-elevated/60" />
            <div className="mt-2 h-[10px] w-4/5 rounded-sm bg-bg-elevated/60" />
          </div>
        ))}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" className="animate-spin">
      <circle
        cx="12"
        cy="12"
        r="9"
        fill="none"
        stroke="rgba(180,174,255,0.2)"
        strokeWidth="2"
      />
      <path
        d="M12 3 a9 9 0 0 1 9 9"
        fill="none"
        stroke="#B4AEFF"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* ================================================================ */
/* 已生成态                                                            */
/* ================================================================ */
function GeneratedState({
  data,
  meta,
  tab,
  setTab,
  conflictCount,
  onRegenerate,
  error,
}: {
  data: RecommendResponse;
  meta: { ms: number; model: string } | null;
  tab: "questions" | "methods";
  setTab: (t: "questions" | "methods") => void;
  conflictCount: number;
  onRegenerate: () => void;
  error: string | null;
}) {
  const list = tab === "questions" ? data.questions : data.methods;

  return (
    <div>
      {/* 顶部标题行 */}
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <StarIcon />
          <h2 className="text-17 font-medium" style={{ color: "#E8E6F5" }}>
            研究方向建议
          </h2>
          <span
            className="rounded-sm px-2 py-[1px] text-10 font-medium uppercase tracking-label"
            style={{
              background: "rgba(180,174,255,0.12)",
              color: "#B4AEFF",
            }}
          >
            Beta
          </span>
        </div>
        <button
          type="button"
          onClick={onRegenerate}
          className="flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-12 transition-colors"
          style={{ color: "#9FA5B8" }}
          onMouseEnter={(e) =>
            (e.currentTarget.style.background = "rgba(180,174,255,0.08)")
          }
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <RefreshIcon />
          重新生成
        </button>
      </header>

      <p className="mt-2 text-11" style={{ color: "#6B6985" }}>
        基于本次识别的 {conflictCount} 组矛盾观点, AI 为你推导出潜在的研究机会
      </p>

      {/* Tab 切换 */}
      <div
        className="mt-4 flex rounded-sm"
        style={{
          padding: "3px",
          background: "#14162A",
        }}
      >
        <TabButton active={tab === "questions"} onClick={() => setTab("questions")}>
          研究问题 · {data.questions.length}
        </TabButton>
        <TabButton active={tab === "methods"} onClick={() => setTab("methods")}>
          方法路线 · {data.methods.length}
        </TabButton>
      </div>

      {/* 卡片列表 */}
      <div className="mt-4 space-y-3">
        {list.length === 0 ? (
          <div className="py-6 text-center text-12" style={{ color: "#6B6985" }}>
            当前 Tab 暂无推荐。
          </div>
        ) : (
          list.map((item, idx) => <RecoCard key={idx} item={item} />)
        )}
      </div>

      {error && (
        <div className="mt-4 rounded border border-accent-conflict/40 bg-accent-conflict/10 px-3 py-2 text-12 text-accent-conflict">
          {error}
        </div>
      )}

      {/* 底部元信息 */}
      {meta && (
        <div
          className="mt-[18px] flex flex-wrap items-center justify-between gap-2 rounded-sm"
          style={{
            padding: "10px 14px",
            background: "rgba(180,174,255,0.04)",
            border: "0.5px solid rgba(180,174,255,0.12)",
          }}
        >
          <span
            className="inline-flex items-center gap-2 text-11"
            style={{ color: "#9FA5B8" }}
          >
            <span
              className="inline-block h-[6px] w-[6px] rounded-sm"
              style={{ background: "#B4AEFF" }}
            />
            DeepSeek · 基于 {conflictCount} 组矛盾推导
          </span>
          <span
            className="num text-11"
            style={{
              color: "#6B6985",
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
            }}
          >
            推理耗时 {(meta.ms / 1000).toFixed(1)}s
          </span>
        </div>
      )}
    </div>
  );
}

/* ================================================================ */
/* 子组件                                                            */
/* ================================================================ */
function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex-1 rounded-sm py-1.5 text-12 transition-colors"
      style={{
        background: active ? "rgba(180,174,255,0.12)" : "transparent",
        color: active ? "#B4AEFF" : "#9FA5B8",
      }}
    >
      {children}
    </button>
  );
}

function RecoCard({ item }: { item: Recommendation }) {
  const [hover, setHover] = useState(false);

  const priColor =
    item.priority === "high"
      ? "#FFB547"
      : item.priority === "medium"
      ? "#B4AEFF"
      : "#6B6985";
  const priLabel =
    item.priority === "high" ? "高优先级" : item.priority === "medium" ? "中优先级" : "低优先级";

  return (
    <article
      className="relative overflow-hidden rounded-sm"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: hover ? "#1A1E3A" : "#14162A",
        border: "0.5px solid rgba(180,174,255,0.08)",
        padding: "14px 16px",
        transition: "background-color 160ms",
      }}
    >
      {/* 左侧 2px 优先级色条 */}
      <span
        aria-hidden
        className="absolute left-0 top-0 h-full"
        style={{ width: "2px", background: priColor }}
      />

      {/* 第一行: 标题 + 优先级 */}
      <div className="flex items-start gap-3">
        <h3
          className="flex-1 text-13 font-medium leading-snug"
          style={{ color: "#E8E6F5" }}
        >
          {item.title}
        </h3>
        <span
          className="shrink-0 text-10 uppercase tracking-label"
          style={{ color: priColor }}
        >
          {priLabel}
        </span>
      </div>

      {/* 第二行: 描述 */}
      <p
        className="mt-2 text-12"
        style={{ color: "#9FA5B8", lineHeight: 1.7 }}
      >
        {item.desc}
      </p>

      {/* 第三行: 溯源 */}
      {item.sources && item.sources.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-10">
          <span style={{ color: "#6B6985" }}>溯源 →</span>
          {item.sources.map((s, i) => (
            <span
              key={i}
              className="rounded-sm"
              style={{
                background: "rgba(255,181,71,0.08)",
                color: "#FFB547",
                padding: "2px 8px",
              }}
            >
              {s}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

function StarIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path
        d="M12 2 L14.09 8.26 L20.5 8.27 L15.45 12.14 L17.36 18.4 L12 14.52 L6.64 18.4 L8.55 12.14 L3.5 8.27 L9.91 8.26 Z"
        fill="#B4AEFF"
        opacity="0.9"
      />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
      <path
        d="M21 12 A9 9 0 1 1 12 3 M21 3 L21 9 L15 9"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
