/**
 * PaperTrace 首页
 * ====================
 * 一个搜索框 + 一个按钮。用户输入研究问题 → 调后端 → 跳转到结果页。
 *
 * 视觉亮点:
 *   1. 背景:紫色网格 + 大光晕,营造科技感
 *   2. "✨ 一键演示"按钮:一键填入示例 query 并自动提交
 *   3. 玻璃拟态卡片 + 微交互(hover 抬起 / 阴影 / 边框发光)
 *
 * App Router 与 Pages Router 的区别(新手必读):
 *   App Router(Next.js 13+ 推荐,本项目用的)
 *     app/page.tsx → 路由 /
 *     - 默认是 React Server Component(在服务器端渲染)
 *     - 想用 useState/useEffect/onClick 等"客户端"功能时,文件顶部加 "use client"
 *
 * 这个文件需要 onClick + useState + useRouter,所以是 client component。
 */

"use client"; // ← 关键!声明这是客户端组件,能用 hooks 和事件

import { useState } from "react";
import { useRouter } from "next/navigation"; // App Router 的 router
import { startAnalysis } from "@/lib/api";
// 一键演示的示例 query
const DEMO_QUERY = "intermittent fasting health effects";

export default function HomePage() {
  // 用户输入的研究问题
  const [query, setQuery] = useState("");
  // 拉多少篇论文,默认 20
  const [limit, setLimit] = useState(20);
  // 提交中状态:禁用按钮 + 显示 loading
  const [submitting, setSubmitting] = useState(false);
  // 错误信息(提交失败时显示)
  const [error, setError] = useState<string | null>(null);

  const router = useRouter();

  /**
   * 抽出来的"提交"逻辑:
   * 接受一个可选的 overrideQuery 参数,这样既能给输入框的按钮用,
   * 也能给"一键演示"按钮用 —— 演示按钮会传入示例 query。
   * 为什么不复用 setQuery + handleSubmit?
   *   因为 setQuery 是异步的,setQuery(DEMO_QUERY) 后立刻调 handleSubmit
   *   读到的还是旧的 query。直接传参更安全。
   */
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
    // 成功路径不重置 submitting,因为页面马上要跳走了
  };

  /** 一键演示:把输入框填上示例,顺便触发提交 */
  const handleDemo = () => {
    setQuery(DEMO_QUERY);
    submit(DEMO_QUERY);
  };

  /** 回车也能提交 */
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !submitting) submit();
  };

  return (
    // relative + overflow-hidden 让背景层不会溢出滚动条
    <main className="relative min-h-screen overflow-hidden bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white">
      {/* ===== 背景层 1:淡紫色网格 =====
          做法:用线性渐变画一根细线,然后通过 background-size 重复成网格。
          rgba(139,92,246,0.1) 是 tailwind 的 violet-500 透明 10%,够淡又能看见。
          absolute -z-10 让它躺在所有内容下面。 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          backgroundImage:
            "linear-gradient(rgba(139,92,246,0.1) 1px, transparent 1px), linear-gradient(to right, rgba(139,92,246,0.1) 1px, transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />

      {/* ===== 背景层 2:600px 紫色径向光晕 =====
          radial-gradient 画一个柔和的圆,中心紫色,边缘透明,
          模拟"屏幕中央漂浮一团光"的效果。
          left/top -200px 让光晕中心偏出屏幕,只露半边,更有层次。 */}
      <div
        aria-hidden
        className="pointer-events-none absolute -z-10"
        style={{
          left: "50%",
          top: "20%",
          width: "600px",
          height: "600px",
          transform: "translate(-50%, 0)",
          background:
            "radial-gradient(circle, rgba(139,92,246,0.25) 0%, rgba(139,92,246,0.05) 40%, transparent 70%)",
          filter: "blur(20px)",
        }}
      />

      {/* 中央卡片容器 */}
      <div className="container relative mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center px-4 py-16">
        {/* 标题区 */}
        <div className="mb-10 text-center">
          <h1 className="bg-gradient-to-r from-indigo-300 via-purple-300 to-pink-300 bg-clip-text text-5xl font-bold tracking-tight text-transparent md:text-6xl">
            PaperTrace
          </h1>

          <p className="mt-4 text-sm text-slate-400">
            学术论文矛盾发现工具 · 自动拉论文、抽主张、画矛盾矩阵、生成综述
          </p>
        </div>

        {/* 搜索卡片 —— 玻璃拟态:backdrop-blur-xl + 半透明白底 + 半透明白边框 */}
        <div className="w-full rounded-2xl border border-white/10 bg-white/5 p-6 shadow-2xl backdrop-blur-xl transition-all duration-200 hover:border-purple-400/30 hover:shadow-purple-500/10 md:p-8">
          {/* 搜索输入 */}
          <label
            htmlFor="query"
            className="mb-2 block text-sm font-medium text-slate-200"
          >
            研究问题(建议英文,论文库以英文为主)
          </label>
          <input
            id="query"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="例如:remote work productivity, sleep deprivation cognition ..."
            className="w-full rounded-xl border border-white/10 bg-slate-900/60 px-4 py-3 text-base text-white placeholder:text-slate-500 transition-all focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400/40"
            disabled={submitting}
          />

          {/* 数量滑块 */}
          <div className="mt-5">
            <label
              htmlFor="limit"
              className="mb-2 flex items-center justify-between text-sm font-medium text-slate-200"
            >
              <span>论文数量</span>
              <span className="rounded-full bg-indigo-500/20 px-3 py-0.5 text-indigo-200">
                {limit} 篇
              </span>
            </label>
            <input
              id="limit"
              type="range"
              min={3}
              max={30}
              step={1}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              disabled={submitting}
              className="w-full accent-indigo-400"
            />
            <div className="mt-1 flex justify-between text-xs text-slate-500">
              <span>3</span>
              <span>越多越慢,建议 5-15</span>
              <span>30</span>
            </div>
          </div>

          {/* 主提交按钮 + 一键演示按钮(并排) */}
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              onClick={() => submit()}
              disabled={submitting}
              className="flex-1 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-500 px-6 py-3 text-lg font-semibold text-white shadow-lg shadow-indigo-500/30 transition-all duration-200 hover:-translate-y-0.5 hover:from-indigo-400 hover:to-purple-400 hover:shadow-xl hover:shadow-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
            >
              {submitting ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                  正在提交 ...
                </span>
              ) : (
                "开始分析"
              )}
            </button>

            {/* ✨ 一键演示:不抢主按钮的视觉,但也用紫色调,
                hover 时同样的微交互,保持一致的"质感语言" */}
            <button
              type="button"
              onClick={handleDemo}
              disabled={submitting}
              className="rounded-xl border border-purple-400/40 bg-purple-500/10 px-5 py-3 text-base font-medium text-purple-200 transition-all duration-200 hover:-translate-y-0.5 hover:border-purple-300/60 hover:bg-purple-500/20 hover:shadow-lg hover:shadow-purple-500/20 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
            >
              ✨ 一键演示
            </button>
          </div>

          {/* 错误提示 */}
          {error && (
            <div className="mt-4 rounded-lg border border-red-400/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
              ⚠ {error}
            </div>
          )}
        </div>

        {/* 底部小字 */}
        <p className="mt-8 text-center text-xs text-slate-500">
          数据源:OpenAlex + arXiv · 推理:DeepSeek
        </p>
      </div>
    </main>
  );
}
