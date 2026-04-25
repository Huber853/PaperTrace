/**
 * HeroSplash · 首屏启动覆盖层
 * ----------------------------------------------------------------
 * 进入 PaperTrace 首页时叠在最顶层的"启动屏":
 *   · canvas 粒子网络 (60 个粒子 / 4 种立场色 / 鼠标排斥 / 邻近连线)
 *   · 6 张浮动论文卡装饰
 *   · 序列渐入动画 (徽章 → 标题 → 副标题 → 流程链 → CTA)
 *   · CTA 按钮带 shimmer
 *   · 点 CTA 后 0.8s 淡出 + 微缩放, 露出底层主页
 *
 * 性能注意:
 *   - canvas 仅在 useEffect 内初始化, SSR 期间不渲染
 *   - 组件卸载时取消 rAF 并解绑 mouse/resize 监听
 *   - prefers-reduced-motion 用户跳过动画 (在 globals.css 内)
 */
"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  /** 用户点 CTA 时触发, 父级在这里关闭 splash */
  onEnter: () => void;
}

/* PaperTrace 设计 token 内的四种"立场色", 与 hero / 流程链 / 网络图保持一致 */
const PARTICLE_COLORS = [
  "rgba(74,222,128,",   // accent-support 翡翠
  "rgba(255,181,71,",   // accent-conflict 琥珀
  "rgba(180,174,255,",  // brand 紫
  "rgba(125,211,252,",  // 青 (流程链"抽取"色)
] as const;

const N_PARTICLES = 60;
const CONNECT_DIST = 160;
const REPEL_DIST = 150;

export default function HeroSplash({ onEnter }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [leaving, setLeaving] = useState(false);
  const [gone, setGone] = useState(false);

  /* ----- canvas 粒子动画 ----- */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let W = 0;
    let H = 0;
    const mouse: { x: number | null; y: number | null } = { x: null, y: null };

    const resize = () => {
      W = canvas.width = window.innerWidth;
      H = canvas.height = window.innerHeight;
    };
    const onMove = (e: MouseEvent) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
    };
    const onLeave = () => {
      mouse.x = null;
      mouse.y = null;
    };
    resize();
    window.addEventListener("resize", resize);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseleave", onLeave);

    type Particle = {
      x: number; y: number; vx: number; vy: number; r: number; c: string;
    };
    const particles: Particle[] = Array.from({ length: N_PARTICLES }, (_, i) => ({
      x: Math.random() * W,
      y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.6,
      vy: (Math.random() - 0.5) * 0.6,
      r: Math.random() * 2 + 1,
      c: PARTICLE_COLORS[i % PARTICLE_COLORS.length],
    }));

    let rafId = 0;
    let active = true;

    const draw = () => {
      if (!active) return;
      ctx.clearRect(0, 0, W, H);

      for (let i = 0; i < N_PARTICLES; i++) {
        const p = particles[i];

        // 位移
        p.x += p.vx;
        p.y += p.vy;
        // 边界回绕
        if (p.x < 0) p.x = W;
        else if (p.x > W) p.x = 0;
        if (p.y < 0) p.y = H;
        else if (p.y > H) p.y = 0;

        // 鼠标排斥
        if (mouse.x !== null && mouse.y !== null) {
          const dx = p.x - mouse.x;
          const dy = p.y - mouse.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist > 0 && dist < REPEL_DIST) {
            p.vx += (dx / dist) * 0.15;
            p.vy += (dy / dist) * 0.15;
          }
        }
        // 阻尼: 防止鼠标排斥后无限加速
        p.vx *= 0.99;
        p.vy *= 0.99;

        // 粒子点
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = p.c + "0.7)";
        ctx.fill();

        // 邻近连线 (透明度按距离衰减)
        for (let j = i + 1; j < N_PARTICLES; j++) {
          const q = particles[j];
          const dx2 = p.x - q.x;
          const dy2 = p.y - q.y;
          const d = Math.sqrt(dx2 * dx2 + dy2 * dy2);
          if (d < CONNECT_DIST) {
            const alpha = (0.12 * (1 - d / CONNECT_DIST)).toFixed(3);
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(q.x, q.y);
            ctx.strokeStyle = p.c + alpha + ")";
            ctx.lineWidth = 0.8;
            ctx.stroke();
          }
        }
      }

      rafId = requestAnimationFrame(draw);
    };
    rafId = requestAnimationFrame(draw);

    return () => {
      active = false;
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseleave", onLeave);
    };
  }, []);

  /* ----- splash 显示期间锁定 body 滚动, 避免下方主页一起滚 ----- */
  useEffect(() => {
    if (gone) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [gone]);

  const handleEnter = () => {
    if (leaving) return;
    setLeaving(true);
    onEnter();
    // 与 transition duration 对齐
    setTimeout(() => setGone(true), 800);
  };

  if (gone) return null;

  return (
    <div
      role="dialog"
      aria-label="PaperTrace 启动屏"
      className="fixed inset-0 z-[100] flex flex-col items-center justify-center"
      style={{
        background: "#111428",
        opacity: leaving ? 0 : 1,
        transform: leaving ? "scale(1.05)" : "scale(1)",
        transition:
          "opacity 0.8s cubic-bezier(0.4,0,0.2,1), transform 0.8s cubic-bezier(0.4,0,0.2,1)",
        pointerEvents: leaving ? "none" : "auto",
      }}
    >
      {/* 粒子网络 canvas */}
      <canvas
        ref={canvasRef}
        aria-hidden
        className="absolute inset-0 z-0"
      />

      {/* 浮动论文卡装饰 */}
      <FloatingPapers />

      {/* 主内容 */}
      <div className="relative z-10 max-w-[800px] px-6 text-center">
        {/* 徽章 */}
        <div
          className="splash-fade-1 inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-13 tracking-wide"
          style={{
            background: "rgba(74,222,128,0.08)",
            borderColor: "rgba(74,222,128,0.20)",
            color: "#4ADE80",
          }}
        >
          <span
            className="splash-pulse-dot inline-block h-[6px] w-[6px] rounded-full"
            style={{ background: "#4ADE80" }}
          />
          research tool · v0.1
        </div>

        {/* 主标题 */}
        <h1
          className="splash-fade-2 mt-8 font-bold leading-[1.15] tracking-tight"
          style={{ fontSize: "clamp(40px, 6vw, 72px)" }}
        >
          发现学术论文之间
          <span className="mt-1 block">
            真正的<span className="text-accent-conflict">争论</span>
            <span className="text-text-muted">、</span>
            <span className="text-accent-support">共识</span>
            <span className="text-text-muted"> 与</span>
            <span className="text-brand">分歧</span>
          </span>
        </h1>

        {/* 副标题 */}
        <p
          className="splash-fade-3 mx-auto mt-6 max-w-[560px] leading-[1.7] text-text-secondary"
          style={{ fontSize: "clamp(15px, 2vw, 18px)" }}
        >
          输入一个研究问题, 自动检索论文、抽取核心主张、识别彼此之间的
          <span className="text-accent-conflict"> 矛盾 </span>与
          <span className="text-accent-support"> 共识 </span>,
          并生成可追溯引用的<span className="text-brand"> 综述段落</span>。
        </p>

        {/* 流程链 */}
        <div className="splash-fade-4 mt-9 flex items-center justify-center gap-3">
          <FlowStep color="#4ADE80" label="检索" />
          <Arrow />
          <FlowStep color="#FFB547" label="抽取" />
          <Arrow />
          <FlowStep color="#B4AEFF" label="判定" />
          <Arrow />
          <FlowStep color="#7DD3FC" label="综述" />
        </div>

        {/* CTA */}
        <div className="splash-fade-5 mt-12">
          <button
            type="button"
            onClick={handleEnter}
            className="group relative overflow-hidden rounded-xl px-12 py-4 font-semibold transition-all hover:-translate-y-0.5"
            style={{
              fontSize: 17,
              color: "#0b0e14",
              background: "linear-gradient(135deg, #4ADE80, #22c55e)",
              boxShadow: "0 4px 16px rgba(74,222,128,0.25)",
              letterSpacing: "0.3px",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.boxShadow =
                "0 8px 32px rgba(74,222,128,0.4)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.boxShadow =
                "0 4px 16px rgba(74,222,128,0.25)";
            }}
          >
            <span className="splash-shimmer-bar" aria-hidden />
            <span className="relative z-[1]">开始使用 PaperTrace →</span>
          </button>
        </div>
      </div>
    </div>
  );
}

