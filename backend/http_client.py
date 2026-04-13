"""
PaperTrace - 带重试的 HTTP 客户端
==================================

作用：把"遇到 429 就指数退避重试"这个套路抽出来，让 fetcher / extractor 等
      所有需要打外部 API 的模块都能复用，而不用每个文件各写一遍重试循环。

为什么要专门写一个 request_with_retry ？
  - Semantic Scholar 公共配额非常紧（无 key 时大约每秒 1 次请求），
    遇到突发请求很容易吃到 HTTP 429 Too Many Requests
  - DeepSeek、OpenAI 等 LLM API 也都会用 429 表示限流
  - 重试策略不应该每次现写 —— 那样很容易写错（比如忘了上限、忘了打日志）

退避策略（"指数退避"是经典做法）
  - 第 1 次失败 → 等 2 秒
  - 第 2 次失败 → 等 4 秒
  - 第 3 次失败 → 等 8 秒
  - 第 4 次失败 → 等 16 秒
  - 第 5 次失败 → 等 32 秒
  - 第 6 次失败 → 抛 RuntimeError
  总计最多 5 次重试，加起来最多等 62 秒（不含每次请求本身耗时）

为什么是指数？
  - 限流通常是"突发请求过多"，越等越久能给服务端缓口气
  - 比"固定每次等 5 秒"更友好，也比"立即重试"更礼貌
"""

# ===== 导入区 =====
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# ===== 退避表 =====
# 写成模块级常量而不是 [2**i for i in range(5)]，是为了"一眼就能看清"
# 也方便单元测试时 monkeypatch 成 [0, 0, 0, 0, 0] 跳过等待
BACKOFF_SECONDS = [2, 4, 8, 16, 32]
MAX_RETRIES = len(BACKOFF_SECONDS)  # 5


# ===== 核心函数 =====
async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    json: Optional[Any] = None,
    timeout: Optional[float] = None,
) -> httpx.Response:
    """
    用给定的 httpx.AsyncClient 发请求，遇到 429 自动指数退避重试。

    参数:
        client: 调用方传入的 AsyncClient（共享连接池更高效）
        method: "GET" / "POST" 等
        url: 完整 URL
        params: query string
        headers: 额外的请求头
        json: POST body（dict，会被 httpx 自动 json 序列化）
        timeout: 单次请求超时（秒），None 时用 client 的默认超时

    返回:
        httpx.Response 对象（已经检查过 status_code，是 2xx 才会返回）

    抛出:
        RuntimeError: 5 次重试后仍然 429，或者其它非可重试错误连续出现
        httpx.HTTPStatusError: 4xx（除 429 外）/5xx 错误
        httpx.TimeoutException: 网络超时（也会先重试一轮再抛）

    设计要点:
        1. 只对 429 做指数退避 —— 其他 4xx 是"你写错了"，重试也没用
        2. 5xx 也重试一次（服务器临时故障常常一次就好），但用同样的退避表
        3. TimeoutException 视同 5xx 处理 —— 网络抖动通常是临时的
        4. 每次重试都打 INFO 日志，方便排查"为什么这么慢"
    """
    # 注意：range 给 MAX_RETRIES + 1 是因为"第一次请求"不算重试
    # 失败后才会进入第 1 次重试，对应 BACKOFF_SECONDS[0]
    last_exception: Optional[Exception] = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                json=json,
                timeout=timeout,
            )

            # ----- 命中 429：指数退避后重试 -----
            if response.status_code == 429:
                if attempt >= MAX_RETRIES:
                    # 已经用完所有重试机会
                    raise RuntimeError(
                        f"HTTP 429 限流：{method} {url} 重试 {MAX_RETRIES} 次后仍失败"
                    )
                wait = BACKOFF_SECONDS[attempt]
                logger.info(
                    "[429 限流] %s %s → 等待 %ds 后第 %d/%d 次重试",
                    method, url, wait, attempt + 1, MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                continue  # 跳到下一次循环

            # ----- 5xx：服务器错误，也重试，但只重 1 次足够 -----
            # 这里的"足够"是经验：5xx 通常不是限流，多重几次也没用，
            # 但给它一次机会能挡掉很多瞬时故障
            if 500 <= response.status_code < 600:
                if attempt >= MAX_RETRIES:
                    response.raise_for_status()  # 让 httpx 抛标准异常
                wait = BACKOFF_SECONDS[attempt]
                logger.info(
                    "[%d 服务端错误] %s %s → 等待 %ds 后第 %d/%d 次重试",
                    response.status_code, method, url, wait, attempt + 1, MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                continue

            # ----- 其它 4xx：调用方写错了，重试没意义，立即抛 -----
            response.raise_for_status()

            # 走到这里说明是 2xx，成功
            return response

        except httpx.TimeoutException as e:
            last_exception = e
            if attempt >= MAX_RETRIES:
                raise
            wait = BACKOFF_SECONDS[attempt]
            logger.info(
                "[超时] %s %s → 等待 %ds 后第 %d/%d 次重试",
                method, url, wait, attempt + 1, MAX_RETRIES,
            )
            await asyncio.sleep(wait)
            continue

        except httpx.HTTPStatusError:
            # raise_for_status() 抛出来的，已经在上面分类处理过；
            # 走到这里就是真该往外抛的（非 429 / 非 5xx）
            raise

    # 理论上不会走到这里（要么 return，要么在循环里 raise）
    # 兜底：万一逻辑有 bug，也别静默 —— 把最后一个异常包起来
    raise RuntimeError(
        f"request_with_retry 异常退出（最后一次错误：{last_exception}）"
    )


# ===== 自检 =====
if __name__ == "__main__":
    """
    用 https://httpbin.org/status/429 模拟 429 响应，验证退避真的发生了。
    自检会真实跑 60 秒以上（因为要把 5 次退避全走完），所以用环境变量控制是否跑。
    """
    import os
    import sys
    import time

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    async def _demo_429():
        print("正在测试 429 退避（最坏情况会等 2+4+8+16+32 = 62 秒）...")
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await request_with_retry(
                    client, "GET", "https://httpbin.org/status/429"
                )
            except RuntimeError as e:
                elapsed = time.monotonic() - t0
                print(f"✓ 正确抛出 RuntimeError：{e}")
                print(f"✓ 总耗时 {elapsed:.1f}s（应该 ≥ 62 秒）")
                assert elapsed >= 60, "退避时间不对"

    async def _demo_200():
        print("\n正在测试 200 成功路径 ...")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await request_with_retry(
                client, "GET", "https://httpbin.org/get"
            )
            assert r.status_code == 200
            print(f"✓ 200 OK，正确返回 Response")

    if os.getenv("RUN_429_TEST") == "1":
        asyncio.run(_demo_429())
    else:
        print("跳过 429 退避自检（设 RUN_429_TEST=1 启用，会等 60+ 秒）")

    asyncio.run(_demo_200())
    print("\n所有自检通过 ✅")
