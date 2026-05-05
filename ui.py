import os
import sys
import json
import traceback
import datetime
import threading
import subprocess


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
        self.worker_python = self.resolve_worker_python()
        self.worker_script = os.path.join(main.ROOT_DIR, "ui_worker.py")

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
