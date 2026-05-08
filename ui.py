import os
import sys
import json
import traceback
import datetime
import threading
import subprocess
import shutil
import tempfile
import glob


def configure_tk_runtime():
    base_prefix = getattr(sys, "base_prefix", "")
    tcl_dir = os.path.join(base_prefix, "tcl", "tcl8.6")
    tk_dir = os.path.join(base_prefix, "tcl", "tk8.6")
    if os.path.isdir(tcl_dir) and "TCL_LIBRARY" not in os.environ:
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.isdir(tk_dir) and "TK_LIBRARY" not in os.environ:
        os.environ["TK_LIBRARY"] = tk_dir


configure_tk_runtime()

import tkinter as tk
from tkinter import scrolledtext

import main


BG_COLOR = "#F5F5F7"
CARD_COLOR = "#FFFFFF"
PANEL_COLOR = "#FAFAFC"
TEXT_COLOR = "#1D1D1F"
SUBTEXT_COLOR = "#6E6E73"
PRIMARY_COLOR = "#007AFF"
SUCCESS_COLOR = "#34C759"
BORDER_COLOR = "#DADAE0"
SOFT_BLUE = "#EAF3FF"
BUTTON_DISABLED = "#B9C7D8"
LOG_BG = "#FBFBFD"
BILIBILI_DESC = (
    "每天为你精选人工智能、智能体与硬件领域的最新动态，追踪前沿技术突破与行业热点，助你高效掌握科技资讯。"
    "本视频由我自己手搓的 Python AI 爬虫全自动生成，本项目已经在开源。"
    "开源地址：https://github.com/maqi88933-stack/OpenNewsBrief。感兴趣的开发者欢迎交流"
)
BILIBILI_TAGS = "人工智能,智能体,硬件,科技资讯"
BILIBILI_TID = "171"


