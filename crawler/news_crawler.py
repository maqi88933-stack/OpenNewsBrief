# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import datetime
import email.utils
import os
import sys
from html.parser import HTMLParser
from typing import Optional, List, Dict, Tuple
from playwright.sync_api import sync_playwright

# ==========================================
# 核心配置区域
# ==========================================
# 关键词配置统一放在工程根目录的 main.py 中
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
try:
    from main import KEYWORDS  # type: ignore
except Exception as e:
    raise RuntimeError(f"无法从 main.py 导入 KEYWORDS，请检查工程目录结构与运行入口：{e}")

# 在这里配置新闻有效时间的时限（单位：小时）
# 如果新闻的发布实际距离现在超过该配置的小时数，则判定为过时并被丢弃
MAX_HOURS =24

def fetch_rss_xml(keyword):
    """
    爬取核心逻辑：从 Google News（默认）、Bing 新闻（备选）或 百度新闻（兜底）提取数据。
    使用 Playwright 模拟浏览器。
    """
    encoded_kw = urllib.parse.quote(keyword)
    
    # 百度新闻搜索 URL
    baidu_url = (
        "https://www.baidu.com/s"
        f"?tn=news&word={encoded_kw}"
    )
    # Bing 新闻 RSS URL (备选)
    bing_url = (
        "https://www.bing.com/news/search"
        f"?q={encoded_kw}"
        "&format=rss"
        "&setlang=zh-hans"
        "&setmkt=zh-CN"
        "&mkt=zh-CN"
    )
    google_url = (
        "https://news.google.com/rss/search"
        f"?q={encoded_kw}"
        "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )

    def _fetch_with_playwright(url: str):
        try:
            with sync_playwright() as p:
                with p.chromium.launch(headless=True) as browser:
                    context = browser.new_context(
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                        ignore_https_errors=True
                    )
                    page = context.new_page()
                    # 屏蔽无关资源以加速
                    page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] else route.continue_())
                    
                    # 访问页面
                    response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    if not response:
                        return None
                    
                    body = response.body()
                    return body
        except Exception as e:
            print(f"    [!] 网络请求失败 [{url[:30]}...]: {str(e).splitlines()[0][:60]}...")
            return None

    # 1. 优先尝试 Google News
    print(f"    [i] 尝试从 Google News RSS 获取: {keyword}")
    body = _fetch_with_playwright(google_url)
    if body:
        return body

    # 2. 备选尝试 Bing
    print(f"    [i] Google News 结果不可用，尝试 Bing 新闻 RSS: {keyword}")
    body = _fetch_with_playwright(bing_url)
    if body:
        return body
    
    # 3. 最后的兜底 百度
    print(f"    [i] Bing 结果不可用，尝试 百度新闻 获取: {keyword}")
    body = _fetch_with_playwright(baidu_url)
    if body and (b"baidu.com" in body or b"result-op news" in body):
        return body

    return None

class _BaiduNewsHTMLParser(HTMLParser):
    """
    解析百度新闻搜索结果页面。
    """
    def __init__(self):
        super().__init__()
        self._in_h3 = False
        self._in_a = False
        self._a_href: Optional[str] = None
        self._a_text_parts: List[str] = []
        self.items: List[Tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            # 百度新闻标题通常在 h3.news-title_xxx 或类似结构中
            self._in_h3 = True
        elif tag == "a" and self._in_h3:
            self._in_a = True
            for k, v in attrs:
                if k.lower() == "href":
                    self._a_href = v
                    break

    def handle_data(self, data):
        if self._in_a:
            self._a_text_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "h3":
            self._in_h3 = False
        elif tag == "a" and self._in_a:
            self._in_a = False
            title = "".join(self._a_text_parts).strip()
            href = (self._a_href or "").strip()
            if len(title) > 5 and href.startswith("http"):
                self.items.append((title, href))
            self._a_text_parts = []
            self._a_href = None

def _parse_baidu_html_results(html_bytes: bytes, keyword: str) -> List[Dict]:
    """
    将百度返回的 HTML 解析成统一的 news_list 结构。
    """
    try:
        html_text = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        html_text = str(html_bytes[:2000])

    parser = _BaiduNewsHTMLParser()
    try:
        parser.feed(html_text)
    except Exception as e:
        print(f"百度HTML解析失败 [{keyword}]: {e}")
        return []

    seen = set()
    results: List[Dict] = []
    today_date = datetime.datetime.now().astimezone().date()
    for title, link in parser.items:
        # 关键词过滤
        if keyword:
            if any("\u4e00" <= ch <= "\u9fff" for ch in keyword):
                if keyword not in title:
                    continue
            else:
                if keyword.lower() not in title.lower():
                    continue
        
        key = (title, link)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "title": title,
            "link": link,
            "content": "",
            "date": today_date.strftime("%Y-%m-%d")
        })
        if len(results) >= 10:
            break
    return results

