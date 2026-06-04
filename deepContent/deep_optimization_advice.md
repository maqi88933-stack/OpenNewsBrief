# 深度系列自动优化建议

# OpenNewsBrief 深度系列：数据回流后的代码优化任务清单

> 给 Codex 执行目标：基于本次回流数据，优先修复「数据可用性、发布风控、资料质量、成片时长」四类闭环问题。  
> 注意：本次报告里 `点击率=0`、`完播率=0`、`平均观看秒数=0` 大面积为 0，但 `low_click_count=0`、`low_retention_count=0`，说明当前更可能是**指标缺失或未正确入库**，不要直接按低点击/低完播做内容判断。

---

## 一、总体判断

### 1. 点击率问题

当前不应判定为真实点击率问题。

- 28 条视频里大量 `点击率=0.0`
- 但汇总里 `low_click_count=0`
- 多条视频有播放量，例如 358、340、333、307、303，但 CTR 仍为 0
- 说明 CTR 可能没有被采集、没有被解析、或被默认值 0 覆盖

**优先任务：修复指标回流中的 CTR 缺失识别，不要把缺失值当成 0。**

---

### 2. 完播率问题

当前不应判定为真实完播率问题。

- 所有视频 `平均观看秒数=0.0`、`完播率=0.0`
- 但播放量并非 0
- 汇总里 `low_retention_count=0`
- 说明完播率、平均观看时长大概率未接入或字段映射失败

**优先任务：修复 retention 指标的 null / missing / zero 语义。**

---

### 3. 资料质量问题

资料质量问题真实存在，且影响写稿差异化。

高风险表现：

- 多条视频 `来源数=0`
- 多条视频被标记：
  - `有效来源不足：0/3`
  - `有效来源不足：2/3`
  - `使用保守写稿，可能缺少差异化信息`

需要代码层面增加：

- 最低来源数门禁
- 来源不足时自动补充检索
- 来源不足禁止进入正式写稿或发布
- 保守写稿模式需要显式标记并降级为草稿

---

### 4. 发布质量风险问题

发布质量风险非常明确。

已发布或准备发布的视频中，多条超过 150 秒：

- 246 秒
- 232 秒
- 219 秒
- 205 秒
- 184 秒
- 182 秒
- 181 秒
- 162 秒

这说明当前脚本生成、配音、剪辑或发布前校验没有强制控制时长。

**需要在发布前增加硬门禁：成片超过 150 秒不得自动发布。**

---

# 二、Codex 执行任务

## Task 1：修复指标回流，不要把缺失 CTR / 完播率写成 0

### 问题

当前报告中大量视频有播放量，但：

```json
"平均观看秒数": 0.0,
"完播率": 0.0,
"点击率": 0.0
```

这会导致系统误以为表现极差，或者反过来因为 `low_click_count=0`、`low_retention_count=0` 而跳过诊断。

### 需要修改的模块或函数

请在代码库中查找并修改以下相关模块或函数，具体名称以现有项目为准：

- `metrics_ingest.py`
- `analytics.py`
- `report_generator.py`
- `growth_report.py`
- `load_video_metrics()`
- `normalize_metrics()`
- `build_metric_summary()`
- `generate_feedback_report()`

### 修改要求

1. 将 CTR、完播率、平均观看秒数的缺失值保留为 `null` / `None`，不要默认写成 `0.0`。
2. 只有当平台明确返回 0 时，才写成 `0.0`。
3. 新增字段：
   - `ctr_status`
   - `retention_status`
   - `avg_watch_status`

建议状态：

```python
"available"       # 指标真实可用
"missing"         # 平台未返回
"not_applicable"  # 不适用
"parse_failed"    # 返回了但解析失败
```

4. 修改低点击、低完播判断逻辑：

```python
if ctr_status != "available":
    不计入 low_click_count

if retention_status != "available":
    不计入 low_retention_count
```

5. 报告中增加指标健康检查：

```json
{
  "metric_health": {
    "ctr_available_count": 0,
    "retention_available_count": 0,
    "avg_watch_available_count": 0,
    "metric_missing_warning": true
  }
}
```

### 验收标准

- 本次这批数据重新生成报告时，不应把所有 CTR / 完播率显示成真实 0。
- 报告应明确提示：

