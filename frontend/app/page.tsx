/**
 * PaperTrace 首页
 * ====================
 * 一个搜索框 + 一个按钮。用户输入研究问题 → 调后端 → 跳转到结果页。
 *
 * App Router 与 Pages Router 的区别（新手必读）：
 *
 *   Pages Router（旧）：
 *     pages/index.tsx       → 路由 /
 *     pages/about.tsx       → 路由 /about
 *     pages/blog/[id].tsx   → 路由 /blog/:id
 *     - 默认是 Server-Side Rendering，但写法上和普通 React 一样
 *     - 用 getServerSideProps / getStaticProps 取数据
 *
 *   App Router（Next.js 13+ 推荐，本项目用的）：
 *     app/page.tsx                  → 路由 /
 *     app/about/page.tsx            → 路由 /about
 *     app/blog/[id]/page.tsx        → 路由 /blog/:id
 *     - 默认是 React Server Component（在服务器端渲染，浏览器只收 HTML）
 *     - 想用 useState/useEffect/onClick 等"客户端"功能时，文件顶部加 "use client"
 *     - 数据通常直接 await fetch(...)，更接近原生 React Server Component 模型
 *
 * 这个文件需要 onClick + useState + useRouter，所以是 client component。
 */

"use client"; // ← 关键！声明这是客户端组件，能用 hooks 和事件

import { useState } from "react";
import { useRouter } from "next/navigation"; // App Router 的 router
import { startAnalysis } from "@/lib/api";

export default function HomePage() {
  // 用户输入的研究问题
  const [query, setQuery] = useState("");
  // 拉多少篇论文，默认 20
  const [limit, setLimit] = useState(20);
  // 提交中状态：禁用按钮 + 显示 loading
  const [submitting, setSubmitting] = useState(false);
  // 错误信息（提交失败时显示）
  const [error, setError] = useState<string | null>(null);

  const router = useRouter();

  /** 点击按钮触发：调后端 → 拿 task_id → 跳转 */
  const handleSubmit = async () => {
    // 简单前端校验
    if (!query.trim()) {
      setError("请先输入研究问题");
      return;
    }

    setError(null);
    setSubmitting(true);

    try {
      const res = await startAnalysis(query.trim(), limit);
      // 跳转到结果页，task_id 作为路径参数
      router.push(`/result/${res.task_id}`);
    } catch (e: unknown) {
      // axios 拦截器把错误统一翻成了 Error 对象
      const message = e instanceof Error ? e.message : "未知错误";
      setError(message);
      setSubmitting(false);
    }
    // 注意：成功路径不重置 submitting，因为页面马上要跳走了
  };

  /** 回车也能提交 */
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !submitting) {
      handleSubmit();
    }
  };

  return (
    // 渐变背景：from-slate-900 → via-indigo-950 → to-slate-900，配深色科技感
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white">
      {/* 中央卡片容器 */}
      <div className="container mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center px-4 py-16">
        {/* 标题区 */}
        <div className="mb-10 text-center">
          <h1 className="bg-gradient-to-r from-indigo-300 via-purple-300 to-pink-300 bg-clip-text text-5xl font-bold tracking-tight text-transparent md:text-6xl">
            PaperTrace
          </h1>
          <p className="mt-4 text-lg text-slate-300">学术论文矛盾发现工具</p>
          <p className="mt-1 text-sm text-slate-400">
            输入一个研究问题 → 自动拉论文、抽主张、画矛盾矩阵、生成综述
          </p>
        </div>

        {/* 搜索卡片 */}
        <div className="w-full rounded-2xl border border-white/10 bg-white/5 p-6 shadow-2xl backdrop-blur-md md:p-8">
          {/* 搜索输入 */}
          <label
            htmlFor="query"
            className="mb-2 block text-sm font-medium text-slate-200"
          >
            研究问题（建议英文，论文库以英文为主）
          </label>
          <input
            id="query"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="例如：remote work productivity, sleep deprivation cognition ..."
            className="w-full rounded-xl border border-white/10 bg-slate-900/60 px-4 py-3 text-base text-white placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-400/40"
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
              <span>越多越慢，建议 5-15</span>
              <span>30</span>
            </div>
          </div>

          {/* 提交按钮 */}
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="mt-6 w-full rounded-xl bg-gradient-to-r from-indigo-500 to-purple-500 px-6 py-3 text-lg font-semibold text-white shadow-lg shadow-indigo-500/30 transition hover:from-indigo-400 hover:to-purple-400 disabled:cursor-not-allowed disabled:opacity-60"
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

          {/* 错误提示 */}
          {error && (
            <div className="mt-4 rounded-lg border border-red-400/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
              ⚠ {error}
            </div>
          )}
        </div>

        {/* 底部小字 */}
        <p className="mt-8 text-center text-xs text-slate-500">
          数据源：Semantic Scholar · 推理：DeepSeek · 开源项目
        </p>
      </div>
    </main>
  );
}