class _BingNewsHTMLParser(HTMLParser):
    """
    从 Bing News 搜索 HTML 页面里尽量抽取新闻条目（标题/链接）。
    这是“兜底兼容”，用于 Bing RSS 被降级为 HTML 时仍能产出结果。
    """
    def __init__(self):
        super().__init__()
        self._in_a = False
        self._a_href: Optional[str] = None
        self._a_text_parts: List[str] = []
        self.items: List[Tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = None
        for k, v in attrs:
            if k.lower() == "href":
                href = v
                break
        if not href:
            return
        # 常见新闻结果链接形态：/news/apiclick?... 或 https://...
        if ("/news/apiclick" in href) or href.startswith("http"):
            self._in_a = True
            self._a_href = href
            self._a_text_parts = []

    def handle_data(self, data):
        if self._in_a and data:
            t = data.strip()
            if t:
                self._a_text_parts.append(t)

    def handle_endtag(self, tag):
        if tag != "a":
            return
        if not self._in_a:
            return
        text = " ".join(self._a_text_parts).strip()
        href = (self._a_href or "").strip()
        self._in_a = False
        self._a_href = None
        self._a_text_parts = []
        # 过滤：标题太短/无效链接
        if len(text) < 8:
            return
        if not href:
            return
        # 去掉明显非新闻跳转
        if href.startswith("javascript:"):
            return
        self.items.append((text, href))

def _parse_bing_html_results(html_bytes: bytes, keyword: str) -> List[Dict]:
    """
    将 Bing 返回的 HTML 解析成统一的 news_list 结构。
    """
    try:
        html_text = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        html_text = str(html_bytes[:2000])

    parser = _BingNewsHTMLParser()
    try:
        parser.feed(html_text)
    except Exception as e:
        print(f"HTML解析失败 [{keyword}]: {e}")
        return []

    # 去重并限制数量
    seen = set()
    results: List[Dict] = []
    today_date = datetime.datetime.now().astimezone().date()
    for title, link in parser.items:
        if keyword:
            if any("\u4e00" <= ch <= "\u9fff" for ch in keyword):
                if keyword not in title:
                    continue
            else:
                if keyword.lower() not in title.lower():
                    continue
        if "壁纸" in title or "必应" in title:
            continue
        key = (title, link)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "title": title,
            "link": link,
            "content": "",
            "date": today_date.strftime("%Y-%m-%d")
        })
        if len(results) >= 10:
            break
    return results

def fetch_article_content(url, default_desc=''):
    """
    抓取新闻详情页正文核心逻辑。
    """
    import urllib.parse
    real_url = url
    if "url=" in url:
        try:
            real_url = urllib.parse.unquote(url.split("url=")[1].split("&")[0])
        except Exception:
            pass
            
    try:
        with sync_playwright() as p:
            with p.chromium.launch(headless=True) as browser:
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                    ignore_https_errors=True
                )
                page = context.new_page()
                page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] else route.continue_())

                page.goto(real_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)

                html = page.content()

                import re
                p_list = re.findall(r'<p[^>]*>(.*?)</p>', html, re.S)

                article_paragraphs = []
                for p_text in p_list:
                    clean_p = re.sub(r'<[^>]+>', '', p_text).strip()
                    if len(clean_p) > 15 and "原标题" not in clean_p and "责任编辑" not in clean_p:
                        article_paragraphs.append(clean_p)

                if article_paragraphs:
                    print(f"        [+] 成功提取正文: {len(article_paragraphs)} 条段落")
                    return "\n\n".join(article_paragraphs)
                else:
                    print(f"        [!] 页面加载完成但未发现有效正文格式（<p> 标签过少或为空）")
    except KeyboardInterrupt:
        raise
    except Exception as e:
        # 提供更详细的报错信息以便排查
        err_msg = str(e).splitlines()[0]
        print(f"        [!] 详情页抓取异常: {err_msg[:80]}...")
        
    if default_desc:
        import re
        clean_desc = re.sub(r'<[^>]+>', '', default_desc.replace("<br/>", "\n").replace("<p>", "\n").replace("</p>", "\n")).strip()
        clean_desc = '\n'.join([line for line in clean_desc.split('\n') if line.strip()])
        return f"（深入提取原网页正文失败/被拦截，以下为新闻摘要回退数据）\n\n> {clean_desc}"
        
    return "未能从该页面成功提取到有效正文结构，且无摘要回退记录。"

