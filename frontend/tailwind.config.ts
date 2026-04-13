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
        background: "var(--background)",
        foreground: "var(--foreground)",
      },
      animation: {
        breathe: "breathe 2s ease-in-out infinite",
      },
      keyframes: {
        breathe: {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(139,92,246,0.5)" },
          "50%": { boxShadow: "0 0 0 14px rgba(139,92,246,0)" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
