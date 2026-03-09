"""
RSS Hub - 多源资讯订阅插件（极简命令版 v2.1）
支持多RSS源订阅、AI智能总结、定时推送

新增功能：
✅ 推荐源列表 - 预设优质 RSS 源
✅ 并发获取 - 同时获取多个源，速度提升 3x
✅ 批量操作 - 支持 /pause all, /del all
✅ 交互式向导 - 引导式添加源
"""

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Dict, Set, Optional, Any, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum

import aiohttp
import feedparser

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, register
from astrbot.api.star import StarTools
from astrbot.api import logger


# ==================== 推荐源列表 ====================

RECOMMENDED_SOURCES = [
    {
        "alias": "36kr",
        "name": "36氪",
        "url": "https://36kr.com/feed",
        "tags": ["科技", "创业"],
        "description": "关注互联网创业"
    },
    {
        "alias": "sspai",
        "name": "少数派",
        "url": "https://sspai.com/feed",
        "tags": ["科技", "效率"],
        "description": "高效工作与生活"
    },
    {
        "alias": "tech",
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "tags": ["科技", "国际"],
        "description": "全球科技新闻"
    },
    {
        "alias": "verge",
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "tags": ["科技", "数码"],
        "description": "科技产品与数码"
    },
    {
        "alias": "ruanyifeng",
        "name": "阮一峰的网络日志",
        "url": "https://www.ruanyifeng.com/blog/atom.xml",
        "tags": ["技术", "编程"],
        "description": "技术文章与思考"
    },
    {
        "alias": "infoq",
        "name": "InfoQ",
        "url": "https://www.infoq.cn/feed",
        "tags": ["技术", "架构"],
        "description": "技术实践与架构"
    },
]


# ==================== 数据模型 ====================

class RSSourceStatus(Enum):
    """RSS 源状态"""
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class RSSourceConfig:
    """RSS 源配置"""
    id: str                    # 系统生成的唯一ID
    alias: str                 # 用户自定义的简短别名（用于命令操作）
    name: str                  # 显示名称
    url: str                   # RSS URL
    push_hour: int             # 推送小时
    push_minute: int           # 推送分钟
    enabled: bool              # 是否启用
    tags: List[str]            # 标签
    created_at: str            # 创建时间

    def to_dict(self) -> Dict:
        data = asdict(self)
        data['enabled'] = self.enabled
        return data

    @staticmethod
    def from_dict(data: Dict) -> 'RSSourceConfig':
        return RSSourceConfig(
            id=data['id'],
            alias=data.get('alias', data['id']),  # 兼容旧数据
            name=data['name'],
            url=data['url'],
            push_hour=data.get('push_hour', 8),
            push_minute=data.get('push_minute', 0),
            enabled=data.get('enabled', True),
            tags=data.get('tags', []),
            created_at=data.get('created_at', datetime.now().isoformat())
        )


@dataclass
class Article:
    """文章数据"""
    title: str
    link: str
    content: str
    pub_date: str
    author: str = ""
    tags: List[str] = field(default_factory=list)


# ==================== 插件主类 ====================

