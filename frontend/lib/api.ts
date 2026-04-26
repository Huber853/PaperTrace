/**
 * PaperTrace 前端 API 封装
 * ===========================
 *
 * 作用：把"调后端 HTTP 接口"统一收口到这里。
 * 好处：
 *   1) 后端地址只在一处配置，部署时改环境变量就行
 *   2) 所有请求共享同一个 axios 实例（共用拦截器、超时配置）
 *   3) TypeScript 类型集中管理，每个 API 的入参/返回都能被 IDE 自动补全
 *   4) 出错处理只写一次，每个页面拿到的都是友好的错误对象
 *
 * 为什么用 axios 而不是 fetch？
 *   - axios 自动 JSON 序列化/反序列化（fetch 要手写 .json()）
 *   - axios 有更友好的错误处理（4xx/5xx 默认 throw，fetch 不会）
 *   - 拦截器机制方便统一加 header / 日志 / 重试
 */

import axios, { AxiosError, AxiosInstance } from "axios";

// ============== 类型定义（和后端 Pydantic 模型保持一致）==============

/** 后端任务的四种状态 */
export type TaskStatus = "pending" | "running" | "done" | "failed";

/** 关系矩阵中的一个格子 */
export interface Relation {
  relation: "support" | "contradict" | "unrelated";
  confidence: number;
  reason: string;
}

/** 一篇论文 */
export interface Paper {
  id: number;
  paper_id: string;
  title: string;
  abstract: string;
  year: number | null;
  authors: string[];
  citation_count: number;
  doi: string | null;
  url: string | null;
  source: string | null;
}

/** 一条主张 */
export interface Claim {
  id: number;
  paper_id: number;
  subject: string;
  intervention: string;
  conclusion: string;
  direction: "positive" | "negative" | "neutral";
}

/** 时间轴上的一个年份聚合点 */
export interface TimelinePoint {
  year: number;
  positive: number;
  negative: number;
  neutral: number;
  total: number;
}

/** 完整分析结果 */
export interface AnalysisResult {
  task_id: string;
  query: string;
  papers: Paper[];
  claims: Claim[];
  matrix: Relation[][]; // N × N，顺序与 claims 数组一致
  stats: {
    papers_count: number;
    claims_count: number;
    contradict_pairs: number;
    support_pairs: number;
  };
  /** 按年份聚合的观点演化数据 */
  timeline: TimelinePoint[];
  /** ISO 8601 时间戳：这批论文最初是何时从 OpenAlex/arXiv 拉到的 */
  data_fetched_at: string;
}

/** POST /api/analyze 的响应 */
export interface AnalyzeResponse {
  task_id: string;
  status: TaskStatus;
  message: string;
}

/** GET /api/task/{id} 的响应 */
export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  progress: string;
  error: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /api/review/{id} 的响应 */
export interface ReviewResponse {
  task_id: string;
  review: string;
  cached: boolean;
  /** LLM 生成耗时 (毫秒) · 缓存命中时是首次生成时记录的值 */
  elapsed_ms: number;
  /** prompt token 数 */
  input_tokens: number;
  /** completion token 数 */
  output_tokens: number;
  /** 实际使用的模型名 */
  model: string;
}

// ============== 统一 axios 实例 ==============

// NEXT_PUBLIC_ 前缀的环境变量会被打包到浏览器侧（注意：会暴露给用户，不要放密钥）
// 部署到 Vercel 时改这个变量即可指向 Render 后端
const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const http: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 30_000, // 30 秒超时（注意：这是 HTTP 请求超时，不是后台任务超时）
  headers: {
    "Content-Type": "application/json",
  },
});

// 响应拦截器：把 axios 的错误对象统一翻译成更易读的 Error
http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: string }>) => {
    // 后端用 FastAPI HTTPException 抛错时，返回体长这样：{"detail": "..."}
    const detail = error.response?.data?.detail;
    const status = error.response?.status;
    const message = detail
      ? `[${status}] ${detail}`
      : error.message || "网络请求失败";
    return Promise.reject(new Error(message));
  }
);

// ============== API 调用函数 ==============

/**
 * 提交一次分析任务。
 * refresh=true 时后端会跳过缓存,强制重新拉外部数据。
 */
