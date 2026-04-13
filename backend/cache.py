"""
PaperTrace - 本地 JSON 文件缓存
================================

作用：把"昂贵或受限的外部调用结果"存到磁盘，下次同样的请求直接读文件，
      避免重复打 Semantic Scholar / DeepSeek 这种按次限流（甚至按次收费）的接口。

为什么不用 Redis / SQLite ？
  - MVP 阶段不想多养一个进程
  - 调试时方便：缓存就是普通 JSON 文件，cat 一下就能看
  - 跨进程安全：每个 key 一个独立文件，没有锁竞争

缓存键的设计
  - 输入是任意可序列化的 dict（比如 {"query": "remote work", "limit": 20}）
  - 把它做 JSON 规范化（sort_keys + 紧凑分隔符）后取 MD5
  - 拼上 prefix 和 .json 后缀，得到形如 cache/ss_search_3a2f....json 的文件名
  - prefix 的作用是分类命名空间，避免不同模块的缓存撞到一起

使用方式
  >>> from cache import get_cache, set_cache
  >>> key_content = {"query": "remote work", "limit": 20}
  >>> cached = get_cache("ss_search", key_content)
  >>> if cached is not None:
  ...     return cached
  >>> data = expensive_api_call(...)
  >>> set_cache("ss_search", key_content, data)
  >>> return data
"""

# ===== 导入区 =====
from __future__ import annotations

import hashlib                  # 算 MD5
import json                     # 序列化缓存键和缓存值
import logging                  # 用 logging 比 print 更适合工具模块
from datetime import datetime, timezone   # 给缓存盖时间戳 / 算过期
from pathlib import Path        # 跨平台拼路径
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ===== 缓存信封格式（envelope）=====
# 旧版本直接把 data 写进文件；新版本统一包一层信封：
#   {
#     "cached_at": "2026-04-12T10:00:00+00:00",  # ISO 8601 带时区
#     "data": <原始数据>
#   }
# 这样既能记录"什么时候缓存的"，又能在读出来时算"是不是过期了"。
#
# 为什么要带时区？
#   "2026-04-12T10:00:00" 这种 naive 字符串歧义大（本地时间还是 UTC？）。
#   我们统一存 UTC，前端再按用户本地时区展示，跨时区也不会乱。


