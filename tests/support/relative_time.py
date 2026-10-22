"""统计类用例的共享时间工厂。

统计端点与 CLI 统计命令都在服务端用真实时钟换算查询窗口，测试没有 ``now`` 注入点，
写死绝对时间会让 ``days`` 查询随日期推进把种子数据挤出窗口（2026-10-21 起曾有
三条端点用例因此每天必红），所以这类用例统一用「相对真实时钟多久以前」构造时刻。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    """返回当前 UTC 时刻，去掉微秒以便与 SQLite 文本时间戳逐字符比较。"""
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_minutes_ago(minutes: int = 5) -> datetime:
    """返回相对真实时钟往前推若干分钟的时刻。"""
    return utc_now() - timedelta(minutes=minutes)


def utc_days_ago(days: int = 1) -> datetime:
    """返回相对真实时钟往前推若干天的时刻。"""
    return utc_now() - timedelta(days=days)
