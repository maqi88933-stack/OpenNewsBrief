import json
import sys

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

    topic = get_topic(sys.argv[1])
    result = main.run_topic_pipeline(topic)
    print("__RESULT__" + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