```text
CTR / 完播率指标不可用，当前不能判断点击率和完播率问题。
```

---

## Task 2：发布前增加成片时长硬门禁，超过 150 秒禁止自动发布

### 问题

当前至少 8 条视频超过 150 秒，且被标记为风险：

```text
视频实际246秒，超过150秒
视频实际232秒，超过150秒
视频实际219秒，超过150秒
视频实际205秒，超过150秒
视频实际184秒，超过150秒
视频实际182秒，超过150秒
视频实际181秒，超过150秒
视频实际162秒，超过150秒
```

这说明当前发布质量门禁没有拦截。

### 需要修改的模块或函数

请查找并修改：

- `publish.py`
- `publisher.py`
- `release_pipeline.py`
- `video_quality_gate.py`
- `validate_before_publish()`
- `pre_publish_check()`
- `publish_video()`
- `render_video()`

### 修改要求

新增或强化发布前校验函数：

```python
MAX_VIDEO_DURATION_SECONDS = 150

def validate_video_duration(video):
    if video.duration_seconds is None or video.duration_seconds <= 0:
        return {
            "passed": False,
            "reason": "missing_duration"
        }

    if video.duration_seconds > MAX_VIDEO_DURATION_SECONDS:
        return {
            "passed": False,
            "reason": "duration_exceeded",
            "actual_duration": video.duration_seconds,
            "max_duration": MAX_VIDEO_DURATION_SECONDS
        }

    return {
        "passed": True
    }
```

发布函数中必须调用：

```python
duration_check = validate_video_duration(video)

if not duration_check["passed"]:
    block_publish(video, duration_check)
    return
```

### 发布阻断规则

只要满足以下任一条件，禁止自动发布：

```python
duration_seconds > 150
duration_seconds is None
duration_seconds <= 0
```

### 验收标准

- 246 秒、232 秒、219 秒、205 秒等视频全部被 `blocked`。
- `blocked_count` 应至少覆盖这些超时视频。
- 被阻断的视频进入 `needs_edit` 或 `draft` 状态，而不是继续发布。

---

## Task 3：增加自动压缩脚本功能，将 180-250 秒脚本压缩到 120-145 秒

### 问题

多条深度系列视频时长在 180-246 秒，说明脚本阶段没有严格控制字数。

以中文口播估算：

- 150 秒约 450-525 汉字
- 120 秒约 360-420 汉字

当前脚本可能过长，导致成片超过平台推荐时长。

### 需要修改的模块或函数

请查找并修改：

- `script_writer.py`
- `script_generator.py`
- `draft_generator.py`
- `narration.py`
- `estimate_duration()`
- `generate_script()`
- `rewrite_script()`
- `compress_script()`

### 修改要求

新增脚本预算参数：

```python
TARGET_DURATION_SECONDS = 135
MAX_DURATION_SECONDS = 150
CHINESE_CHARS_PER_SECOND = 3.2
TARGET_SCRIPT_CHARS = int(TARGET_DURATION_SECONDS * CHINESE_CHARS_PER_SECOND)
MAX_SCRIPT_CHARS = int(MAX_DURATION_SECONDS * CHINESE_CHARS_PER_SECOND)
```

新增脚本压缩函数：

```python
def compress_script_to_duration(script: str, target_seconds: int = 135) -> str:
    """
    将脚本压缩到目标时长。
    保留：
    1. 开头钩子
    2. 核心反差
    3. 关键事实
    4. 结尾判断

    删除：
    1. 重复解释
    2. 过长背景
    3. 空泛铺垫
    4. 多余转场
    """
```

在 `generate_script()` 后增加二次检查：

```python
script = generate_script(topic, sources)
estimated_duration = estimate_duration(script)

if estimated_duration > MAX_DURATION_SECONDS:
    script = compress_script_to_duration(script, target_seconds=135)
```

### 压缩策略

Codex 请在提示词或压缩逻辑里加入以下要求：

```text
把脚本压缩为 120-145 秒短视频口播。
必须保留一个反差开头、三个事实点、一个结论。
删除行业背景铺垫、重复解释和抽象形容词。
每段不超过 60 字。
总字数控制在 420 字以内。
```

### 验收标准

