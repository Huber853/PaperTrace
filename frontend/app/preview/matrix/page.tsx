/**
 * /preview/matrix 预览页
 * ========================
 * 用 mock 数据直接预览 ContradictionMatrix 组件。
 * 好处：答辩前不用等后端跑完，直接 `npm run dev` 打开这个路由就能看到效果。
 * 也方便前端调样式时快速迭代。
 */

"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import {
  MOCK_CLAIMS,
  MOCK_MATRIX,
  MOCK_PAPER_TITLES,
} from "@/components/ContradictionMatrix.mock";

// ECharts 会在渲染时访问 window，必须禁用 SSR
const ContradictionMatrix = dynamic(
  () => import("@/components/ContradictionMatrix"),
  { ssr: false }
);

export default function PreviewPage() {
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-white">
      <div className="container mx-auto max-w-5xl px-4 py-10">
        <Link
          href="/"
          className="mb-4 inline-block text-sm text-indigo-300 hover:text-indigo-200"
        >
          ← 返回首页
        </Link>

        <h1 className="text-3xl font-bold">矛盾矩阵预览（Mock 数据）</h1>
        <p className="mt-1 text-sm text-slate-400">
          7 条精心编排的 claim，展示 support / contradict / unrelated 三种格子。
          hover 看详情，click 打开 modal。
        </p>

        <div className="mt-8">
          <ContradictionMatrix
            claims={MOCK_CLAIMS}
            matrix={MOCK_MATRIX}
            paperTitleById={MOCK_PAPER_TITLES}
          />
        </div>
      </div>
    </main>
  );
}
