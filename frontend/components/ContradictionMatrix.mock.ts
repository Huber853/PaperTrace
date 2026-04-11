/**
 * ContradictionMatrix 的样例数据
 * ===================================
 * 用于 /preview/matrix 路由，无需后端即可预览组件视觉效果。
 *
 * 设计：7 条 claim，覆盖 3 个不同主题，让矩阵有：
 *   - 明显的 support 簇（同主题同方向）
 *   - 明显的 contradict 对（同主题反方向）
 *   - 大片 unrelated（跨主题）
 * 可以直接搬到答辩演示里当"脱网兜底"。
 */

import type { Claim, Relation } from "@/lib/api";

export const MOCK_CLAIMS: Claim[] = [
  // === 主题 1：remote work productivity ===
  {
    id: 1,
    paper_id: 1,
    subject: "software engineers",
    intervention: "fully remote work",
    conclusion: "self-rated productivity increased by 13%",
    direction: "positive",
  },
  {
    id: 2,
    paper_id: 2,
    subject: "knowledge workers",
    intervention: "remote work arrangements",
    conclusion: "measured output improved by 17% vs in-office baseline",
    direction: "positive",
  },
  {
    id: 3,
    paper_id: 3,
    subject: "software engineers",
    intervention: "fully remote work",
    conclusion: "task completion rates dropped 8% over 6 months",
    direction: "negative",
  },
  // === 主题 2：sleep deprivation ===
  {
    id: 4,
    paper_id: 4,
    subject: "college students",
    intervention: "sleep deprivation > 24h",
    conclusion: "working memory performance declined significantly",
    direction: "negative",
  },
  {
    id: 5,
    paper_id: 5,
    subject: "adults",
    intervention: "chronic sleep restriction (<6h/night)",
    conclusion: "reaction times slowed by 18% across tasks",
    direction: "negative",
  },
  // === 主题 3：caffeine on cognition ===
  {
    id: 6,
    paper_id: 6,
    subject: "healthy adults",
    intervention: "200mg caffeine before task",
    conclusion: "sustained attention improved modestly",
    direction: "positive",
  },
  {
    id: 7,
    paper_id: 7,
    subject: "healthy adults",
    intervention: "200mg caffeine before task",
    conclusion: "no significant improvement on complex problem solving",
    direction: "neutral",
  },
];

/** 辅助：生成一个对称格子的矩阵 */
function makeMatrix(
  n: number,
  getCell: (i: number, j: number) => Relation
): Relation[][] {
  const m: Relation[][] = [];
  for (let i = 0; i < n; i++) {
    const row: Relation[] = [];
    for (let j = 0; j < n; j++) {
      if (i === j) {
        row.push({
          relation: "support",
          confidence: 1.0,
          reason: "Self-comparison.",
        });
      } else if (j < i) {
        row.push(m[j][i]); // 镜像
      } else {
        row.push(getCell(i, j));
      }
    }
    m.push(row);
  }
  return m;
}

// 手工编排矩阵内容，让每种关系都有代表性的格子
export const MOCK_MATRIX: Relation[][] = makeMatrix(MOCK_CLAIMS.length, (i, j) => {
  // claim 0,1 都是 remote work 正向 → support
  if ((i === 0 && j === 1) || (i === 1 && j === 0)) {
    return {
      relation: "support",
      confidence: 0.85,
      reason: "Both report positive productivity effects of remote work.",
    };
  }
  // claim 0 vs 2：remote work 正 vs 负 → contradict（主力亮点）
  if (i === 0 && j === 2) {
    return {
      relation: "contradict",
      confidence: 0.93,
      reason: "Same subject and intervention but opposite productivity effects.",
    };
  }
  // claim 1 vs 2：同样 remote work 正 vs 负 → contradict
  if (i === 1 && j === 2) {
    return {
      relation: "contradict",
      confidence: 0.88,
      reason: "Positive output gains vs measured drop in completion rates.",
    };
  }
  // claim 3 vs 4：两条 sleep 负向 → support
  if (i === 3 && j === 4) {
    return {
      relation: "support",
      confidence: 0.82,
      reason: "Both demonstrate negative cognitive effects of insufficient sleep.",
    };
  }
  // claim 5 vs 6：caffeine 正 vs 中性 → 弱 contradict
  if (i === 5 && j === 6) {
    return {
      relation: "contradict",
      confidence: 0.55,
      reason: "One finds attention improvement, the other finds no effect on reasoning.",
    };
  }
  // 其余跨主题：unrelated
  return {
    relation: "unrelated",
    confidence: 0.95,
    reason: "Different subjects and interventions; not comparable.",
  };
});

export const MOCK_PAPER_TITLES: Record<number, string> = {
  1: "A Longitudinal Study of Fully Remote Software Engineers",
  2: "Output Metrics of Knowledge Workers During the Pandemic",
  3: "Six-Month Productivity Decline in Remote Engineering Teams",
  4: "Cognitive Consequences of Acute Sleep Deprivation in Students",
  5: "Chronic Sleep Restriction and Reaction Time",
  6: "Caffeine and Sustained Attention: A Meta-Analysis",
  7: "Caffeine Does Not Improve Complex Problem Solving",
};