def parse_and_deduplicate(xml_data, keyword):
    """解析逻辑"""
    if not xml_data:
        return [], 0

    # 统一转换为 bytes 处理
    if isinstance(xml_data, str):
        xml_data_bytes = xml_data.encode("utf-8")
    else:
        xml_data_bytes = xml_data

    head = xml_data_bytes[:1000].lower()
    looks_like_html = b"<!doctype html" in head or b"<html" in head
    if looks_like_html:
        # 判定来源：百度通常包含 baidu.com 或特定样式类
        if b"baidu.com" in head or b"result-op news" in head or b"baidu" in head:
            print(f"    [d] 检测到百度 HTML 格式")
            items = _parse_baidu_html_results(xml_data_bytes, keyword)
        else:
            print(f"    [d] 检测到必应 HTML 格式")
            items = _parse_bing_html_results(xml_data_bytes, keyword)
            
        news_list = []
        expired_count = 0
        seen_links = set()
        seen_titles = set()
        pending_items = []
        for item in items:
            title = item.get("title", "").strip()
            link = item.get("link", "").strip()
            if not title or not link:
                continue
            if link in seen_links or title in seen_titles:
                continue
            seen_links.add(link)
            seen_titles.add(title)
            pending_items.append({
                "title": title,
                "link": link,
                "date": item.get("date") or datetime.date.today().strftime("%Y-%m-%d"),
            })
            if len(pending_items) >= 10:
                break

        def fetch_html_article(item):
            print(f"    - 正在抓取正文(HTML模式): {item['title'][:20]}...")
            import random
            import time
            time.sleep(random.uniform(0.5, 1.2))
            content = fetch_article_content(item['link'], "")
            return {
                "title": item["title"],
                "link": item["link"],
                "content": content,
                "date": item["date"],
            }

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            news_list = list(executor.map(fetch_html_article, pending_items))
            
        return news_list, expired_count
        
    try:
        # 对于 XML，ET 可以接受 bytes 或 string
        root = ET.fromstring(xml_data_bytes)
    except Exception as e:
        print(f"XML解析失败 [{keyword}]: {e}")
        return [], 0

    now_time = datetime.datetime.now().astimezone()
    today_date = now_time.date()
    news_list = []
    seen_links = set()
    seen_titles = set()
    expired_count = 0
    pending_items = []
    
    for item in root.findall('.//item'):
        title_elem = item.find('title')
        link_elem = item.find('link')
        desc_elem = item.find('description')
        pubdate_elem = item.find('pubDate')
        
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
        link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""
        desc = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else ""
        pubdate_str = pubdate_elem.text.strip() if pubdate_elem is not None and pubdate_elem.text else ""
        
        if not title or not link:
            continue
            
        is_expired = False
        if pubdate_str:
            try:
                dt = email.utils.parsedate_to_datetime(pubdate_str).astimezone()
                time_diff = now_time - dt
                if time_diff.total_seconds() > MAX_HOURS * 3600:
                    is_expired = True
            except Exception:
                pass
                
        if is_expired:
            expired_count += 1
            continue
            
        if link not in seen_links and title not in seen_titles:
            seen_links.add(link)
            seen_titles.add(title)
            pending_items.append({
                "title": title,
                "link": link,
                "desc": desc
            })
            if len(pending_items) >= 10:
                break

    def fetch_xml_article(item):
        print(f"    - 正在抓取正文: {item['title'][:20]}...")
        import random
        import time
        time.sleep(random.uniform(0.5, 1.5))
        content = fetch_article_content(item['link'], item['desc'])
        return {
            "title": item["title"],
            "link": item["link"],
            "content": content,
            "date": today_date.strftime("%Y-%m-%d")
        }

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        news_list = list(executor.map(fetch_xml_article, pending_items))
                    
    return news_list, expired_count

def save_to_markdown_file(keyword, news_list, expired_count, title_dir=None):
    if not news_list and expired_count == 0:
        print(f"关键词 '{keyword}' 未获取到任何相关动态")
        return
        
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    safe_keyword = keyword.replace("/", "_").replace("\\", "_").replace(" ", "")
    filename = f"{safe_keyword}.md"
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 按 日期/title_dir/ 子目录存放
    if title_dir:
        output_dir = os.path.join(script_dir, today_str, title_dir)
    else:
        output_dir = os.path.join(script_dir, today_str)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"# 【{keyword}】 每日新闻追踪\n\n")
        f.write(f"> **生成日期**: {today_str}\n")
        f.write(f"> **包含关键词**: {keyword}\n")
        f.write(f"> **收录有效新闻**: {len(news_list)} 条\n")
        
        if expired_count > 0:
            f.write(f"> **已忽略过期新闻**: {expired_count} 条\n")
            
        f.write(f"\n---\n\n")
        
        for i, news in enumerate(news_list, 1):
            f.write(f"### {i}. {news['title']}\n\n")
            f.write(f"- **原链接**: [{news['link']}]({news['link']})\n\n")
            f.write(f"**新闻正文**:\n\n{news['content']}\n\n")
            f.write("---\n\n")
            
    print(f"[*] 成功保存 {len(news_list)} 条关于 '{keyword}' 的新闻至：{filepath}")

def run_crawler(keywords=None, title_dir=None):
    """运行爬虫，支持外部传入关键词列表和主题子目录名"""
    kw_list = keywords if keywords is not None else KEYWORDS
    print("="*40)
    print("      今日新闻聚合程序启动 (Playwright版)")
    print("="*40)
    for kw in kw_list:
        print(f"\n>>>> 正在检索关键词：{kw}")
        try:
            xml_data = fetch_rss_xml(kw)
            news_items, expired_count = parse_and_deduplicate(xml_data, kw)
            save_to_markdown_file(kw, news_items, expired_count, title_dir=title_dir)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[!] 关键词 '{kw}' 处理失败：{e}")
    print("\n==== 所有爬取任务已完成！ ====")

if __name__ == '__main__':
    run_crawler()