- 重新生成这些主题时，估算时长不超过 150 秒：
  - 机器人不是来当管家的，是先来打工的
  - AI 最缺的可能是能把系统跑稳的人
  - Dell：卖电脑的公司为什么吃到 AI 服务器红利
  - HOYA：眼镜公司为什么掌握光刻入口
  - 日东电工：胶带公司为什么离 AI 芯片很近
  - 味之素：味精公司为什么成了高端芯片底座
  - 大金：空调公司为什么站在 AI 基建背后
  - 3M：便利贴公司为什么卡住芯片抛光良率

---

## Task 4：资料来源不足时自动补检索，仍不足则禁止正式写稿

### 问题

多条视频来源数为 0 或不足 3：

```text
来源数 0：机器人打工、AI 系统跑稳、Dell、日东电工、3M 等
来源数 2：HOYA
```

当前系统在来源不足时仍继续生成内容，并触发：

```text
使用保守写稿，可能缺少差异化信息
```

这会导致内容泛化，缺少可信细节。

### 需要修改的模块或函数

请查找并修改：

- `research.py`
- `source_collector.py`
- `retriever.py`
- `fact_check.py`
- `collect_sources()`
- `validate_sources()`
- `build_research_pack()`
- `generate_script_from_sources()`

### 修改要求

设置最小有效来源数：

```python
MIN_VALID_SOURCE_COUNT = 3
```

在进入写稿前增加资料校验：

```python
def validate_research_pack(research_pack):
    valid_sources = [
        s for s in research_pack.sources
        if s.get("is_valid") and s.get("url") and s.get("title")
    ]

    if len(valid_sources) < MIN_VALID_SOURCE_COUNT:
        return {
            "passed": False,
            "reason": "insufficient_sources",
            "valid_source_count": len(valid_sources),
            "required_source_count": MIN_VALID_SOURCE_COUNT
        }

    return {
        "passed": True,
        "valid_source_count": len(valid_sources)
    }
```

写稿前调用：

```python
research_check = validate_research_pack(research_pack)

if not research_check["passed"]:
    research_pack = auto_expand_sources(topic, research_pack)

research_check = validate_research_pack(research_pack)

if not research_check["passed"]:
    mark_as_research_needed(topic, research_check)
    return
```

### 自动补检索策略

新增函数：

```python
def auto_expand_sources(topic, research_pack):
    """
    当有效来源不足 3 个时，自动扩展检索。
    优先检索：
    1. 公司官网 / 年报 / 投资者关系
    2. 权威媒体
    3. 行业报告
    4. 专利 / 供应链资料
    5. 财报电话会文字稿
    """
```

### 搜索 query 模板

对企业类主题自动生成：

```python
queries = [
    f"{company} annual report AI revenue",
    f"{company} investor relations semiconductor materials",
    f"{company} earnings call AI data center",
    f"{company} product semiconductor manufacturing",
    f"{company} official AI infrastructure"
]
```

对行业趋势类主题自动生成：

```python
queries = [
    f"{topic} market report",
    f"{topic} company adoption",
    f"{topic} Gartner IDC McKinsey report",
    f"{topic} earnings call",
    f"{topic} official blog"
]
```

### 验收标准

- 来源数小于 3 时，不允许直接进入正式写稿。
- 自动补检索后仍不足 3，则进入 `research_needed`。
- 报告中应区分：
  - `source_count=0`
  - `source_count<3`
  - `source_count>=3`
- 不再出现来源数 0 但直接发布的情况。

---

## Task 5：将“保守写稿”改为降级草稿，不允许自动发布

### 问题

多条视频被标记：

```text
使用保守写稿，可能缺少差异化信息
```

这说明系统在资料不足时会用保守写稿兜底，但该模式适合生成草稿，不适合直接发布。

### 需要修改的模块或函数

请查找并修改：

- `script_writer.py`
- `fallback_writer.py`
- `content_pipeline.py`
- `generate_conservative_script()`
- `generate_script()`
- `finalize_script()`
- `publish_video()`

### 修改要求

为脚本增加生成模式字段：

```python
script_generation_mode = "normal" | "conservative" | "fallback"
```

如果使用保守写稿：

```python
if script_generation_mode in ["conservative", "fallback"]:
    video.publish_allowed = False
    video.status = "draft"
    video.risks.append("conservative_script_not_publishable")
```

发布前也要二次拦截：

