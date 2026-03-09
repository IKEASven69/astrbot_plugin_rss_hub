# RSS Hub - 极简命令版多源资讯订阅插件

🚀 **极简命令 + 自定义别名 + 并发获取 + 推荐源** - 为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 开发

## ✨ 核心特性

### 🎯 极简命令（所有命令超短）

| 命令 | 说明 | 示例 |
|------|------|------|
| `/` | 帮助 | `/` |
| `/list` | 查看所有源 | `/list` |
| `/add <别名/数字> <URL>` | 添加源 | `/add 36kr https://36kr.com/feed` |
| `/get [别名]` | 获取资讯 | `/get ai` |
| `/del <别名>` | 删除源 | `/del 36kr` |
| `/rename <旧> <新>` | 改名 | `/rename ai 橘鸦` |
| `/pause <别名/all>` | 暂停 | `/pause 36kr` / `/pause all` |
| `/resume <别名/all>` | 恢复 | `/resume 36kr` / `/resume all` |
| `/test <别名>` | 测试 | `/test ai` |
| `/recs` | 推荐源 | `/recs` |
| `/sub` | 订阅 | `/sub` |
| `/unsub` | 取消 | `/unsub` |
| `/status` | 状态 | `/status` |

### 🏷️ 自定义别名系统

```
添加源时自定义：/add ai https://example.com/feed
后续用别名操作：/get ai, /pause ai, /del ai
随时可以改名：/rename ai 橘鸦
```

## 🚀 核心功能

### 多源管理
- 支持无限数量的 RSS 源订阅
- 每个源独立配置推送时间
- 暂停/恢复单个源或所有源

### 并发获取
- **速度提升 3 倍**：5 个源从 15 秒降至 5 秒
- 自动容错：单个源失败不影响其他源

### 批量操作
- `/pause all` - 暂停所有源
- `/resume all` - 恢复所有源
- `/get` - 无参数时获取所有活跃源的资讯

### 推荐源列表
```
/recs

显示 6 个精选 RSS 源：
1. 36氪 (科技资讯)
2. 少数派 (效率工具)
3. 橘鸦AI日报 (AI行业)
4. 阮一峰科技周刊 (技术)
5. solidot (开源/科技)
6. 机器之心 (AI/科技)

快速添加：/add 1
```

### 交互式向导
- `/add <数字>` - 从推荐源快速添加
- 自动填充别名、URL、推送时间
- 支持自定义覆盖

## 📝 快速上手

```
# 1. 查看推荐源（最快上手方式）
/recs

# 2. 快速添加推荐源（用数字）
/add 1    # 添加 36氪
/add 2    # 添加少数派

# 3. 查看所有源
/list

# 4. 获取资讯
/get 36kr         # 获取 36氪
/get              # 获取所有活跃源

# 5. 批量暂停/恢复
/pause all        # 暂停所有源
/resume 36kr      # 恢复单个源
/resume all       # 恢复所有源

# 6. 手动添加自定义源
/add myblog https://example.com/feed
/add sspai https://sspai.com/feed 9:00

# 7. 测试源
/test 36kr

# 8. 改名
/rename 36kr 科技

# 9. 删除源
/del myblog
```

## ⚙️ 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `default_push_hour` | 推送小时 | 8 |
| `default_push_minute` | 推送分钟 | 0 |
| `enable_ai_summary` | AI总结 | true |
| `cache_ttl_days` | 缓存天数 | 10 |
| `max_concurrent_fetches` | 并发获取上限 | 10 |
| `fetch_timeout` | 获取超时（秒） | 30 |

### 并发获取性能

| 源数量 | 串行耗时 | 并发耗时 | 提升 |
|--------|----------|----------|------|
| 3 个 | 9 秒 | 3 秒 | 3x |
| 5 个 | 15 秒 | 5 秒 | 3x |
| 10 个 | 30 秒 | 10 秒 | 3x |

## 📦 安装

### 方式一：通过插件市场安装（推荐）

在 AstrBot 插件市场搜索 **rss_hub**，点击安装即可。

或手动填入仓库地址安装：

```
https://github.com/IKEASven69/astrbot_plugin_rss_hub
```

**依赖（`aiohttp`、`feedparser`、`python-dateutil`）会由 AstrBot 自动安装。**

### 方式二：手动安装

1. 将插件复制到 AstrBot 的 `plugins/` 目录
2. 重启 AstrBot

**注意**：AstrBot 会自动检测并安装 `requirements.txt` 中的依赖，无需手动运行 pip install。

## 💡 别名命名建议

| 类型 | 建议别名 |
|------|----------|
| 科技类 | `tech`, `36kr` |
| AI 类 | `ai`, `ml` |
| 开发者 | `dev`, `code` |
| 新闻类 | `news` |

## 📄 License

GPL-3.0
