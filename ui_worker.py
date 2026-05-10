import json
import sys

import deep_series
import main


def get_topic(title: str):
    for topic in main.TOPICS:
        if topic["title"] == title:
            return topic
    raise ValueError(f"未找到主题: {title}")


def main_cli():
    if len(sys.argv) < 2:
        print("请传入主题标题", file=sys.stderr)
        return 2

    if sys.argv[1] == "--deep":
        if len(sys.argv) < 4:
            print("请传入深度系列标题和主题标题", file=sys.stderr)
            return 2
        result = deep_series.run_episode_by_titles(sys.argv[2], sys.argv[3])
        print("__RESULT__" + json.dumps(result, ensure_ascii=False), flush=True)
        return 0

    if sys.argv[1] == "--deep-generate-video":
        if len(sys.argv) < 4:
            print("请传入深度系列标题和主题标题", file=sys.stderr)
            return 2
        result = deep_series.generate_episode_video_by_titles(sys.argv[2], sys.argv[3])
        print("__RESULT__" + json.dumps(result, ensure_ascii=False), flush=True)
        return 0

    topic = get_topic(sys.argv[1])
    result = main.run_topic_pipeline(topic)
    print("__RESULT__" + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
