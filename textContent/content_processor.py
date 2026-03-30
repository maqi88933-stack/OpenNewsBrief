# -*- coding: utf-8 -*-
import os
import re
import json
import datetime
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# 核心配置区域 (大模型配置与主题配置)
# ==========================================
import sys

# 将工程根目录加入系统路径以导入 util
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from util.llm import LLmFactory
except ImportError as e:
    print(f"导入 util.llm 失败: {e}")

try:
    from main import THEME_CONFIG  # type: ignore
except Exception as e:
    raise RuntimeError(f"无法从 main.py 导入 THEME_CONFIG，请检查工程目录结构与运行入口：{e}")

def call_llm(prompt, text=""):
    """
    调用大模型通用接口，通过 util/llm.py 的 Deepseek 实现
    """
    content = prompt
    if text:
        content += f"\n\n内容如下：\n{text}"
        
    try:
        factory = LLmFactory()
        llm = factory.getDeepseek()
        response = llm.invoke(content)
        return response.content.strip() if hasattr(response, 'content') else str(response)
    except Exception as e:
        print(f"调用大模型失败: {e}")
        return ""

def is_theme_matched(content, theme=None):
    """
    判断内容是否符合主题，并且是近期（24小时内）发生的新闻
    :param theme: 主题描述，默认使用 THEME_CONFIG
    """
    theme_text = theme if theme else THEME_CONFIG
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    prompt = (
        f"当前日期是 {today_str}。请判断以下新闻内容是否满足两个条件：\n"
        f"1. 属于近24小时内发生的新事件（如果是几天前或更早的旧闻请直接判断为否）。\n"
        f"2. 与主题“{theme_text}”紧密相关。\n\n"
        "如果同时满足上述两个条件，请仅回复“是”；否则请仅回复“否”。不要输出其他任何字符。"
    )
    result = call_llm(prompt, content)
    return "是" in result