```python
if video.script_generation_mode in ["conservative", "fallback"]:
    block_publish(video, {
        "reason": "conservative_script_not_publishable"
    })
    return
```

### 验收标准

- 使用保守写稿的视频只能进入草稿。
- 不允许自动发布。
- 报告中风险项应从模糊文案变成结构化字段：

```json
{
  "risk_code": "conservative_script_not_publishable",
  "risk_message": "使用保守写稿，禁止自动发布"
}
```

---

## Task 6：补齐标题为空的发布质量检查

### 问题

多条视频 `发布标题` 为空：

```text
AI 为什么会替代搜索？
AI 为什么需要记忆？
私有化 AI 为什么会爆发？
AI Agent——从聊天到替你干活，还有多远？
```

标题为空但仍有播放量，说明可能存在：

- 数据回流没有抓到标题
- 发布标题没有入库
- 发布前没有标题门禁
- 平台标题字段映射错误

### 需要修改的模块或函数

请查找并修改：

- `title_generator.py`
- `metadata.py`
- `publisher.py`
- `metrics_ingest.py`
- `validate_metadata()`
- `generate_title()`
- `publish_video()`
- `parse_platform_video_data()`

### 修改要求

发布前增加标题校验：

```python
def validate_publish_title(video):
    title = (video.publish_title or "").strip()

    if not title:
        return {
            "passed": False,
            "reason": "missing_publish_title"
        }

    if len(title) < 6:
        return {
            "passed": False,
            "reason": "publish_title_too_short",
            "title": title
        }

    if len(title) > 24:
        return {
            "passed": False,
            "reason": "publish_title_too_long",
            "title": title
        }

    return {
        "passed": True
    }
```

发布前调用：

```python
title_check = validate_publish_title(video)

if not title_check["passed"]:
    block_publish(video, title_check)
    return
```

### 标题生成兜底

如果标题为空，尝试生成标题，但生成后必须重新校验：

```python
if not video.publish_title:
    video.publish_title = generate_publish_title(video.topic, video.series)

title_check = validate_publish_title(video)
```

### 验收标准

- 标题为空的视频不能发布。
- 标题为空时系统自动生成候选标题。
- 生成失败或仍不合格时进入 `metadata_needed`。

---

## Task 7：新增发布质量总门禁函数，集中拦截风险

### 问题

当前风险分散在报告中，但没有形成统一的发布阻断逻辑。

本次至少应阻断：

- 时长超过 150 秒
- 来源不足 3
- 使用保守写稿
- 标题为空
- 时长为 0 或缺失

### 需要修改的模块或函数

请查找并修改：

- `quality_gate.py`
- `publish_guard.py`
- `release_pipeline.py`
- `pre_publish_check()`
- `validate_before_publish()`
- `publish_video()`

### 修改要求

新增统一函数：

```python
def validate_before_publish(video):
    checks = [
        validate_publish_title(video),
        validate_video_duration(video),
        validate_video_sources(video),
        validate_script_generation_mode(video),
    ]

    failed_checks = [c for c in checks if not c["passed"]]

    if failed_checks:
        return {
            "passed": False,
            "failed_checks": failed_checks
        }

    return {
        "passed": True,
        "failed_checks": []
    }
```

发布流程必须改成：

```python
publish_check = validate_before_publish(video)

if not publish_check["passed"]:
    block_publish(video, publish_check)
    save_publish_block_record(video, publish_check)
    return

publish_to_platform(video)
```

### 验收标准

报告中的 `blocked_count` 应来自真实发布门禁，而不是事后风险统计。

---

## Task 8：修复时长为 0 的视频元数据问题

### 问题

多条视频时长为 0：

```text
AI未来三年系列 多条视频时长=0
AI 如何重构企业 多条视频时长=0
```

但这些视频有播放量，说明时长字段未正确回流或未写入。

### 需要修改的模块或函数

请查找并修改：

- `video_metadata.py`
- `metrics_ingest.py`
- `platform_api.py`
- `parse_platform_video_data()`
- `load_video_metadata()`
- `sync_video_metadata()`

### 修改要求

1. 平台回流时正确解析视频时长。
2. 如果平台未返回时长，则从本地渲染产物读取。
3. 如果仍读取不到，则标记为 `duration_status="missing"`，不要写成 0。