/* ============== 子组件 ============== */

function FlowStep({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5 text-14 text-text-secondary">
      <span
        aria-hidden
        className="h-2 w-2 rounded-full"
        style={{ background: color }}
      />
      {label}
    </span>
  );
}

function Arrow() {
  return (
    <span aria-hidden className="text-12" style={{ color: "#3a4055" }}>
      →
    </span>
  );
}

interface PaperConf {
  width: number;
  height: number;
  top?: string;
  bottom?: string;
  left?: string;
  right?: string;
  rot: number;
  dy: number;
  delay: number;
  dur: number;
  lines: number[]; // 每条横线宽度的百分比
}

const PAPERS: PaperConf[] = [
  { width: 100, height: 70, top: "12%", left: "8%",   rot: -6, dy: -15, delay: 0,   dur: 6,   lines: [60, 80, 40] },
  { width: 80,  height: 60, top: "18%", right: "10%", rot:  4, dy: -12, delay: 1,   dur: 7,   lines: [70, 50] },
  { width: 110, height: 75, bottom: "20%", left: "12%", rot: 3, dy: -18, delay: 0.5, dur: 8, lines: [50, 75, 60, 30] },
  { width: 90,  height: 55, bottom: "15%", right: "8%", rot: -5, dy: -10, delay: 2, dur: 5.5, lines: [65, 45] },
  { width: 70,  height: 50, top: "40%", left: "3%",   rot:  2, dy: -8,  delay: 1.5, dur: 6.5, lines: [55, 70] },
  { width: 85,  height: 60, top: "35%", right: "4%",  rot: -3, dy: -14, delay: 0.8, dur: 7.5, lines: [60, 80, 35] },
];

function FloatingPapers() {
  return (
    <>
      {PAPERS.map((p, idx) => (
        <div
          key={idx}
          aria-hidden
          className="splash-paper absolute z-0 rounded-sm border backdrop-blur-[2px]"
          style={{
            width: p.width,
            height: p.height,
            top: p.top,
            bottom: p.bottom,
            left: p.left,
            right: p.right,
            background: "rgba(26,30,58,0.55)",
            borderColor: "rgba(36,40,69,1)",
            // CSS 变量驱动 keyframe
            ["--rot" as string]: `${p.rot}deg`,
            ["--dy" as string]: `${p.dy}px`,
            ["--delay" as string]: `${p.delay}s`,
            ["--dur" as string]: `${p.dur}s`,
            transform: `rotate(${p.rot}deg)`, // 初始角度
          }}
        >
          {p.lines.map((w, i) => (
            <div
              key={i}
              className="mx-2 rounded-[2px]"
              style={{
                marginTop: i === 0 ? 12 : 6,
                height: 3,
                width: `${w}%`,
                background: "rgba(255,255,255,0.06)",
              }}
            />
          ))}
        </div>
      ))}
    </>
  );
}