# ===== 缓存目录 =====
# 用 __file__ 锚定到 backend/cache，避免"在哪个目录运行就在哪建缓存"的混乱
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
# parents=True：父目录不存在也一起建；exist_ok=True：已存在不报错
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ===== 内部工具：把任意 dict 变成稳定的缓存文件名 =====
def _make_filename(prefix: str, content: Any) -> Path:
    """
    根据 prefix + content 生成一个稳定的缓存文件路径。

    "稳定"的意思是：
      - 同样的 content（即使 dict 字段顺序不同）必须产生同样的文件名
      - 不同的 content 必须产生不同的文件名
      - 文件名只用安全字符（MD5 是 32 个十六进制字符，肯定安全）
    """
    # sort_keys=True 让 {"a":1,"b":2} 和 {"b":2,"a":1} 序列化成完全相同的字符串
    # ensure_ascii=False：非 ASCII 字符（比如中文 query）按 UTF-8 编码，更紧凑
    # separators=(",", ":")：去掉所有空格，让序列化结果完全确定
    canonical = json.dumps(
        content,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # MD5 用来做文件名足够（这里不是密码学场景，不在意碰撞攻击）
    digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{prefix}_{digest}.json"


# ===== 公开 API =====
def get_cache(
    prefix: str,
    content: Any,
    max_age_hours: float = 72.0,
) -> Optional[tuple[Any, str]]:
    """
    查缓存。命中且未过期返回 (data, cached_at)，否则返回 None。

    参数:
        prefix: 命名空间，比如 "papers_search"、"deepseek_extract"
        content: 用来构造缓存键的任意可 JSON 序列化对象
        max_age_hours: 缓存过期时间（小时），默认 72 小时（3 天）。
                       传 float('inf') 可以禁用过期检查。

    返回:
        - 缓存命中且未过期 → (data, cached_at_iso)
            - data 是当初 set_cache 存进去的那个对象
            - cached_at_iso 是 ISO 8601 时间字符串（UTC 时区）
        - 缓存未命中 / 已过期 / 读取失败 / 旧版无信封 → None

    设计说明:
        返回元组而不是单个 data，是为了让上层既能用数据，
        又能告诉用户"这份数据是 XX 时间获取的"。
        如果只需要数据，写 `data, _ = get_cache(...)` 即可。
    """
    path = _make_filename(prefix, content)
    if not path.exists():
        return None

    try:
        # 用 utf-8 显式打开，避免 Windows 默认 GBK 把中文读乱
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        # 缓存文件坏了（比如上次写了一半被 Ctrl+C 杀掉）→ 当作未命中
        # 不抛异常的原因：缓存只是优化，不应该让坏缓存把整个流程搞挂
        logger.warning("读取缓存失败 %s：%s（按未命中处理）", path.name, e)
        return None

    # ===== 信封校验 =====
    # 新版统一格式：{"cached_at": "...", "data": ...}
    # 旧版直接是裸 data，没有 cached_at —— 一律按未命中处理
    # （这样旧缓存会自然失效，下一次写入就是新格式）
    if not (isinstance(raw, dict) and "cached_at" in raw and "data" in raw):
        logger.info("缓存 %s 不是新格式（无信封），按未命中处理", path.name)
        return None

    cached_at_str: str = raw["cached_at"]
    data = raw["data"]

    # ===== 过期检查 =====
    try:
        cached_at_dt = datetime.fromisoformat(cached_at_str)
    except ValueError:
        logger.warning("缓存 %s 的 cached_at 格式不合法：%r", path.name, cached_at_str)
        return None

    # 如果 cached_at 是 naive（旧数据），补一个 UTC，防止跟 now 减运算时报错
    if cached_at_dt.tzinfo is None:
        cached_at_dt = cached_at_dt.replace(tzinfo=timezone.utc)

    age_hours = (datetime.now(timezone.utc) - cached_at_dt).total_seconds() / 3600.0
    if age_hours > max_age_hours:
        logger.info(
            "缓存 %s 已过期（%.1fh > %.1fh），按未命中处理",
            path.name, age_hours, max_age_hours,
        )
        return None

    logger.debug("缓存命中：%s（%.1fh 前）", path.name, age_hours)
    return data, cached_at_str


def set_cache(prefix: str, content: Any, data: Any) -> str:
    """
    把 data 写入缓存。

    参数:
        prefix: 和 get_cache 用同一个值
        content: 和 get_cache 用同一个值（决定文件名）
        data: 要存的内容，必须能被 json.dumps 序列化

    返回:
        cached_at 的 ISO 字符串（UTC）。这样调用方可以立即拿到时间戳，
        不用为了知道"刚刚存的时间"而再 get_cache 一次。

    写入策略：
        - 先写到 .tmp 文件，再 rename 成正式文件名
        - 这是"原子写入"惯用法：进程被强杀也不会留下"写到一半的"坏缓存
        - 写入信封 {cached_at, data}，便于以后做过期检查
    """
    path = _make_filename(prefix, content)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    cached_at = datetime.now(timezone.utc).isoformat()
    envelope = {"cached_at": cached_at, "data": data}

    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            # ensure_ascii=False：中文按原文存，文件可读性更好
            # indent=2：人眼调试友好；如果你在意磁盘空间可以去掉
            json.dump(envelope, f, ensure_ascii=False, indent=2)
        # rename 在大多数文件系统（包括 NTFS、ext4、APFS）上是原子的
        tmp_path.replace(path)
        logger.debug("写入缓存：%s", path.name)
    except (OSError, TypeError) as e:
        # TypeError = data 里有不可序列化的对象（比如 datetime、set）
        # 这种情况"缓存失败"也不应该影响主流程，所以只打警告
        logger.warning("写入缓存失败 %s：%s（已跳过缓存）", path.name, e)
        # 清理可能残留的临时文件
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return cached_at


# ===== 直接运行时的自检 =====
if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    print(f"缓存目录：{CACHE_DIR}")

    # 1. 第一次查应该是 None
    key = {"query": "remote work productivity", "limit": 5}
    assert get_cache("ss_search_test", key) is None
    print("✓ 未命中正确返回 None")

    # 2. 写一份，set_cache 会返回 cached_at
    fake_data = {"papers": [{"id": 1, "title": "测试论文"}]}
    cached_at = set_cache("ss_search_test", key, fake_data)
    print(f"✓ 写入成功，cached_at={cached_at}")

    # 3. 再查应该命中，返回 (data, cached_at) 元组
    hit = get_cache("ss_search_test", key)
    assert hit is not None, "应该命中"
    data, ts = hit
    assert data == fake_data, f"读出来的内容不一致：{data}"
    assert ts == cached_at, f"cached_at 不一致：{ts} vs {cached_at}"
    print("✓ 命中并取回原数据 + 时间戳")

    # 4. 字段顺序不同应该命中同一个文件
    key2 = {"limit": 5, "query": "remote work productivity"}
    hit2 = get_cache("ss_search_test", key2)
    assert hit2 is not None and hit2[0] == fake_data
    print("✓ 字段顺序不同也能命中（sort_keys 生效）")

    # 5. 内容稍有差别应该未命中
    key3 = {"query": "remote work productivity", "limit": 6}
    assert get_cache("ss_search_test", key3) is None
    print("✓ 内容不同正确未命中")

    # 6. 过期检查：max_age_hours=0 应该让所有缓存都"瞬间过期"
    assert get_cache("ss_search_test", key, max_age_hours=0) is None
    print("✓ max_age_hours=0 正确判定过期")

    # 7. 清理测试文件
    test_file = _make_filename("ss_search_test", key)
    test_file.unlink(missing_ok=True)
    print(f"✓ 清理测试文件 {test_file.name}")

    print("\n所有自检通过 ✅")