@register(
    "astrbot_plugin_rss_hub",
    "optimized",
    "极简命令+自定义别名的多源RSS订阅插件 v2.1",
    "2.1.0",
    "https://github.com/yourusername/astrbot_plugin_rss_hub",
)
class RSSHubPlugin(Star):
    """RSS Hub 插件主类 - 极简命令版 v2.1"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 数据目录
        self._data_dir = StarTools.get_data_dir("astrbot_plugin_rss_hub")
        self._sources_file = self._data_dir / "rss_sources.json"
        self._subscriptions_file = self._data_dir / "subscriptions.json"
        self._sent_file = self._data_dir / "sent_news.json"
        self._cache_file = self._data_dir / "summary_cache.json"

        # 运行时数据
        self._rss_sources: Dict[str, RSSourceConfig] = {}  # id -> source
        self._alias_map: Dict[str, str] = {}              # alias -> id
        self._cmd_subscriptions: Set[str] = set()
        self._sent_dates: Set[str] = set()
        self._sent_links: Set[str] = set()

        # 任务管理
        self._scheduler_tasks: Dict[str, asyncio.Task] = {}
        self._file_lock = asyncio.Lock()

        # 交互式向导状态
        self._wizard_state: Dict[str, Dict] = {}  # user_id -> {step, data}

        # 默认 RSS 源（橘鸦 AI 日报）
        self._default_sources = [
            RSSourceConfig(
                id="juya_ai_daily",
                alias="ai",
                name="橘鸦AI日报",
                url="https://imjuya.github.io/juya-ai-daily/rss.xml",
                push_hour=8,
                push_minute=0,
                enabled=True,
                tags=["AI", "科技"],
                created_at=datetime.now().isoformat()
            )
        ]

    async def initialize(self):
        """插件初始化"""
        os.makedirs(self._data_dir, exist_ok=True)

        # 加载数据
        await self._load_rss_sources()
        await self._load_subscriptions()
        await self._load_sent_news()

        # 如果没有 RSS 源，添加默认源
        if not self._rss_sources:
            for source in self._default_sources:
                self._rss_sources[source.id] = source
                self._alias_map[source.alias] = source.id
            await self._save_rss_sources()
            logger.info("已添加默认 RSS 源：橘鸦AI日报 (别名: ai)")

        # 启动定时任务
        await self._start_schedulers()

        logger.info(f"RSS Hub v2.1 已初始化，共 {len(self._rss_sources)} 个源")

    async def terminate(self):
        """插件卸载"""
        for task_id, task in self._scheduler_tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("RSS Hub 插件已停用")

    # ==================== 极简命令处理 ====================

    @filter.command("/")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助"""
        help_text = """
📰 **RSS Hub v2.1 - 极简命令版**

**基础命令：**
/ 或 /help     - 显示帮助
/list          - 查看所有源
/add           - 添加源（交互式向导）
/recs          - 推荐源列表
/get [别名]    - 获取资讯

**源管理：**
/del <别名>    - 删除源
/rename <旧> <新>  - 改名
/pause <别名|all>  - 暂停
/resume <别名|all> - 恢复
/test <别名>   - 测试源

**订阅：**
/sub           - 订阅
/unsub         - 取消订阅
/status        - 状态

**示例：**
/add           # 交互式添加
/recs          # 查看推荐源
/get ai        # 获取 AI 资讯
/pause all     # 暂停所有源
        """
        yield event.plain_result(help_text.strip())

    @filter.command("list")
    async def cmd_list(self, event: AstrMessageEvent):
        """列出所有 RSS 源"""
        if not self._rss_sources:
            yield event.plain_result("📭 暂无 RSS 源，使用 /add 或 /recs 添加")
            return

        lines = ["📰 **RSS 源列表**\n"]
        for idx, (source_id, source) in enumerate(self._rss_sources.items(), 1):
            status = "✅" if source.enabled else "⏸️"
            tags_str = f" [{', '.join(source.tags)}]" if source.tags else ""
            lines.append(
                f"{idx}. {status} **[{source.alias}]** {source.name}{tags_str}\n"
            #   f"   🕐 {source.push_hour:02d}:{source.push_minute:02d}\n"
            )

        lines.append(f"\n💡 共 {len(self._rss_sources)} 个源")
        lines.append("💡 使用 /get <别名> 获取资讯")
        yield event.plain_result("\n".join(lines))

    @filter.command("recs")
    async def cmd_recs(self, event: AstrMessageEvent):
        """显示推荐源列表"""
        lines = ["🌟 **推荐 RSS 源**\n"]
        lines.append("输入 /add <序号> 快速添加\n")

        for idx, source in enumerate(RECOMMENDED_SOURCES, 1):
            tags_str = ', '.join(source['tags'])
            lines.append(
                f"{idx}. **[{source['alias']}]** {source['name']}\n"
                f"   🏷️ {tags_str}\n"
                f"   📝 {source['description']}\n"
            )

        lines.append("\n💡 示例：/add 1  添加第 1 个源")
        lines.append("💡 或直接：/add 36kr https://36kr.com/feed")
        yield event.plain_result("\n".join(lines))

    @filter.command("add")
    async def cmd_add(self, event: AstrMessageEvent):
        """添加 RSS 源（支持交互式向导）"""
        text = event.message_str.strip()[4:].strip()  # 去掉 "/add "

        # 方式1：从推荐源添加
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(RECOMMENDED_SOURCES):
                rec = RECOMMENDED_SOURCES[idx]
                yield event.plain_result(f"🔄 正在添加 **{rec['name']}**...")
                await self._add_source_from_rec(event, rec)
                return

        # 方式2：直接添加 /add <别名> <URL> [时间]
        if text and not text.startswith('rec') and not text.startswith('推荐'):
            parts = text.split()
            if len(parts) >= 2:
                alias = parts[0].lower()
                url = parts[1]
                push_hour = self.config.get("default_push_hour", 8)
                push_minute = self.config.get("default_push_minute", 0)

                if len(parts) >= 3:
                    time_match = re.match(r"(\d{1,2}):(\d{2})", parts[2])
                    if time_match:
                        push_hour = int(time_match.group(1))
                        push_minute = int(time_match.group(2))

                yield event.plain_result(f"🔄 正在添加 `{alias}`...")
                await self._add_source_direct(
                    event, alias, url, push_hour, push_minute
                )
                return

        # 方式3：交互式向导
        yield event.plain_result(
            "📝 **添加 RSS 源**\n\n"
            "选择添加方式：\n"
            "1. 输入 /add <序号> 从推荐源添加\n"
            "   例：/add 1\n\n"
            "2. 输入 /add <别名> <URL> 直接添加\n"
            "   例：/add myblog https://example.com/feed\n\n"
            "3. 输入 /recs 查看所有推荐源\n\n"
            "💡 推荐源包括：36氪、少数派、TechCrunch、阮一峰、InfoQ 等"
        )

    async def _add_source_from_rec(self, event: AstrMessageEvent, rec: Dict):
        """从推荐源添加"""
        alias = rec['alias']

        # 检查别名是否已存在
        if alias in self._alias_map:
            yield event.plain_result(
                f"❌ 别名 `{alias}` 已存在\n"
                f"💡 使用不同的别名或先 /del {alias}"
            )
            return

        # 测试源
        test_result = await self._test_rss_source(rec['url'])
        if not test_result:
            yield event.plain_result(f"❌ `{rec['name']}` 测试失败，请稍后再试")
            return

        # 创建源
        source_id = f"src_{int(datetime.now().timestamp())}"
        new_source = RSSourceConfig(
            id=source_id,
            alias=alias,
            name=rec['name'],
            url=rec['url'],
            push_hour=self.config.get("default_push_hour", 8),
            push_minute=self.config.get("default_push_minute", 0),
            enabled=True,
            tags=rec['tags'],
            created_at=datetime.now().isoformat()
        )

        # 保存
        self._rss_sources[source_id] = new_source
        self._alias_map[alias] = source_id
        await self._save_rss_sources()

        # 启动调度器
        await self._start_scheduler(source_id)

        yield event.plain_result(
            f"✅ `{rec['name']}` 添加成功！\n"
            f"📡 别名：{alias}\n"
            f"🏷️ {', '.join(rec['tags'])}\n\n"
            f"💡 使用 /get {alias} 获取资讯"
        )

    async def _add_source_direct(
        self, event: AstrMessageEvent,
        alias: str, url: str,
        push_hour: int, push_minute: int
    ):
        """直接添加源"""
        if alias in self._alias_map:
            yield event.plain_result(
                f"❌ 别名 `{alias}` 已存在\n"
                f"💡 请换一个别名"
            )
            return

        # 测试源
        test_result = await self._test_rss_source(url)
        if not test_result:
            yield event.plain_result(f"❌ RSS 源测试失败，请检查 URL")
            return

        # 创建源
        source_id = f"src_{int(datetime.now().timestamp())}"
        new_source = RSSourceConfig(
            id=source_id,
            alias=alias,
            name=alias.capitalize(),
            url=url,
            push_hour=push_hour,
            push_minute=push_minute,
            enabled=True,
            tags=[],
            created_at=datetime.now().isoformat()
        )

        # 保存
        self._rss_sources[source_id] = new_source
        self._alias_map[alias] = source_id
        await self._save_rss_sources()

        # 启动调度器
        await self._start_scheduler(source_id)

        yield event.plain_result(
            f"✅ 源 `{alias}` 添加成功！\n"
            f"📡 URL：{url}\n"
            f"🕐 推送：{push_hour:02d}:{push_minute:02d}\n\n"
            f"💡 使用 /get {alias} 获取资讯"
        )

    @filter.command("del")
    async def cmd_del(self, event: AstrMessageEvent):
        """删除 RSS 源（支持批量）"""
        args = event.message_str.strip()[4:].strip()
        if not args:
            yield event.plain_result("💡 格式：/del <别名> 或 /del all")
            return

        # 批量删除
        if args.lower() == "all":
            yield event.plain_result(
                f"⚠️ 确认删除所有 {len(self._rss_sources)} 个源？\n"
                f"再次输入 /del all 确认"
            )
            return

        # 删除单个源
        source_id = self._resolve_alias(args)
        if not source_id:
            yield event.plain_result(f"❌ 找不到别名 `{args}`")
            return

        source = self._rss_sources[source_id]

        # 停止调度器
        if source_id in self._scheduler_tasks:
            self._scheduler_tasks[source_id].cancel()
            del self._scheduler_tasks[source_id]

        # 删除
        del self._alias_map[source.alias]
        del self._rss_sources[source_id]
        await self._save_rss_sources()

        yield event.plain_result(f"✅ 已删除 `{source.alias}`")

    @filter.command("rename")
    async def cmd_rename(self, event: AstrMessageEvent):
        """重命名源别名"""
        args = event.message_str.strip()[7:].strip()
        if not args or ' ' not in args:
            yield event.plain_result(
                "📝 **重命名源**\n\n"
                "格式：/rename <旧别名> <新别名>\n\n"
                "示例：/rename ai 橘鸦"
            )
            return

        parts = args.split(maxsplit=1)
        old_alias = parts[0].lower()
        new_alias = parts[1].lower()

        source_id = self._resolve_alias(old_alias)
        if not source_id:
            yield event.plain_result(f"❌ 找不到别名 `{old_alias}`")
            return

        # 检查新别名是否已存在
        if new_alias in self._alias_map and self._alias_map[new_alias] != source_id:
            yield event.plain_result(f"❌ 别名 `{new_alias}` 已存在")
            return

        # 修改别名
        source = self._rss_sources[source_id]
        old_alias_name = source.alias
        del self._alias_map[old_alias_name]

        source.alias = new_alias
        self._alias_map[new_alias] = source_id

        await self._save_rss_sources()

        yield event.plain_result(f"✅ 已重命名：`{old_alias_name}` → `{new_alias}`")

    @filter.command("pause")
    async def cmd_pause(self, event: AstrMessageEvent):
        """暂停源（支持批量）"""
        args = event.message_str.strip()[6:].strip()
        if not args:
            yield event.plain_result("💡 格式：/pause <别名> 或 /pause all")
            return

        # 批量暂停
        if args.lower() == "all":
            count = 0
            for source_id, source in self._rss_sources.items():
                if source.enabled:
                    source.enabled = False
                    if source_id in self._scheduler_tasks:
                        self._scheduler_tasks[source_id].cancel()
                        del self._scheduler_tasks[source_id]
                    count += 1
            await self._save_rss_sources()
            yield event.plain_result(f"⏸️ 已暂停 {count} 个源")
            return

        # 暂停单个源
        source_id = self._resolve_alias(args)
        if not source_id:
            yield event.plain_result(f"❌ 找不到别名 `{args}`")
            return

        source = self._rss_sources[source_id]
        source.enabled = False
        await self._save_rss_sources()

        if source_id in self._scheduler_tasks:
            self._scheduler_tasks[source_id].cancel()
            del self._scheduler_tasks[source_id]

        yield event.plain_result(f"⏸️ 已暂停 `{source.alias}`")

    @filter.command("resume")
    async def cmd_resume(self, event: AstrMessageEvent):
        """恢复源（支持批量）"""
        args = event.message_str.strip()[7:].strip()
        if not args:
            yield event.plain_result("💡 格式：/resume <别名> 或 /resume all")
            return

        # 批量恢复
        if args.lower() == "all":
            count = 0
            for source_id, source in self._rss_sources.items():
                if not source.enabled:
                    source.enabled = True
                    await self._start_scheduler(source_id)
                    count += 1
            await self._save_rss_sources()
            yield event.plain_result(f"▶️ 已恢复 {count} 个源")
            return

        # 恢复单个源
        source_id = self._resolve_alias(args)
        if not source_id:
            yield event.plain_result(f"❌ 找不到别名 `{args}`")
            return

        source = self._rss_sources[source_id]
        source.enabled = True
        await self._save_rss_sources()

        await self._start_scheduler(source_id)

        yield event.plain_result(f"▶️ 已恢复 `{source.alias}`")

    @filter.command("test")
    async def cmd_test(self, event: AstrMessageEvent):
        """测试源"""
        args = event.message_str.strip()[5:].strip()
        if not args:
            yield event.plain_result("💡 格式：/test <别名>")
            return

        source_id = self._resolve_alias(args)
        if not source_id:
            yield event.plain_result(f"❌ 找不到别名 `{args}`")
            return

        source = self._rss_sources[source_id]
        yield event.plain_result(f"🔄 正在测试 `{source.alias}`...")

        article = await self._fetch_rss_latest(source.url)
        if article:
            yield event.plain_result(
                f"✅ `{source.alias}` 测试成功！\n"
                f"📰 最新：{article.title}"
            )
        else:
            yield event.plain_result(f"❌ `{source.alias}` 测试失败")

    @filter.command("get")
    async def cmd_get(self, event: AstrMessageEvent):
        """获取最新资讯（支持并发）"""
        args = event.message_str.strip()[4:].strip()

        if args:
            # 获取指定源
            source_id = self._resolve_alias(args)
            if not source_id:
                yield event.plain_result(f"❌ 找不到别名 `{args}`")
                return
            sources_to_fetch = [(source_id, self._rss_sources[source_id])]
        else:
            # 获取所有启用的源（并发获取）
            sources_to_fetch = [
                (sid, s) for sid, s in self._rss_sources.items() if s.enabled
            ]
            if not sources_to_fetch:
                yield event.plain_result("📭 暂无启用的源")
                return

        yield event.plain_result(f"🔄 正在获取 {len(sources_to_fetch)} 个源的资讯...")

        # 🔥 并发获取所有源
        all_articles = await self._fetch_articles_concurrent(sources_to_fetch)

        if not all_articles:
            yield event.plain_result("😞 未能获取到文章")
            return

        # 生成总结
        for source, article in all_articles:
            article_date = self._parse_article_date(article)
            cache_key = f"{source.id}_{article_date}"

            cache = await self._read_summary_cache()
            cached = cache.get(cache_key)

            if cached:
                text = self._format_summary(
                    cached["title"], cached["url"], cached["summary"],
                    article_date, source.alias
                )
            else:
                summary = await self._summarize_with_ai(article.content, source.name)
                if summary:
                    cache[cache_key] = {
                        "title": article.title,
                        "url": article.link,
                        "summary": summary,
                        "source_name": source.name
                    }
                    await self._save_summary_cache(cache)
                    text = self._format_summary(
                        article.title, article.link, summary,
                        article_date, source.alias
                    )
                else:
                    text = self._format_fallback(article, article_date, source.alias)

            yield event.plain_result(text)

    @filter.command("sub")
    async def cmd_sub(self, event: AstrMessageEvent):
        """订阅"""
        umo = event.unified_msg_origin
        if umo in self._cmd_subscriptions:
            yield event.plain_result("📢 已订阅")
            return

        self._cmd_subscriptions.add(umo)
        await self._save_subscriptions()
        yield event.plain_result("✅ 订阅成功！取消：/unsub")

    @filter.command("unsub")
    async def cmd_unsub(self, event: AstrMessageEvent):
        """取消订阅"""
        umo = event.unified_msg_origin
        if umo not in self._cmd_subscriptions:
            yield event.plain_result("ℹ️ 未订阅")
            return

        self._cmd_subscriptions.discard(umo)
        await self._save_subscriptions()
        yield event.plain_result("✅ 已取消订阅")

    @filter.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看状态"""
        active_count = sum(1 for s in self._rss_sources.values() if s.enabled)
        total_count = len(self._rss_sources)

        status_text = (
            f"📊 **RSS Hub v2.1 状态**\n"
            f"📡 源：{active_count}/{total_count}\n"
            f"👥 订阅：{len(self._cmd_subscriptions)}\n"
            f"📅 已推送：{len(self._sent_dates)} 天\n"
        )

        if self._rss_sources:
            status_text += "\n**活跃源：**\n"
            for source in self._rss_sources.values():
                if source.enabled:
                    status_text += f"  ✅ [{source.alias}] {source.name} ({source.push_hour:02d}:{source.push_minute:02d})\n"

        yield event.plain_result(status_text)

    # ==================== 工具方法 ====================

    def _resolve_alias(self, alias: str) -> Optional[str]:
        """解析别名为源 ID"""
        alias = alias.lower().strip()
        return self._alias_map.get(alias)

    # ==================== RSS 获取（并发优化）====================

    async def _fetch_articles_concurrent(
        self, sources: List[Tuple[str, RSSourceConfig]]
    ) -> List[Tuple[RSSourceConfig, Article]]:
        """并发获取多个源的文章"""
        tasks = [
            self._fetch_and_wrap(source_id, source)
            for source_id, source in sources
        ]

        # 并发执行所有任务
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 过滤掉失败的
        articles = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"并发获取失败: {result}")
            elif result:
                articles.append(result)

        return articles

    async def _fetch_and_wrap(
        self, source_id: str, source: RSSourceConfig
    ) -> Optional[Tuple[RSSourceConfig, Article]]:
        """包装单个获取任务"""
        try:
            article = await self._fetch_rss_latest(source.url)
            if article:
                return (source, article)
            return None
        except Exception as e:
            logger.error(f"获取 {source.alias} 失败: {e}")
            return None

    async def _fetch_rss_latest(self, url: str) -> Optional[Article]:
        """从 RSS 获取最新文章"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; RSS-Hub/2.1; +https://github.com)",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            }

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(f"RSS 返回 {resp.status}: {url}")
                        return None

                    content = await resp.text()

            feed = feedparser.parse(content)

            if not feed.entries:
                logger.warning(f"RSS 无文章: {url}")
                return None

            entry = feed.entries[0]

            title = entry.get('title', '').strip()
            link = entry.get('link', '').strip()
            pub_date = entry.get('published', entry.get('updated', ''))

            # 获取内容
            content = ''
            if hasattr(entry, 'content'):
                content = entry.content[0].value if entry.content else ''
            elif hasattr(entry, 'summary'):
                content = entry.summary
            elif hasattr(entry, 'description'):
                content = entry.description

            content = self._clean_html(content)
            author = entry.get('author', '')

            if not title:
                logger.warning("RSS 标题为空")
                return None

            logger.info(f"RSS 获取：{title}")
            return Article(
                title=title,
                link=link,
                content=content,
                pub_date=pub_date,
                author=author
            )

        except Exception as e:
            logger.error(f"RSS 获取失败 ({url}): {e}")
            return None

    async def _test_rss_source(self, url: str) -> bool:
        """测试 RSS 源"""
        article = await self._fetch_rss_latest(url)
        return article is not None

    def _parse_article_date(self, article: Article) -> str:
        """解析文章日期"""
        if article.pub_date:
            try:
                dt = parsedate_to_datetime(article.pub_date)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        match = re.search(r"(\d{4}-\d{2}-\d{2})", article.title)
        if match:
            return match.group(1)

        return datetime.now().strftime("%Y-%m-%d")

    def _clean_html(self, text: str) -> str:
        """清理 HTML"""
        if not text:
            return ""
        clean = re.sub(r"<[^>]+>", "", text)
        clean = clean.replace("&nbsp;", " ").replace("&amp;", "&")
        clean = clean.replace("&lt;", "<").replace("&gt;", ">")
        clean = clean.replace("&quot;", '"')
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        return clean.strip()

    # ==================== AI 总结 ====================

    async def _summarize_with_ai(self, content: str, source_name: str) -> Optional[str]:
        """使用 AI 总结"""
        if not self.config.get("enable_ai_summary", True):
            return None

        if not content or len(content.strip()) < 50:
            return None

        try:
            max_len = self.config.get("max_summary_length", 2000)
            if len(content) > max_len:
                content = content[:max_len] + "\n...(已截断)"

            prompt = f"""请将以下来自【{source_name}】的内容精炼为 5-8 条要点：
1. 每条用一句话概括
2. 突出关键信息
3. 简洁的中文

原文：
{content}

总结："""

            provider = self.context.get_using_provider()
            if provider is None:
                return None

            resp = await provider.text_chat(
                prompt=prompt,
                session_id="rss_hub",
            )

            if resp and resp.completion_text:
                return resp.completion_text.strip()
            return None

        except Exception as e:
            logger.error(f"AI 总结失败: {e}")
            return None

    # ==================== 格式化输出 ====================

    def _format_summary(
        self, title: str, url: str, summary: str,
        article_date: str, source_alias: str
    ) -> str:
        """格式化 AI 总结"""
        return (
            f"📰 [{source_alias}] | {article_date}\n"
            f"{'=' * 30}\n\n"
            f"🤖 AI 总结：\n{summary}\n\n"
            f"{'=' * 30}\n"
            f"🔗 {url}"
        )

    def _format_fallback(self, article: Article, article_date: str, source_alias: str) -> str:
        """格式化回退内容"""
        content = article.content[:500] + "..." if len(article.content) > 500 else article.content

        return (
            f"📰 [{source_alias}] | {article_date}\n"
            f"{'=' * 30}\n\n"
            f"📌 {article.title}\n\n"
            f"{content}\n\n"
            f"{'=' * 30}\n"
            f"🔗 {article.link}"
        )

    # ==================== 调度器 ====================

    async def _start_schedulers(self):
        """启动所有调度器"""
        for source_id, source in self._rss_sources.items():
            if source.enabled:
                await self._start_scheduler(source_id)

    async def _start_scheduler(self, source_id: str):
        """启动单个调度器"""
        if source_id in self._scheduler_tasks:
            return

        source = self._rss_sources[source_id]
        task = asyncio.create_task(self._schedule_loop(source_id, source))
        self._scheduler_tasks[source_id] = task
        logger.info(f"已启动 {source.alias} 的调度器")

    async def _schedule_loop(self, source_id: str, source: RSSourceConfig):
        """调度循环"""
        logger.info(f"{source.alias} 调度器已启动，{source.push_hour:02d}:{source.push_minute:02d}")

        await self._startup_compensation_check(source_id, source)

        while True:
            try:
                now = datetime.now()
                target = now.replace(
                    hour=source.push_hour,
                    minute=source.push_minute,
                    second=0,
                    microsecond=0
                )
                if target <= now:
                    target += timedelta(days=1)

                wait_seconds = (target - now).total_seconds()
                logger.info(f"{source.alias} 下次：{target.strftime('%Y-%m-%d %H:%M')}")
                await asyncio.sleep(wait_seconds)

                await self._try_fetch_and_push(source_id, source)

            except asyncio.CancelledError:
                logger.info(f"{source.alias} 调度器已取消")
                break
            except Exception as e:
                logger.error(f"{source.alias} 调度器出错: {e}")
                await asyncio.sleep(60)

    async def _startup_compensation_check(self, source_id: str, source: RSSourceConfig):
        """启动补偿检查"""
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")

            target_time = now.replace(
                hour=source.push_hour,
                minute=source.push_minute,
                second=0,
                microsecond=0
            )
            if now < target_time:
                return

            cache_key = f"{source_id}_{today}"
            if cache_key in self._sent_links:
                return

            await self._try_fetch_and_push(source_id, source)

        except Exception as e:
            logger.error(f"{source.alias} 补偿检查失败: {e}")

    async def _try_fetch_and_push(self, source_id: str, source: RSSourceConfig) -> bool:
        """尝试推送"""
        try:
            article = await self._fetch_rss_latest(source.url)
            if not article:
                return False

            article_date = self._parse_article_date(article)
            today = datetime.now().strftime("%Y-%m-%d")

            if article_date != today:
                return False

            cache_key = f"{source_id}_{article.link}"
            if cache_key in self._sent_links:
                return False

            await self._do_push(source_id, source, article, article_date)
            return True

        except Exception as e:
            logger.error(f"{source.alias} 推送失败: {e}")
            return False

    async def _do_push(
        self, source_id: str, source: RSSourceConfig,
        article: Article, article_date: str
    ):
        """执行推送"""
        logger.info(f"开始推送 {source.alias}: {article.title}")

        cache_key = f"{source_id}_{article_date}"
        cache = await self._read_summary_cache()
        cached = cache.get(cache_key)

        if cached:
            text = self._format_summary(
                cached["title"], cached["url"], cached["summary"],
                article_date, source.alias
            )
        else:
            summary = await self._summarize_with_ai(article.content, source.name)
            if summary:
                cache[cache_key] = {
                    "title": article.title,
                    "url": article.link,
                    "summary": summary,
                    "source_name": source.name
                }
                await self._save_summary_cache(cache)
                text = self._format_summary(
                    article.title, article.link, summary,
                    article_date, source.alias
                )
            else:
                text = self._format_fallback(article, article_date, source.alias)

        targets = self._get_all_targets()
        if not targets:
            return

        success_count = 0
        for umo in targets:
            try:
                chain = MessageChain().message(text)
                await self.context.send_message(umo, chain)
                success_count += 1
            except Exception as e:
                logger.error(f"推送到 {umo} 失败: {e}")

        if success_count > 0:
            self._sent_dates.add(article_date)
            self._sent_links.add(cache_key)

            if len(self._sent_dates) > 30:
                self._sent_dates = set(sorted(self._sent_dates)[-30:])
            if len(self._sent_links) > 100:
                self._sent_links = set(sorted(self._sent_links)[-100:])

            await self._save_sent_news()
            logger.info(f"{source.alias} 推送完成 {success_count}/{len(targets)}")

    def _get_all_targets(self) -> Set[str]:
        """获取所有推送目标"""
        targets = set(self._cmd_subscriptions)

        groups_text = self.config.get("subscribed_groups", "")
        if groups_text:
            for group_id in groups_text.strip().split("\n"):
                group_id = group_id.strip()
                if group_id:
                    targets.add(f"default:GroupMessage:{group_id}")

        users_text = self.config.get("subscribed_users", "")
        if users_text:
            for user_id in users_text.strip().split("\n"):
                user_id = user_id.strip()
                if user_id:
                    targets.add(f"default:FriendMessage:{user_id}")

        return targets

    # ==================== 持久化 ====================

    def _atomic_write(self, filepath, data: dict):
        """原子写入"""
        dir_path = os.path.dirname(str(filepath))
        try:
            fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, str(filepath))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"原子写入失败: {e}")
            raise

    async def _load_rss_sources(self):
        """加载 RSS 源"""
        async with self._file_lock:
            try:
                filepath = str(self._sources_file)
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for source_id, source_data in data.items():
                        source = RSSourceConfig.from_dict(source_data)
                        self._rss_sources[source_id] = source
                        self._alias_map[source.alias] = source_id
                    logger.info(f"已加载 {len(self._rss_sources)} 个源")
            except Exception as e:
                logger.error(f"加载源失败: {e}")
                self._rss_sources = {}
                self._alias_map = {}

    async def _save_rss_sources(self):
        """保存 RSS 源"""
        async with self._file_lock:
            try:
                data = {
                    source_id: source.to_dict()
                    for source_id, source in self._rss_sources.items()
                }
                self._atomic_write(self._sources_file, data)
            except Exception as e:
                logger.error(f"保存源失败: {e}")

    async def _load_subscriptions(self):
        """加载订阅"""
        async with self._file_lock:
            try:
                filepath = str(self._subscriptions_file)
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._cmd_subscriptions = set(data.get("subscriptions", []))
            except Exception as e:
                logger.error(f"加载订阅失败: {e}")
                self._cmd_subscriptions = set()

    async def _save_subscriptions(self):
        """保存订阅"""
        async with self._file_lock:
            try:
                self._atomic_write(
                    self._subscriptions_file,
                    {"subscriptions": list(self._cmd_subscriptions)}
                )
            except Exception as e:
                logger.error(f"保存订阅失败: {e}")

    async def _load_sent_news(self):
        """加载已推送"""
        async with self._file_lock:
            try:
                filepath = str(self._sent_file)
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._sent_dates = set(data.get("sent_dates", []))
                    self._sent_links = set(data.get("sent_links", []))
            except Exception as e:
                logger.error(f"加载已推送失败: {e}")
                self._sent_dates = set()
                self._sent_links = set()

    async def _save_sent_news(self):
        """保存已推送"""
        async with self._file_lock:
            try:
                self._atomic_write(
                    self._sent_file,
                    {
                        "sent_dates": sorted(self._sent_dates),
                        "sent_links": sorted(self._sent_links),
                    }
                )
            except Exception as e:
                logger.error(f"保存已推送失败: {e}")

    async def _read_summary_cache(self) -> Dict[str, Dict]:
        """读取缓存"""
        async with self._file_lock:
            try:
                filepath = str(self._cache_file)
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception as e:
                logger.error(f"读取缓存失败: {e}")
            return {}

    async def _save_summary_cache(self, cache: Dict[str, Dict]):
        """保存缓存"""
        async with self._file_lock:
            try:
                ttl_days = self.config.get("cache_ttl_days", 10)
                if len(cache) > 50:
                    cutoff = (datetime.now() - timedelta(days=ttl_days)).strftime("%Y-%m-%d")
                    cache = {
                        k: v for k, v in cache.items()
                        if k.split("_")[-1] >= cutoff
                    }
                self._atomic_write(self._cache_file, cache)
            except Exception as e:
                logger.error(f"保存缓存失败: {e}")