示例：

```python
def resolve_video_duration(platform_payload, local_video_path=None):
    duration = parse_duration_from_platform(platform_payload)

    if duration and duration > 0:
        return duration, "platform"

    if local_video_path:
        duration = probe_local_video_duration(local_video_path)
        if duration and duration > 0:
            return duration, "local_probe"

    return None, "missing"
```

### 验收标准

- 有播放量但时长为 0 的记录不再显示为 `0`。
- 报告显示：

```json
{
  "duration_seconds": null,
  "duration_status": "missing"
}
```

- 发布门禁中，`duration_status="missing"` 的视频不能自动发布。

---

## Task 9：报告中分离“真实表现问题”和“数据缺失问题”

### 问题

当前报告中 CTR、完播率为 0，但汇总 low count 为 0，容易误导判断。

### 需要修改的模块或函数

请查找并修改：

- `report_generator.py`
- `feedback_report.py`
- `growth_diagnosis.py`
- `classify_video_issues()`
- `generate_summary()`

### 修改要求

报告分成四个部分：

```markdown
## 点击率问题
- 仅统计 CTR 指标可用的视频
- CTR 缺失的视频进入“指标缺失问题”

## 完播率问题
- 仅统计完播率指标可用的视频
- 完播率缺失的视频进入“指标缺失问题”

## 资料质量问题
- 来源不足
- 保守写稿
- 缺少有效引用

## 发布质量风险
- 超时
- 标题为空
- 时长缺失
- 使用 fallback
```

### 分类逻辑建议

```python
def classify_video_issues(video):
    issues = {
        "click": [],
        "retention": [],
        "research": [],
        "publish_quality": [],
        "metric_missing": []
    }

    if video.ctr_status != "available":
        issues["metric_missing"].append("ctr_missing")
    elif video.ctr < LOW_CTR_THRESHOLD:
        issues["click"].append("low_ctr")

    if video.retention_status != "available":
        issues["metric_missing"].append("retention_missing")
    elif video.completion_rate < LOW_RETENTION_THRESHOLD:
        issues["retention"].append("low_retention")

    if video.source_count < 3:
        issues["research"].append("insufficient_sources")

    if video.script_generation_mode in ["conservative", "fallback"]:
        issues["research"].append("conservative_script")

    if not video.publish_title:
        issues["publish_quality"].append("missing_title")

    if video.duration_seconds is None or video.duration_seconds <= 0:
        issues["publish_quality"].append("missing_duration")
    elif video.duration_seconds > 150:
        issues["publish_quality"].append("duration_exceeded")

    return issues
```

### 验收标准

- 本次报告不再把 CTR=0 和完播率=0 直接解释成用户不爱看。
- 报告能明确说明：

```text
本批次无法判断真实点击率和完播率，因为关键指标缺失。
```

---

## Task 10：对本批高风险视频生成返工队列

### 问题

当前报告只列出风险，没有形成返工任务。

### 需要修改的模块或函数

请查找并修改：

- `task_queue.py`
- `workflow.py`
- `remediation.py`
- `create_rework_tasks()`
- `generate_action_items()`
- `save_feedback_tasks()`

### 修改要求

新增返工任务生成函数：

```python
def create_rework_tasks(video):
    tasks = []

    if video.duration_seconds and video.duration_seconds > 150:
        tasks.append({
            "type": "script_compression",
            "priority": "high",
            "reason": "duration_exceeded",
            "target_duration_seconds": 135
        })

    if video.source_count < 3:
        tasks.append({
            "type": "source_expansion",
            "priority": "high",
            "reason": "insufficient_sources",
            "required_source_count": 3
        })

    if video.script_generation_mode in ["conservative", "fallback"]:
        tasks.append({
            "type": "rewrite_with_sources",
            "priority": "high",
            "reason": "conservative_script"
        })

    if not video.publish_title:
        tasks.append({
            "type": "title_generation",
            "priority": "medium",
            "reason": "missing_publish_title"
        })

    if video.duration_seconds is None or video.duration_seconds <= 0:
        tasks.append({
            "type": "metadata_sync",
            "priority": "high",
            "reason": "missing_duration"
        })

    return tasks
```

### 本批应生成的重点返工任务

#### A. 脚本压缩任务