class NewsBriefApp:
    def __init__(self, root):
        self.root = root
        self.root.title("OpenNewsBrief")
        self.root.geometry("1220x800")
        self.root.minsize(1080, 680)
        self.root.configure(bg=BG_COLOR)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))

        self.topic_vars = []
        self.latest_result = {}
        self.is_running = False
        self.is_publishing = False
        self.worker_python = self.resolve_worker_python()
        self.worker_script = os.path.join(main.ROOT_DIR, "ui_worker.py")
        self.biliup_command = self.resolve_biliup_command()

        self.status_var = tk.StringVar(value="就绪")
        self.brief_var = tk.StringVar(value="未生成")
        self.audio_var = tk.StringVar(value="未生成")
        self.video_var = tk.StringVar(value="未生成")

        self.build_layout()

    def build_layout(self):
        header = tk.Frame(self.root, bg=BG_COLOR)
        header.pack(fill="x", padx=34, pady=(26, 18))

        tk.Label(
            header,
            text="OpenNewsBrief",
            font=("Segoe UI", 28, "bold"),
            bg=BG_COLOR,
            fg=TEXT_COLOR,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="更近的新闻、更稳的去重、直接输出音频和视频",
            font=("Microsoft YaHei UI", 11),
            bg=BG_COLOR,
            fg=SUBTEXT_COLOR,
        ).pack(anchor="w", pady=(8, 0))

        body = tk.Frame(self.root, bg=BG_COLOR)
        body.pack(fill="both", expand=True, padx=34, pady=(0, 30))

        left = tk.Frame(body, bg=BG_COLOR)
        left.pack(side="left", fill="y", ipadx=2)

        right = tk.Frame(body, bg=BG_COLOR)
        right.pack(side="left", fill="both", expand=True, padx=(22, 0))

        self.build_control_card(left)
        self.build_result_card(left)
        self.build_log_card(right)

    def build_control_card(self, parent):
        card = self.create_card(parent)
        card.pack(fill="x")

        inner = tk.Frame(card, bg=CARD_COLOR)
        inner.pack(fill="both", expand=True, padx=22, pady=22)

        tk.Label(
            inner,
            text="主题选择",
            font=("Microsoft YaHei UI", 16, "bold"),
            bg=CARD_COLOR,
            fg=TEXT_COLOR,
        ).pack(anchor="w")

        tk.Label(
            inner,
            text="默认会执行选中的主题，并自动生成音频和视频。",
            font=("Microsoft YaHei UI", 10),
            bg=CARD_COLOR,
            fg=SUBTEXT_COLOR,
        ).pack(anchor="w", pady=(8, 16))

        for topic in main.TOPICS:
            var = tk.BooleanVar(value=True)
            self.topic_vars.append((topic, var))
            row = tk.Frame(inner, bg=PANEL_COLOR, highlightthickness=1, highlightbackground="#EEEEF2")
            row.pack(fill="x", pady=5)
            tk.Checkbutton(
                row,
                text=topic["title"],
                variable=var,
                bg=PANEL_COLOR,
                fg=TEXT_COLOR,
                activebackground=PANEL_COLOR,
                activeforeground=TEXT_COLOR,
                selectcolor=PANEL_COLOR,
                font=("Microsoft YaHei UI", 11),
                anchor="w",
                relief="flat",
                padx=10,
                pady=8,
            ).pack(anchor="w", fill="x")

        button_row = tk.Frame(inner, bg=CARD_COLOR)
        button_row.pack(fill="x", pady=(18, 0))

        self.run_button = tk.Button(
            button_row,
            text="执行选中主题",
            command=self.start_run,
            bg=PRIMARY_COLOR,
            fg="white",
            activebackground="#0067D8",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 11, "bold"),
            padx=20,
            pady=12,
            cursor="hand2",
        )
        self.run_button.pack(fill="x")

        tk.Button(
            inner,
            text="打开最新输出目录",
            command=self.open_latest_dir,
            bg=SOFT_BLUE,
            fg=PRIMARY_COLOR,
            activebackground="#DDEEFF",
            activeforeground=PRIMARY_COLOR,
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=16,
            pady=10,
            cursor="hand2",
        ).pack(fill="x", pady=(12, 0))

        self.publish_button = tk.Button(
            inner,
            text="一键发布到B站",
            command=self.start_publish_to_bilibili,
            bg=SUCCESS_COLOR,
            fg="white",
            activebackground="#28A745",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self.publish_button.pack(fill="x", pady=(12, 0))

        tk.Label(
            inner,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 10),
            bg=CARD_COLOR,
            fg=SUCCESS_COLOR,
        ).pack(anchor="w", pady=(14, 0))

    def build_result_card(self, parent):
        card = self.create_card(parent)
        card.pack(fill="x", pady=(20, 0))

        inner = tk.Frame(card, bg=CARD_COLOR)
        inner.pack(fill="both", expand=True, padx=22, pady=22)

        tk.Label(
            inner,
            text="最新结果",
            font=("Microsoft YaHei UI", 16, "bold"),
            bg=CARD_COLOR,
            fg=TEXT_COLOR,
        ).pack(anchor="w")

        self.build_path_row(inner, "简报", self.brief_var, "brief_path")
        self.build_path_row(inner, "音频", self.audio_var, "audio_path")
        self.build_path_row(inner, "视频", self.video_var, "video_path")

    def build_path_row(self, parent, label, text_var, field_name):
        row = tk.Frame(parent, bg=CARD_COLOR)
        row.pack(fill="x", pady=(14, 0))

        tk.Label(
            row,
            text=label,
            width=6,
            anchor="w",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=CARD_COLOR,
            fg=TEXT_COLOR,
        ).pack(side="left")

        tk.Label(
            row,
            textvariable=text_var,
            anchor="w",
            justify="left",
            font=("Microsoft YaHei UI", 9),
            bg=CARD_COLOR,
            fg=SUBTEXT_COLOR,
            wraplength=240,
        ).pack(side="left", fill="x", expand=True)

        tk.Button(
            row,
            text="打开",
            command=lambda key=field_name: self.open_result_file(key),
            bg=CARD_COLOR,
            fg=PRIMARY_COLOR,
            activebackground=CARD_COLOR,
            activeforeground=PRIMARY_COLOR,
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        ).pack(side="right")

    def build_log_card(self, parent):
        card = self.create_card(parent)
        card.pack(fill="both", expand=True)

        inner = tk.Frame(card, bg=CARD_COLOR)
        inner.pack(fill="both", expand=True, padx=22, pady=22)

        top_row = tk.Frame(inner, bg=CARD_COLOR)
        top_row.pack(fill="x")

        tk.Label(
            top_row,
            text="运行日志",
            font=("Microsoft YaHei UI", 16, "bold"),
            bg=CARD_COLOR,
            fg=TEXT_COLOR,
        ).pack(side="left")

        tk.Button(
            top_row,
            text="清空",
            command=self.clear_log,
            bg=CARD_COLOR,
            fg=PRIMARY_COLOR,
            activebackground=CARD_COLOR,
            activeforeground=PRIMARY_COLOR,
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        ).pack(side="right")

        self.log_widget = scrolledtext.ScrolledText(
            inner,
            bg=LOG_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#EEEEF2",
            highlightcolor="#C7D2FE",
            font=("Cascadia Mono", 10),
            wrap="word",
            padx=14,
            pady=14,
        )
        self.log_widget.pack(fill="both", expand=True, pady=(14, 0))

    def create_card(self, parent):
        return tk.Frame(
            parent,
            bg=CARD_COLOR,
            bd=0,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
        )

    def get_selected_topics(self):
        return [topic for topic, var in self.topic_vars if var.get()]

    def start_run(self):
        if self.is_running:
            return

        topics = self.get_selected_topics()
        if not topics:
            self.append_log("请先至少选择一个主题。\n")
            return

        self.is_running = True
        self.run_button.config(state="disabled", bg=BUTTON_DISABLED)
        self.publish_button.config(state="disabled", bg=BUTTON_DISABLED)
        self.status_var.set("运行中...")
        worker = threading.Thread(target=self.run_topics, args=(topics,), daemon=True)
        worker.start()

    def run_topics(self, topics):
        try:
            if not os.path.exists(self.worker_python):
                self.append_log(f"未找到可用的任务解释器：{self.worker_python}\n")
                return

            for topic in topics:
                self.append_log(f"\n{'=' * 56}\n")
                self.append_log(f"开始处理：{topic['title']}\n")
                self.append_log(f"{'=' * 56}\n")
                result = self.run_topic_subprocess(topic["title"])
                if result:
                    self.latest_result = result
                    self.update_result_panel(result)
        except Exception:
            self.append_log(traceback.format_exc())
        finally:
            self.finish_run()

    def run_topic_subprocess(self, topic_title):
        command = [self.worker_python, "-X", "utf8", self.worker_script, topic_title]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        process = subprocess.Popen(
            command,
            cwd=main.ROOT_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        result = None
        if process.stdout is not None:
            for line in process.stdout:
                if line.startswith("__RESULT__"):
                    result = json.loads(line[len("__RESULT__"):].strip())
                    continue
                self.append_log(line)

        return_code = process.wait()
        if return_code != 0:
            self.append_log(f"处理失败，退出码：{return_code}\n")
        return result

    def resolve_worker_python(self):
        venv_python = os.path.join(main.ROOT_DIR, ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_python):
            return venv_python
        return sys.executable

    def finish_run(self):
        def _finish():
            self.is_running = False
            self.run_button.config(state="normal", bg=PRIMARY_COLOR)
            if not self.is_publishing:
                self.publish_button.config(state="normal", bg=SUCCESS_COLOR)
            self.status_var.set(f"完成 {datetime.datetime.now().strftime('%H:%M:%S')}")

        self.root.after(0, _finish)

    def update_result_panel(self, result):
        def _update():
            self.brief_var.set(result.get("brief_path") or "未生成")
            self.audio_var.set(result.get("audio_path") or "未生成")
            self.video_var.set(result.get("video_path") or "未生成")

        self.root.after(0, _update)

    def append_log(self, text):
        def _append():
            self.log_widget.insert("end", text)
            self.log_widget.see("end")

        self.root.after(0, _append)

    def clear_log(self):
        self.log_widget.delete("1.0", "end")

    def open_result_file(self, key):
        path = self.latest_result.get(key)
        if path and os.path.exists(path):
            os.startfile(path)

    def start_publish_to_bilibili(self):
        if self.is_running:
            self.append_log("请等待当前生成任务完成后再发布。\n")
            return
        if self.is_publishing:
            return

        video_path = self.get_publish_video_path()
        if not video_path:
            self.append_log("未找到可发布的视频，请先生成视频。\n")
            return

        self.biliup_command = self.resolve_biliup_command()
        if not self.biliup_command:
            self.append_log("未找到 biliup 命令，请先安装并登录 biliup 后再发布。\n")
            self.append_log("可选：设置 BILIUP_USER_COOKIE 指向 biliup 登录 cookie 文件。\n")
            return

        self.is_publishing = True
        self.publish_button.config(state="disabled", bg=BUTTON_DISABLED)
        self.run_button.config(state="disabled", bg=BUTTON_DISABLED)
        self.status_var.set("发布到B站中...")
        worker = threading.Thread(target=self.publish_to_bilibili, args=(video_path,), daemon=True)
        worker.start()

    def get_publish_video_path(self):
        video_path = self.latest_result.get("video_path")
        if video_path and os.path.exists(video_path):
            return video_path
        return self.find_latest_video_file()

    def find_latest_video_file(self):
        base_dir = os.path.join(main.ROOT_DIR, "audioContent")
        latest_path = ""
        latest_mtime = -1
        for root, _dirs, files in os.walk(base_dir, onerror=lambda _err: None):
            for filename in files:
                if not filename.lower().endswith(".mp4"):
                    continue
                path = os.path.join(root, filename)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtime > latest_mtime:
                    latest_path = path
                    latest_mtime = mtime
        return latest_path

    def resolve_biliup_command(self):
        command = shutil.which("biliup")
        if command:
            return command

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            bbup_biliup = os.path.join(local_app_data, "bbup-app", "binaries", "biliup.exe")
            if os.path.exists(bbup_biliup):
                return bbup_biliup

        return ""

    def resolve_biliup_cookie_path(self):
        env_cookie = os.environ.get("BILIUP_USER_COOKIE")
        if env_cookie and os.path.exists(env_cookie):
            return env_cookie

        project_cookie = os.path.join(main.ROOT_DIR, "cookies.json")
        if os.path.exists(project_cookie):
            return project_cookie

        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return ""

        pattern = os.path.join(local_app_data, "bbup-app", "data", "*.json")
        cookie_files = glob.glob(pattern)
        if not cookie_files:
            return ""

        cookie_files.sort(key=os.path.getmtime, reverse=True)
        temp_cookie = os.path.join(tempfile.gettempdir(), "OpenNewsBrief_biliup_cookie.json")
        try:
            shutil.copyfile(cookie_files[0], temp_cookie)
        except OSError:
            return ""
        return temp_cookie

    def build_biliup_upload_command(self, video_path):
        title = os.path.splitext(os.path.basename(video_path))[0]
        command = [self.biliup_command or "biliup"]
        cookie_path = self.resolve_biliup_cookie_path()
        if cookie_path:
            command.extend(["--user-cookie", cookie_path])
        command.extend([
            "upload",
            "--copyright",
            "1",
            "--tid",
            BILIBILI_TID,
            "--tag",
            BILIBILI_TAGS,
            "--title",
            title,
            "--desc",
            BILIBILI_DESC,
            video_path,
        ])
        return command

    def publish_to_bilibili(self, video_path):
        command = self.build_biliup_upload_command(video_path)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.append_log(f"开始发布到B站：{video_path}\n")
        try:
            process = subprocess.Popen(
                command,
                cwd=main.ROOT_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if process.stdout is not None:
                for line in process.stdout:
                    self.append_log(line)

            return_code = process.wait()
            if return_code == 0:
                self.append_log("B站发布完成。\n")
            else:
                self.append_log(f"B站发布失败，退出码：{return_code}\n")
        except Exception:
            self.append_log(traceback.format_exc())
        finally:
            self.finish_publish()

    def finish_publish(self):
        def _finish():
            self.is_publishing = False
            self.publish_button.config(state="normal", bg=SUCCESS_COLOR)
            if not self.is_running:
                self.run_button.config(state="normal", bg=PRIMARY_COLOR)
            self.status_var.set(f"发布结束 {datetime.datetime.now().strftime('%H:%M:%S')}")

        self.root.after(0, _finish)

    def open_latest_dir(self):
        for key in ("video_path", "audio_path", "brief_path"):
            path = self.latest_result.get(key)
            if path and os.path.exists(path):
                os.startfile(os.path.dirname(path))
                return

        today = datetime.date.today().strftime("%Y-%m-%d")
        fallback_dir = os.path.join(main.ROOT_DIR, "audioContent", today)
        if os.path.exists(fallback_dir):
            os.startfile(fallback_dir)


def launch():
    root = tk.Tk()
    app = NewsBriefApp(root)
    root.mainloop()
    return app


if __name__ == "__main__":
    launch()
