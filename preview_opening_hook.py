import argparse
import json
import os

import deep_series


DEFAULT_SERIES_TITLE = "AI时代的隐形地基"
DEFAULT_EPISODE_TITLE = "味之素：味精公司为什么成了高端芯片底座"
DEFAULT_SCRIPT_PATH = (
    r"D:\myself\AIContentfactory\bg\OpenNewsBrief\deepContent\AI时代的隐形地基"
    r"\味之素：味精公司为什么成了高端芯片底座\2026-05-28\script.md"
)


def load_episode_context(series_title: str, episode_title: str) -> tuple[dict, dict]:
    # 预览脚本优先从配置里读取研究报告、审校意见路径，找不到时也能用标题兜底运行。
    config = deep_series.load_config()
    try:
        series = deep_series.find_series(config, series_title)
        episode = deep_series.find_episode(series, episode_title)
        return series, episode
    except Exception:
        return {"title": series_title}, {"title": episode_title, "question": episode_title}


def main() -> int:
    parser = argparse.ArgumentParser(description="预览深度系列前几秒钩子审校效果")
    parser.add_argument("--script", default=DEFAULT_SCRIPT_PATH, help="要预览的 script.md 路径")
    parser.add_argument("--series", default=DEFAULT_SERIES_TITLE, help="系列标题")
    parser.add_argument("--episode", default=DEFAULT_EPISODE_TITLE, help="单集标题")
    parser.add_argument("--attempts", type=int, default=3, help="前几秒审校最多打磨轮数")
    parser.add_argument("--write", action="store_true", help="确认后才把审校结果写回原脚本")
    args = parser.parse_args()

    if not os.path.exists(args.script):
        raise FileNotFoundError(args.script)

    with open(args.script, "r", encoding="utf-8") as f:
        original_script = f.read()

    series, episode = load_episode_context(args.series, args.episode)
    research = deep_series.read_text_if_exists(episode.get("research_path", ""))
    audit = deep_series.read_text_if_exists(episode.get("audit_path", ""))
    revised_script, report = deep_series.polish_opening_hook_with_review(
        series,
        episode,
        original_script,
        research,
        audit,
        max_attempts=args.attempts,
    )

    print("========== 原始前4句 ==========")
    print(deep_series.extract_opening_hook(original_script))
    print("\n========== 审校后前4句 ==========")
    print(deep_series.extract_opening_hook(revised_script))
    print("\n========== 审校报告 ==========")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.write:
        # 默认只预览；只有显式传 --write 时才覆盖脚本，避免测试误改成片稿。
        with open(args.script, "w", encoding="utf-8") as f:
            f.write(revised_script)
        print(f"\n已写回：{args.script}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