以下视频生成 `script_compression`：

- 别急着让机器人当管家，246 秒
- AI最缺的可能是能把系统跑稳的人，232 秒
- 不造GPU，照样赚AI钱，219 秒
- 卖眼镜的HOYA，卡在AI芯片光刻前一步？，205 秒
- AI芯片怕残胶，184 秒
- 味精厂卡位AI芯片，182 秒
- 空调巨头的AI底牌，181 秒
- 3M凭什么卡住芯片良率？，162 秒

#### B. 来源补充任务

以下视频生成 `source_expansion`：

- 机器人不是来当管家的，是先来打工的，来源数 0
- AI 最缺的可能是能把系统跑稳的人，来源数 0
- Dell：卖电脑的公司为什么吃到 AI 服务器红利，来源数 0
- HOYA：眼镜公司为什么掌握光刻入口，来源数 2
- 日东电工：胶带公司为什么离 AI 芯片很近，来源数 0
- 3M：便利贴公司为什么卡住芯片抛光良率，来源数 0

#### C. 元数据同步任务

以下类型生成 `metadata_sync`：

- 时长为 0 但播放量大于 0 的视频
- 标题为空但播放量大于 0 的视频
- CTR / 完播率不可用的视频

### 验收标准

- 报告生成后自动产出返工任务列表。
- 每个任务包含：
  - `video_id`
  - `topic`
  - `task_type`
  - `priority`
  - `reason`
  - `created_at`
  - `status=pending`

---

# 三、建议的执行优先级

## P0：必须先做

1. 修复指标回流缺失值语义  
   修改模块：
   - `metrics_ingest.py`
   - `analytics.py`
   - `report_generator.py`

2. 增加发布前硬门禁  
   修改模块：
   - `publisher.py`
   - `quality_gate.py`
   - `release_pipeline.py`

3. 修复时长为 0 / 标题为空的元数据问题  
   修改模块：
   - `video_metadata.py`
   - `platform_api.py`
   - `metadata.py`

---

## P1：内容生产闭环

4. 来源不足自动补检索  
   修改模块：
   - `research.py`
   - `source_collector.py`
   - `retriever.py`

5. 保守写稿禁止自动发布  
   修改模块：
   - `script_writer.py`
   - `fallback_writer.py`
   - `publisher.py`

6. 脚本自动压缩到 120-145 秒  
   修改模块：
   - `script_generator.py`
   - `narration.py`
   - `script_writer.py`

---

## P2：报告和返工自动化

7. 报告中分离点击率、完播率、资料质量、发布质量风险  
   修改模块：
   - `report_generator.py`
   - `growth_diagnosis.py`

8. 自动生成返工队列  
   修改模块：
   - `task_queue.py`
   - `remediation.py`
   - `workflow.py`

---

# 四、最终验收口径

Codex 完成后，请运行或补充测试，确保：

1. CTR、完播率、平均观看秒数缺失时显示为 `null`，不再默认 `0.0`。
2. 指标不可用时，不进入低点击、低完播判断。
3. 超过 150 秒的视频禁止自动发布。
4. 时长缺失或为 0 的视频禁止自动发布。
5. 标题为空的视频禁止自动发布。
6. 来源数小于 3 的视频不能进入正式写稿或发布。
7. 使用保守写稿的视频只能进入草稿。
8. 报告明确分为：
   - 点击率问题
   - 完播率问题
   - 资料质量问题
   - 发布质量风险
   - 指标缺失问题
9. 系统自动为本批高风险视频生成返工任务。
10. `blocked_count` 来自真实发布门禁，而不是事后风险文案统计。

## 可复制给 Codex 的执行提示词

```text
请在 `D:\myself\AIContentfactory\bg\OpenNewsBrief` 中继续优化深度系列视频生成闭环。
先读取 `deepContent\deep_feedback_report.json`，再根据报告里的点击率、完播率、平均观看时长、来源数和发布质量风险原因做窄范围改动。
发布列表和单个/批量发布不要在待发布或上传阶段拦截，超时脚本必须回到专门的优化代理处理。
优先顺序：质量风险回修、封面/首屏可读性、脚本时长、前15秒冷开场、数据回流字段。
不要修改每日简报功能，新增或修改代码都写具体中文注释，并运行相关 unittest。
```
