/**
 * 根布局
 * --------
 * · 通过 next/font/google 加载 Noto Sans SC + Noto Serif SC
 *   (next/font 是 Next.js 内置能力, 不是新依赖)
 * · 字体以 CSS 变量形式注入 <body>, Tailwind 的 font-sans / font-serif
 *   会按 tailwind.config.ts 的配置引用这两个变量
 */
import type { Metadata } from "next";
import { Noto_Sans_SC, Noto_Serif_SC } from "next/font/google";
import "./globals.css";

const notoSans = Noto_Sans_SC({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-noto-sans-sc",
  display: "swap",
});

const notoSerif = Noto_Serif_SC({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-noto-serif-sc",
  display: "swap",
});

export const metadata: Metadata = {
  title: "PaperTrace · 学术论文矛盾发现工具",
  description: "输入研究问题, 自动发现论文之间的争论、共识与分歧归因。",
  // 两个错位方块 = "对立的论文", 风格与首页 Logo 一致
  icons: {
    icon: "/icon.svg",
    apple: "/apple-icon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={`${notoSans.variable} ${notoSerif.variable} bg-bg-base text-text-primary antialiased`}>
        {children}
      </body>
    </html>
  );
}