def read_crawled_content(target_dir=None):
    """
    读取指定目录下的爬虫MD文件，解析出每一条新闻正文
    """
    if target_dir is None:
        # 默认为 crawler 当天目录
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(script_dir) # 回退到 bg 目录
        target_dir = os.path.join(project_dir, "crawler", datetime.date.today().strftime("%Y-%m-%d"))
        
    if not os.path.exists(target_dir):
        print(f"目录不存在或当日无内容: {target_dir}")
        return []

    news_items = []
    # 遍历目录下的MD文件
    for filename in os.listdir(target_dir):
        if filename.endswith(".md"):
            filepath = os.path.join(target_dir, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
                
            # 解析MD内容，根据 ### 1. 标题 这样的格式进行切分
            blocks = text.split("---")
            for block in blocks:
                if "### " in block and "**新闻正文**:" in block:
                    title_match = re.search(r"### \d+\.\s*(.+)", block)
                    link_match = re.search(r"- \*\*原链接\*\*: \[(.*?)\]", block)
                    
                    content_split = block.split("**新闻正文**:")
                    if len(content_split) > 1:
                        content = content_split[1].strip()
                        
                        if not content: # 去掉空的内容
                            continue
                            
                        title = title_match.group(1).strip() if title_match else ""
                        link = link_match.group(1).strip() if link_match else ""
                        
                        news_items.append({
                            "title": title,
                            "link": link,
                            "content": content
                        })
    return news_items

def get_previously_written_content_text(filepath):
    """
    返回之前存入文件的新闻文本，用于去重
    """
    if not os.path.exists(filepath):
        return ""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

def is_duplicate(news, previous_content):
    """
    判断是否雷同
    """
    if not previous_content:
        return False
        
    if news['title'] in previous_content:
        return True
        
    # 为了避免Token爆炸，取历史内容的后3000字符来做比对
    context = previous_content[-3000:] 
    prompt = f"以下是之前已经收录的新闻摘要/片段：\n{context}\n\n请判断以下新的新闻是否与上述已收录的新闻讲的是同一件事情（雷同）。如果是雷同新闻，请仅回复“是”；如果不雷同，请仅回复“否”。不要输出其他任何字符。\n新新闻标题：{news['title']}\n新新闻内容片段：{news['content'][:200]}"
    
    result = call_llm(prompt)
    return "是" in result

def summarize_news(item, language="zh-CN"):
    """
    对单条新闻调用大模型生成简讯摘要（100字以内）
    :param language: 目标语言
    """
    prompt = f"请将以下新闻内容压缩为一条简讯，字数严格控制在100字以内，语言简练，直击要点。\n必须使用 {language} 输出结果。\n\n标题：{item['title']}\n\n内容：{item['content'][:1000]}"
    return call_llm(prompt)

def write_to_md(news_item, output_file, index, file_lock):
    """
    将新闻（含简讯摘要）线程安全地追加写入 MD 文件
    """
    with file_lock:
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(f"### {index}. {news_item['title']}\n\n")
            f.write(f"- **原链接**: {news_item['link']}\n\n")
            # 写入简讯摘要
            if news_item.get('summary'):
                f.write(f"**简讯摘要**：{news_item['summary']}\n\n")
            f.write(f"**新闻正文**:\n\n{news_item['content']}\n\n")
            f.write("---\n\n")

def generate_briefs(output_file, brief_file, language="zh-CN"):
    """
    读取写入的 MD 文件，用大模型判断获取其中15条重要的新闻并做成简讯（每条100字以内），写入简讯文件
    :param language: 目标语言
    """
    if not os.path.exists(output_file):
        print("没有可供总结的新闻文件。")
        return
        
    with open(output_file, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # 为避免传入内容超出大模型的 Context 长度限制（如 128K），
    # 在最后生成简讯聚合时没必要传入长篇幅原文。
    # 我们用正则将“新闻正文”后面的长文本去掉，只保留标题、链接和单条摘要！
    import re
    lean_content = re.sub(r'\*\*新闻正文\*\*.*?(?=\n---\n|$)', '', content, flags=re.DOTALL)
        
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    prompt = f"""当前日期是 {today_str}。请从以下新闻合集中，挑选出最重要、最有价值的 15 条近期（24小时内）发生的新闻（如果不足15条则全部挑选）。请务必剔除那些明显是好几天前的旧闻。
然后将这些挑选出的新闻转化为简讯。
必须使用 {language} 输出结果。
要求：
1. 每条简讯字数严格控制在 100 字以内。
2. 语言简练，直击要点，剥离冗余信息。
3. 请按照“1. xxx\n2. xxx”这样的编号格式输出。
"""
    print("开始生成最终简讯...")
    brief_result = call_llm(prompt, lean_content)
    
    with open(brief_file, 'w', encoding='utf-8') as f:
        #f.write(f"# 每日新闻简讯 ({datetime.date.today().strftime('%Y-%m-%d')})\n\n")
        f.write(brief_result)
        
    print(f"简讯生成完毕，已保存至: {brief_file}")

def process_news(target_dir=None, theme=None, title_dir=None, language="zh-CN"):
    """:param theme: 主题描述，传递给 is_theme_matched
    :param title_dir: 主题安全目录名，用于区分不同主题的输出
    :param language: 目标语言"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    
    # 按 日期/title_dir/ 子目录存放
    if title_dir:
        today_dir = os.path.join(script_dir, today_str, title_dir)
    else:
        today_dir = os.path.join(script_dir, today_str)
    if not os.path.exists(today_dir):
        os.makedirs(today_dir)
        
    output_md_file = os.path.join(today_dir, f"processed_news_{today_str}.md")
    output_brief_md_file = os.path.join(today_dir, f"news_brief_{today_str}.md")
    
    print("==========================================")
    print("        大模型内容处理与简讯生成程序")
    print("==========================================")
    print("开始读取并处理新闻...")
    
    # 1. 读目录，去掉空内容
    news_items = read_crawled_content(target_dir)
    print(f"共读取到 {len(news_items)} 条非空新闻内容。")
    if not news_items:
        return
    
    # 获取已经保存的新闻，用于去重
    previous_content = get_previously_written_content_text(output_md_file)
    # 计算当前已有的条数，用于继续编号
    valid_count = previous_content.count("### ")
    
    # 用于文件写入的锁
    file_lock = threading.Lock()

    # 第一阶段：并发判断所有新闻是否符合主题
    print("阶段一：并发判断所有新闻是否符合主题...")
    matched_news = []
    matched_lock = threading.Lock()
    
    def check_theme(item):
        if is_theme_matched(item['content'], theme=theme):
            with matched_lock:
                matched_news.append(item)
            print(f"[+] 符合主题: {item['title'][:15]}...")
        else:
            print(f"[-] 不符合主题，跳过: {item['title'][:15]}...")
            
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(check_theme, item) for item in news_items]
        for future in as_completed(futures):
            if future.exception():
                print(f"[!] 主题判断异常: {future.exception()}")

    # 第二阶段：遍历所有符合主题的新闻，顺序完成去重和合并
    print(f"阶段二：符合主题的新闻共 {len(matched_news)} 条，开始顺序去重和合并...")
    unique_news = []
    
    for item in matched_news:
        if is_duplicate(item, previous_content):
            print(f"[-] 内容雷同或已存在，去重/合并跳过: {item['title'][:15]}...")
        else:
            valid_count += 1
            item['idx'] = valid_count
            unique_news.append(item)
            # 及时更新上下文，确保后续新闻去重判断最准确，避免并发导致雷同新闻写入
            previous_content += f"\n### {valid_count}. {item['title']}\n"
            
    # 第三阶段：并发生成简讯并写入文件
    print(f"阶段三：去重后新增 {len(unique_news)} 条独立新闻，开始生成简讯摘要并写入...")
    
    def process_brief_and_write(item):
        print(f"[~] 生成简讯摘要: {item['title'][:15]}...")
        item['summary'] = summarize_news(item, language=language)
        print(f"[+] 写入文件: {item['title'][:15]}...")
        write_to_md(item, output_md_file, item['idx'], file_lock)

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_brief_and_write, item) for item in unique_news]
        for future in as_completed(futures):
            if future.exception():
                print(f"[!] 简讯生成或写入异常: {future.exception()}")
        
    # 6. 生成简讯
    generate_briefs(output_md_file, output_brief_md_file, language=language)
    print("\n==== 所有处理任务已完成！ ====")

if __name__ == "__main__":
    # 可以指定参数目录，例如 process_news("d:/myself/AIContentfactory/bg/textContent")
    process_news()
