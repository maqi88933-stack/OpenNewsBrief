import json
import sys

import deep_series
import main


RESULT_PREFIX = "__RESULT__"
DEEP_MODE = "--deep"
DEEP_GENERATE_TTS_MODE = "--deep-generate-tts"
DEEP_GENERATE_VIDEO_MODE = "--deep-generate-video"


def get_topic(title: str):
    # UI 子进程最终仍然要落到 main.TOPICS 里的主题配置上执行。
    # 这里单独做一次查找，能把“命令行参数”和“项目真实配置”之间的映射固定在一个地方。
    for topic in main.TOPICS:
        if topic["title"] == title:
            return topic
    raise ValueError(f"未找到主题: {title}")


def print_result(result: dict) -> None:
    # 主进程依赖固定前缀来识别最终结果，所以这里统一封装输出格式。
    # 这样无论是每日简报模式，还是深度系列模式，返回协议都只维护一份。
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)


def ensure_arg_count(args: list[str], expected: int, message: str) -> bool:
    # 统一处理参数个数校验，避免多个命令分支里反复手写同样的报错逻辑。
    # expected 只统计真正的业务参数，不包含模式本身，便于阅读和后续新增命令。
    if len(args) < expected:
        print(message, file=sys.stderr)
        return False
    return True


def run_deep_command(mode: str, args: list[str]) -> int:
    # 深度系列按阶段分发：先写稿，再单独合成 TTS，最后复用 TTS 合成视频。
    # 这里集中分发，后面如果继续扩展新的深度命令，只需要在这一处补入口。
    if not ensure_arg_count(args, 2, "请传入深度系列标题和主题标题"):
        return 2

    series_title, episode_title = args[0], args[1]
    if mode == DEEP_MODE:
        result = deep_series.run_episode_by_titles(series_title, episode_title)
    elif mode == DEEP_GENERATE_TTS_MODE:
        result = deep_series.generate_episode_tts_by_titles(series_title, episode_title)
    else:
        result = deep_series.generate_episode_video_by_titles(series_title, episode_title)
    print_result(result)
    return 0


def main_cli(argv: list[str] = None):
    # 这里允许注入 argv，主要是为了让测试直接传参，不必依赖真实的 sys.argv。
    # 这样子进程入口依然很薄，但测试覆盖会稳定很多。
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("请传入主题标题", file=sys.stderr)
        return 2

    mode = argv[0]
    if mode == DEEP_MODE:
        return run_deep_command(DEEP_MODE, argv[1:])

    if mode == DEEP_GENERATE_TTS_MODE:
        return run_deep_command(DEEP_GENERATE_TTS_MODE, argv[1:])

    if mode == DEEP_GENERATE_VIDEO_MODE:
        return run_deep_command(DEEP_GENERATE_VIDEO_MODE, argv[1:])

    topic = get_topic(mode)
    result = main.run_topic_pipeline(topic)
    print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
