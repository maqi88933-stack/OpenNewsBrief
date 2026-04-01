import os
import sys

# 将上一级目录加入sys.path以便导入外层或同级模块（按需）
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from video.Audio2Video import create_video

def test_waveform_effect():
    yesterday = "2026-03-31"
    
    # 构建昨日的音频和图片路径
    audio_dir = os.path.join(base_dir, "audioContent", yesterday, "AI_每日简报")
    audio_path = os.path.join(audio_dir, f"news_brief_{yesterday}.mp3")
    image_path = os.path.join(audio_dir, "Gemini_Generated_Image.png")
    
    # 输出到测试目录或当前目录
    output_path = os.path.join(base_dir, "test_output", f"test_video_{yesterday}_waveform.mp4")
    
    print("=" * 50)
    print(" 开始测试合成（带有底部音频跳动波形图）")
    print(f" 音频：{audio_path}")
    print(f" 图片：{image_path}")
    print(f" 输出：{output_path}")
    print("=" * 50)
    
    try:
        final_video = create_video(audio_path, image_path, output_path)
        print("=" * 50)
        print(f"✅ 测试通过！合成的测试视频已保存至: {final_video}")
        print(" 请打开该文件检查波形图是否正常跳动。")
    except Exception as e:
        print(f"❌ 测试失败！")
        print(e)
        raise

if __name__ == "__main__":
    test_waveform_effect()