export async function startAnalysis(
  query: string,
  limit = 20,
  refresh = false
): Promise<AnalyzeResponse> {
  const { data } = await http.post<AnalyzeResponse>("/api/analyze", {
    query,
    limit,
    refresh,
  });
  return data;
}

/** 查询任务当前状态 */
export async function getTaskStatus(
  taskId: string
): Promise<TaskStatusResponse> {
  const { data } = await http.get<TaskStatusResponse>(`/api/task/${taskId}`);
  return data;
}

/** 获取已完成任务的完整结果 */
export async function getResult(taskId: string): Promise<AnalysisResult> {
  const { data } = await http.get<AnalysisResult>(`/api/result/${taskId}`);
  return data;
}

/**
 * 生成/获取综述段落
 * ------
 * 后端会缓存结果：第一次调用触发 LLM 生成，之后返回 cached=true。
 * 因为走 LLM，耗时可能到 10-20 秒，所以下面额外给这个请求 90 秒超时。
 */
export async function getReview(taskId: string): Promise<ReviewResponse> {
  const { data } = await http.get<ReviewResponse>(
    `/api/review/${taskId}`,
    { timeout: 90_000 }
  );
  return data;
}

// ============== /api/chat 通用 DeepSeek 代理 ==============

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatOptions {
  /** 默认 0.5, 取值 0-2 */
  temperature?: number;
  /** { type: "json_object" } 强制 DeepSeek 返回合法 JSON */
  response_format?: { type: "json_object" };
  /** 请求超时 (毫秒), 默认 30_000 */
  timeoutMs?: number;
  /** 调用方传入的 AbortSignal, 用来配合页面卸载时中止 */
  signal?: AbortSignal;
}

export interface ChatResponseBody {
  content: string;
  elapsed_ms: number;
  input_tokens: number;
  output_tokens: number;
  model: string;
}

/**
 * 通用 DeepSeek 代理调用。
 * 后端 POST /api/chat 透传给 DeepSeek,API key 只留在服务器端。
 */
export async function chatCompletion(
  messages: ChatMessage[],
  options: ChatOptions = {}
): Promise<ChatResponseBody> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? 30_000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // 把外部 signal 也桥接进来
  if (options.signal) {
    if (options.signal.aborted) controller.abort();
    else options.signal.addEventListener("abort", () => controller.abort());
  }

  try {
    const { data } = await http.post<ChatResponseBody>(
      "/api/chat",
      {
        messages,
        temperature: options.temperature ?? 0.5,
        response_format: options.response_format,
      },
      { signal: controller.signal, timeout: timeoutMs }
    );
    return data;
  } finally {
    clearTimeout(timer);
  }
}

/** 支持的导出格式 */
export type ExportFormat = "md" | "bib";

/** 导出时可选附加给后端的内容
 *  (用于把前端独有的研究方向建议一并写入 markdown 报告) */
export interface ExportExtras {
  /** 研究方向建议 (RecommendationPanel 通过 /api/chat 生成, 后端原本看不到) */
  recommendations?: {
    questions: Array<{
      title: string;
      desc: string;
      sources: string[];
      priority: "high" | "medium" | "low";
    }>;
    methods: Array<{
      title: string;
      desc: string;
      sources: string[];
      priority: "high" | "medium" | "low";
    }>;
  } | null;
}

export async function exportReport(
  taskId: string,
  format: ExportFormat = "md",
  extras: ExportExtras = {}
): Promise<{ blob: Blob; filename: string }> {
  // 改用 POST: 通过请求体把 recommendations 传给后端,
  // 后端拿到后会把"研究方向建议"段写进 markdown 报告.
  const resp = await http.post(
    `/api/export/${taskId}`,
    { recommendations: extras.recommendations ?? null },
    {
      params: { format },
      responseType: "blob",
    }
  );
  const cd: string = resp.headers["content-disposition"] || "";
  const fallbackExt = format === "bib" ? "bib" : "md";
  let filename = `PaperTrace_${taskId}.${fallbackExt}`;
  const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (m) {
    try { filename = decodeURIComponent(m[1]); } catch {}
  }
  return { blob: resp.data as Blob, filename };
}
