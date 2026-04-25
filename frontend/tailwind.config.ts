/**
 * PaperTrace 设计系统 (v2)
 * ==========================
 *
 * 基础原则:
 *   - 配色克制: 深底 + 低饱和紫蓝 + 仅两种强调色 (琥珀/翡翠)
 *   - 禁用: 紫色渐变、纯色圆胶囊、阴影光晕、RGB 红绿 (除非是数据真值)
 *   - 圆角只使用 4 / 6 / 12 px 三个值
 *   - 字重只使用 400 / 500
 *   - 字号从 10 到 36 按固定刻度取值
 */
import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // —— 背景层级 ——
        "bg-base": "#111428",         // 整页底色 (比 v2 初版稍暖)
        "bg-surface": "#1A1E3A",      // 卡片底 (提升层次感)
        "bg-elevated": "#22274A",     // 卡片上的输入框 / 次级容器
        "bg-inset": "#161A33",        // 卡片内内嵌区 (图表背景)

        // —— 文本层级 ——
        "text-primary": "#E8E9F3",    // 主文本
        "text-secondary": "#A5A9C4",  // 次级文本
        "text-muted": "#6B6F8A",      // 注释级 / 禁用

        // —— 品牌主色 (低饱和紫蓝) ——
        brand: {
          DEFAULT: "#B4AEFF",
          soft: "#8B82E8",
          ink: "#5A52B8",
        },

        // —— 功能色 (仅两种) ——
        "accent-conflict": "#FFB547", // 琥珀, 表示"矛盾/分歧"
        "accent-support": "#4ADE80",  // 翡翠, 表示"支持/共识"
        "accent-neutral": "#7A8099",  // 中立

        // —— 分割线 ——
        border: {
          DEFAULT: "#242845",
          strong: "#2F3355",
        },
      },
      borderRadius: {
        sm: "4px",
        DEFAULT: "6px",
        lg: "12px",
      },
      fontSize: {
        "10": ["10px", { lineHeight: "14px" }],
        "11": ["11px", { lineHeight: "16px" }],
        "12": ["12px", { lineHeight: "18px" }],
        "13": ["13px", { lineHeight: "20px" }],
        "14": ["14px", { lineHeight: "22px" }],
        "15": ["15px", { lineHeight: "24px" }],
        "17": ["17px", { lineHeight: "26px" }],
        "20": ["20px", { lineHeight: "30px" }],
        "24": ["24px", { lineHeight: "34px" }],
        "28": ["28px", { lineHeight: "38px" }],
        "36": ["36px", { lineHeight: "46px" }],
      },
      fontWeight: {
        normal: "400",
        medium: "500",
      },
      fontFamily: {
        sans: [
          "var(--font-noto-sans-sc)",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Arial",
          "sans-serif",
        ],
        serif: [
          "var(--font-noto-serif-sc)",
          "ui-serif",
          "Georgia",
          "Times New Roman",
          "serif",
        ],
      },
      letterSpacing: {
        label: "0.08em",  // 小标签用, 纯大写时增强可读性
      },
      // 极轻量的过渡, 不需要大弹性动效
      transitionTimingFunction: {
        precise: "cubic-bezier(0.4, 0, 0.2, 1)",
      },
    },
  },
  plugins: [],
};
export default config;
