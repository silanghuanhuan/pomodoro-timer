#!/usr/bin/env python3
# Git learning: first local modification
"""
桌面番茄钟 — 基于 Python/tkinter 的暖色浅色主题生产力计时器。
==============================================================
使用方法：
    python pomodoro.py

核心设计思路：
  1. 用「挂钟时间」(time.time()) 驱动倒计时，而非每秒递减计数器。
     这样即使 tkinter 的 after 回调有微小延迟，计时依然精准。
  2. 状态机模型：idle → running ⇄ paused → idle（会话结束自动切模式）
  3. 三种模式自动流转：
     工作(25min) → 短休息(5min) → 工作(25min) → ... → 每4个番茄进入长休息(15min)
  4. 时长可通过预设按钮切换，也可用方向键微调，调过的值会自动持久化。
"""

import tkinter as tk
from tkinter import ttk
import time
import threading
import winsound          # Windows 系统蜂鸣，无需额外依赖
import os


# ══════════════════════════════════════════════════════════════════
#  配色方案：暖色浅色主题，三种模式各有主色调
# ══════════════════════════════════════════════════════════════════
class C:
    """
    颜色常量类 — 暖色浅色主题。
    配色参考：暖奶油色背景 + 玫瑰红主色调 + 灰蓝/金色点缀。
    """
    WORK        = "#D39394"   # 暖玫瑰红 — 工作模式（参考图番茄色）
    SHORT_BRK   = "#8FAD9A"   # 鼠尾草绿 — 短休息
    LONG_BRK    = "#70838F"   # 灰蓝色   — 长休息（参考图 slate 色）
    PAUSE       = "#E5CB96"   # 暖金色   — 暂停状态（参考图金色点缀）
    BG          = "#F8F5F0"   # 暖奶油色 — 全局背景
    SURFACE     = "#FFFFFF"   # 纯白     — 卡片/面板背景
    FG          = "#3B3530"   # 深棕黑   — 主文字色
    FG_ON_COLOR = "#FFFFFF"   # 白色     — 彩色按钮上的文字色
    SUBTEXT     = "#968F86"   # 暖灰色   — 次要文字色
    BTN_BG      = "#F0EBE5"   # 浅暖灰   — 默认按钮背景
    PROGRESS_BG = "#E8E3DB"   # 暖米色   — 进度条轨道
    # 按钮 hover / 按下时的加深色（彩色按钮上的文字同步切换为白色）
    HOVER_WORK  = "#C17F80"
    HOVER_SHORT = "#7A9A86"
    HOVER_LONG  = "#5E727D"
    HOVER_PAUSE = "#D4B97E"


# ══════════════════════════════════════════════════════════════════
#  主应用类
# ══════════════════════════════════════════════════════════════════
class PomodoroTimer:
    """
    番茄钟核心类，封装所有 UI 和计时逻辑。

    状态变量说明：
      - mode:       "work" | "short_break" | "long_break"
      - state:      "idle" | "running" | "paused"
      - remaining:  当前会话剩余秒数（浮点数）
      - total_seconds: 当前会话总秒数（用于计算进度百分比）
      - end_time:   挂钟截止时间（time.time() + remaining），仅在 running 时有效
      - sessions:   已完成的工作番茄数（用于判断长休息和圆点显示）
    """

    # 默认时长（分钟），会被持久化文件覆盖
    DEFAULTS = {"work": 25, "short_break": 5, "long_break": 15}
    # 每完成多少个工作番茄后触发一次长休息
    LONG_BREAK_INTERVAL = 4

    # ────────────────────────────────────────────────────────────
    #  初始化：创建窗口、加载设置、构建 UI、绑定快捷键
    # ────────────────────────────────────────────────────────────
    def __init__(self):
        # ---- 创建根窗口 ----
        self.root = tk.Tk()
        self.root.title("Pomodoro Timer")
        self.root.configure(bg=C.BG)

        # ---- 计算窗口居中位置 ----
        w, h = 440, 540
        sw = self.root.winfo_screenwidth()   # 屏幕宽度
        sh = self.root.winfo_screenheight()  # 屏幕高度
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(380, 460)          # 最小尺寸，防止布局挤压变形

        # ---- 初始化运行时状态 ----
        self.durations = {**self.DEFAULTS}   # 拷贝默认值
        self._load_settings()                # 从文件覆盖用户自定义时长
        self.mode = "work"                   # 默认从工作模式开始
        self._set_full_duration()            # 根据 mode 设置 remaining / total_seconds
        self.state = "idle"                  # 初始状态：空闲
        self.sessions = 0                    # 已完成番茄数
        self.end_time = None                 # 仅在 running 时有值
        self.always_on_top = tk.BooleanVar(value=False)
        self._timer_job = None               # 存储 tk.after 返回的 job id，用于取消

        # ---- 构建界面 → 绑定快捷键 → 启动刷新循环 ----
        self._build_ui()
        self._bind_keys()
        self._update_display_loop()          # 每 500ms 刷新一次（idle/paused 时的轻量循环）
        # 用户点击窗口关闭按钮时的回调
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ══════════════════════════════════════════════════════════════
    #  设置持久化：用户调整过的时长保存在脚本同目录的隐藏文件中
    # ══════════════════════════════════════════════════════════════
    def _settings_path(self):
        """返回设置文件的完整路径（与脚本同目录）。"""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".pomodoro_settings.txt")

    def _load_settings(self):
        """
        从 .pomodoro_settings.txt 读取用户保存的时长配置。
        文件格式：每行 key=value（例如 work=30）
        解析失败或文件不存在时静默跳过，使用默认值。
        """
        p = self._settings_path()
        if os.path.exists(p):
            try:
                with open(p) as f:
                    for line in f:
                        k, v = line.strip().split("=")
                        if k in self.durations:
                            self.durations[k] = int(v)
            except Exception:
                pass  # 文件损坏等异常，忽略即可

    def _save_settings(self):
        """
        将当前 durations 字典写入设置文件。
        写入失败时静默跳过（如无写权限）。
        """
        try:
            with open(self._settings_path(), "w") as f:
                for k, v in self.durations.items():
                    f.write(f"{k}={v}\n")
        except Exception:
            pass

    def _set_full_duration(self):
        """
        根据当前模式和 durations 字典，重置 remaining 和 total_seconds。
        通常用于：模式切换后、Reset 操作。
        """
        self.total_seconds = self.durations[self.mode] * 60
        self.remaining = self.total_seconds

    # ══════════════════════════════════════════════════════════════
    #  UI 构建：纯代码布局，无外部资源依赖
    # ══════════════════════════════════════════════════════════════
    def _build_ui(self):
        """
        整体布局结构（从上到下）：
          标题（🍅 + Pomodoro Timer）
          模式标签（🔴 FOCUS TIME / 🟢 SHORT BREAK / 🔵 LONG BREAK）
          计时卡片（大号数字 + 浅色背景面板）
          进度条（Canvas 绘制，随剩余时间减少从左到右填满）
          番茄圆点（◉ 已完成 / ○ 待完成，每 4 个一组）
          按钮行（Start/Pause/Resume + Reset）
          预设时长按钮（⏱25 / ☕5 / 💤15）
          底部栏（📌 Always on Top 勾选框 + Sessions 计数）
        """
        # 主容器，所有控件都放在 main 里
        main = tk.Frame(self.root, bg=C.BG)
        main.pack(fill=tk.BOTH, expand=True, padx=28, pady=(20, 12))

        # ---------- 标题区 ----------
        hdr = tk.Frame(main, bg=C.BG)
        hdr.pack(pady=(0, 8))
        tk.Label(hdr, text="🍅", font=("Segoe UI Emoji", 38),
                 bg=C.BG).pack()
        tk.Label(hdr, text="Pomodoro Timer", font=("Segoe UI", 15, "bold"),
                 fg=C.FG, bg=C.BG).pack()

        # ---------- 模式指示 ----------
        self.mode_lbl = tk.Label(main, text="", font=("Segoe UI", 10, "bold"),
                                 bg=C.BG)
        self.mode_lbl.pack(pady=(6, 0))

        # ---------- 计时卡片 ----------
        # 用 SURFACE 色作为卡片背景，让计时数字从页面中凸显出来
        card = tk.Frame(main, bg=C.SURFACE, highlightthickness=0,
                        padx=30, pady=24)
        card.pack(fill=tk.X, pady=(12, 6))

        self.timer_lbl = tk.Label(
            card, text="25:00", font=("Consolas", 68, "bold"),
            fg=C.FG, bg=C.SURFACE
        )
        self.timer_lbl.pack()

        # ---------- 进度条 ----------
        # 使用 Canvas 绘制进度条，可自由控制填充色与轨道色
        self.progress = tk.Canvas(
            main, height=5, bg=C.PROGRESS_BG, highlightthickness=0
        )
        self.progress.pack(fill=tk.X, pady=(0, 8))
        # 进度条上的填充矩形，初始宽度为 0
        self._progress_rect = self.progress.create_rectangle(
            0, 0, 0, 5, fill=C.WORK, outline=""
        )
        # 窗口大小变化时自动重绘进度条
        self.progress.bind("<Configure>", lambda e: self._update_progress())

        # ---------- 番茄圆点 ----------
        # 每完成一个工作番茄，就把一个 ○ 变成 ◉
        self.dots_lbl = tk.Label(main, text="", font=("Segoe UI Emoji", 15),
                                 fg=C.FG, bg=C.BG)
        self.dots_lbl.pack(pady=2)

        # ---------- 主按钮行 ----------
        btn_frame = tk.Frame(main, bg=C.BG)
        btn_frame.pack(pady=(14, 10))

        # Start / Pause / Resume 按钮（根据状态 text 和颜色会动态变化）
        self.start_btn = tk.Button(
            btn_frame, text="▶  Start", font=("Segoe UI", 12, "bold"),
            fg=C.FG, bg=C.WORK, activeforeground=C.FG,
            activebackground=C.HOVER_WORK,
            relief=tk.FLAT, padx=28, pady=9, cursor="hand2",
            command=self.toggle       # 根据当前状态自动判断行为
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)

        # Reset 按钮：回到当前模式的初始时长
        self.reset_btn = tk.Button(
            btn_frame, text="↺  Reset", font=("Segoe UI", 12),
            fg=C.SUBTEXT, bg=C.BTN_BG, activeforeground=C.SUBTEXT,
            activebackground="#E0DAD3",
            relief=tk.FLAT, padx=28, pady=9, cursor="hand2",
            command=self.reset
        )
        self.reset_btn.pack(side=tk.LEFT, padx=5)

        # ---------- 预设时长按钮 ----------
        dur_frame = tk.Frame(main, bg=C.BG)
        dur_frame.pack(pady=(4, 10))

        # 初始化按钮列表（用于 hover 效果和后续可能的动态管理）
        self._dur_buttons = []
        self._dur_btn("⏱  25", C.WORK, "work", dur_frame)
        self._dur_btn("☕  5",  C.SHORT_BRK, "short_break", dur_frame)
        self._dur_btn("💤  15", C.LONG_BRK, "long_break", dur_frame)

        # ---------- 底部栏 ----------
        bot = tk.Frame(main, bg=C.BG)
        bot.pack(fill=tk.X, pady=(12, 0))

        # 置顶勾选框
        self.top_cb = tk.Checkbutton(
            bot, text="📌 Always on Top", variable=self.always_on_top,
            command=self.toggle_always_on_top,
            fg=C.SUBTEXT, bg=C.BG, selectcolor=C.BG,
            activeforeground=C.FG, activebackground=C.BG,
            font=("Segoe UI", 9)
        )
        self.top_cb.pack(side=tk.LEFT)

        # 已完成番茄总数
        self.sess_lbl = tk.Label(
            bot, text="Sessions: 0", font=("Segoe UI", 9),
            fg=C.SUBTEXT, bg=C.BG
        )
        self.sess_lbl.pack(side=tk.RIGHT)

        # ---- 初始状态同步：将 UI 和数据模型对齐 ----
        self._sync_mode_ui()
        self._sync_timer_label()
        self._update_dots()

    def _dur_btn(self, text, color, mode, parent):
        """
        创建单个预设时长按钮，并绑定 hover 高亮效果。

        参数：
          text   — 按钮上显示的文字（含 emoji）
          color  — hover / 选中时的高亮色
          mode   — 对应模式名（"work" / "short_break" / "long_break"）
          parent — 父容器
        """
        # hover 进入：按钮背景变为该模式的代表色，文字变白
        def on_enter(e):
            e.widget.config(bg=color, fg="#FFFFFF")

        # hover 离开：按钮恢复默认浅色背景和深色文字
        def on_leave(e):
            e.widget.config(bg=C.BTN_BG, fg=C.FG)

        btn = tk.Button(
            parent, text=text, font=("Segoe UI", 10),
            fg=C.FG, bg=C.BTN_BG,
            activeforeground=C.FG, activebackground=color,
            relief=tk.FLAT, padx=14, pady=6, cursor="hand2",
            # 使用默认参数立即捕获 mode，避免 lambda 闭包延迟绑定问题
            command=lambda m=mode: self._pick_preset(m)
        )
        btn.pack(side=tk.LEFT, padx=3)
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        self._dur_buttons.append((btn, mode, color))

    # ══════════════════════════════════════════════════════════════
    #  键盘快捷键
    # ══════════════════════════════════════════════════════════════
    def _bind_keys(self):
        """绑定全局键盘快捷键（焦点在任意控件时均生效）。"""
        self.root.bind("<space>",     lambda e: self.toggle())                # 空格 = 开始/暂停
        self.root.bind("<Escape>",    lambda e: self.reset())                 # Esc  = 重置
        self.root.bind("<Control-t>", lambda e: self.toggle_always_on_top())  # Ctrl+T = 切换置顶
        self.root.bind("<Control-1>", lambda e: self._pick_preset("work"))
        self.root.bind("<Control-2>", lambda e: self._pick_preset("short_break"))
        self.root.bind("<Control-3>", lambda e: self._pick_preset("long_break"))
        # 方向键微调时长（仅在 idle 状态下有效）
        self.root.bind("<Up>",    lambda e: self._nudge(60))    # ↑ +1分钟
        self.root.bind("<Down>",  lambda e: self._nudge(-60))   # ↓ -1分钟
        self.root.bind("<Right>", lambda e: self._nudge(300))   # → +5分钟
        self.root.bind("<Left>",  lambda e: self._nudge(-300))  # ← -5分钟

    # ══════════════════════════════════════════════════════════════
    #  预设模式切换
    # ══════════════════════════════════════════════════════════════
    def _pick_preset(self, mode):
        """
        点击预设按钮 / Ctrl+数字 时调用。
        仅在 idle 状态可切换（running 时忽略，防止误操作）。
        切换后重置为对应模式的默认时长，并持久化。
        """
        if self.state == "running":
            return  # 运行中不允许切换模式
        self.mode = mode
        self._set_full_duration()      # 重置 remaining 为模式对应时长
        self._sync_mode_ui()
        self._sync_timer_label()
        self._update_progress()
        self._save_settings()

    def _nudge(self, delta):
        """
        方向键微调时长（秒）。
        仅在 idle 状态生效。
        限制范围：1分钟 ~ 90分钟。
        调整后立即持久化，下次启动保持。
        """
        if self.state != "idle":
            return
        new = self.remaining + delta
        if new < 60:
            new = 60
        elif new > 90 * 60:
            new = 90 * 60
        self.remaining = new
        self.total_seconds = new
        self.durations[self.mode] = new // 60   # 存为整数分钟
        self._sync_timer_label()
        self._update_progress()
        self._save_settings()

    # ══════════════════════════════════════════════════════════════
    #  核心状态机：idle → running ⇄ paused → idle（完成）
    # ══════════════════════════════════════════════════════════════
    def toggle(self):
        """
        Start / Pause / Resume 按钮的统一入口。
        根据当前 state 决定执行哪个动作：
          idle    → _start()
          running → _pause()
          paused  → _resume()
        """
        if self.state == "idle":
            self._start()
        elif self.state == "running":
            self._pause()
        elif self.state == "paused":
            self._resume()

    def _start(self):
        """
        开始计时。
        记录挂钟截止时间 end_time（当前时间 + 剩余秒数）。
        这样后续通过 time.time() 反算剩余时间，即便 after 回调有延迟也不累积误差。
        """
        self.state = "running"
        self.end_time = time.time() + self.remaining
        self.start_btn.config(text="⏸  Pause", bg=C.PAUSE,
                              fg=C.FG, activeforeground=C.FG,
                              activebackground=C.HOVER_PAUSE)
        # 任务栏标题实时显示剩余时间和状态符号
        self.root.title(f"▶ {self._fmt(self.remaining)} · Pomodoro")
        self._schedule_tick()   # 启动 150ms 间隔的刷新循环

    def _pause(self):
        """
        暂停计时。
        从 end_time 反算剩余时间并保存，然后清空 end_time。
        """
        self.state = "paused"
        self.remaining = max(0, self.end_time - time.time())
        self.end_time = None
        self.start_btn.config(text="▶  Resume", bg=C.WORK,
                              fg=C.FG, activeforeground=C.FG,
                              activebackground=C.HOVER_WORK)
        self.root.title(f"⏸ {self._fmt(self.remaining)} · Pomodoro")
        self._cancel_tick()     # 停止刷新循环

    def _resume(self):
        """
        从暂停恢复。
        重新计算 end_time = 当前时间 + 已保存的 remaining。
        """
        self.state = "running"
        self.end_time = time.time() + self.remaining
        self.start_btn.config(text="⏸  Pause", bg=C.PAUSE,
                              fg=C.FG, activeforeground=C.FG,
                              activebackground=C.HOVER_PAUSE)
        self.root.title(f"▶ {self._fmt(self.remaining)} · Pomodoro")
        self._schedule_tick()

    def reset(self):
        """
        重置当前会话。
        回到 idle 状态，remaining 恢复为当前模式的完整时长。
        """
        self.state = "idle"
        self._cancel_tick()
        self.end_time = None
        self._set_full_duration()
        self.start_btn.config(text="▶  Start", bg=C.WORK,
                              fg=C.FG, activeforeground=C.FG,
                              activebackground=C.HOVER_WORK)
        self.root.title("Pomodoro Timer")
        self._sync_timer_label()
        self._update_progress()

    # ══════════════════════════════════════════════════════════════
    #  计时循环（基于挂钟时间，保证精准）
    # ══════════════════════════════════════════════════════════════
    def _schedule_tick(self):
        """
        启动计时刷新循环。
        先取消旧的循环（防止重复调度），再注册新的。
        刷新间隔 150ms，比 1s 更密集，保证秒数跳变视觉上及时。
        """
        self._cancel_tick()
        self._timer_job = self.root.after(150, self._tick)

    def _cancel_tick(self):
        """
        取消正在进行的计时循环。
        通过 tk.after_cancel 清除已注册的回调。
        """
        if self._timer_job is not None:
            self.root.after_cancel(self._timer_job)
            self._timer_job = None

    def _tick(self):
        """
        每一次计时滴答。
        1. 检查是否仍在 running（防御性判断）
        2. 用 time.time() 反算剩余秒数（挂钟时间驱动，无累积误差）
        3. 刷新 UI（标签、进度条、窗口标题）
        4. 如果时间到 → 触发 _session_complete()，否则注册下一次 tick
        """
        if self.state != "running":
            return

        self.remaining = max(0, self.end_time - time.time())
        self._sync_timer_label()
        self._update_progress()
        # 在任务栏标题中显示进度，方便不切窗口就能看到剩余时间
        self.root.title(f"▶ {self._fmt(self.remaining)} · Pomodoro")

        if self.remaining <= 0:
            self._session_complete()
        else:
            self._timer_job = self.root.after(150, self._tick)

    def _update_display_loop(self):
        """
        轻量级后台刷新循环（每 500ms 一次）。
        仅在 idle / paused 时运行，确保微调时长后 UI 立即更新。
        计时期间由 _tick() 负责刷新，本循环相当于闲置时的保底刷新。
        """
        if self.state != "running":
            self._sync_timer_label()
            self._update_progress()
        self.root.after(500, self._update_display_loop)

    # ══════════════════════════════════════════════════════════════
    #  会话完成：自动切换模式 + 通知
    # ══════════════════════════════════════════════════════════════
    def _session_complete(self):
        """
        计时归零时调用。
        工作流程：
          1. 停止计时循环
          2. 如果当前是工作模式 → sessions++，判断进入短休息还是长休息
          3. 如果当前是休息模式 → 切换回工作模式
          4. 重置新模式的时长
          5. 播放蜂鸣通知 + 短暂置顶闪烁
        """
        self._cancel_tick()
        self.state = "idle"
        self.remaining = 0
        self.end_time = None
        self._sync_timer_label()
        self._update_progress()

        if self.mode == "work":
            # 完成一个工作番茄
            self.sessions += 1
            self._update_dots()
            self.sess_lbl.config(text=f"Sessions: {self.sessions}")
            # 每 4 个番茄 → 长休息，否则短休息
            next_mode = ("long_break"
                         if self.sessions % self.LONG_BREAK_INTERVAL == 0
                         else "short_break")
        else:
            # 休息结束 → 开始新的工作番茄
            next_mode = "work"

        self.mode = next_mode
        self._set_full_duration()   # 切换到新模式的时长
        self._sync_mode_ui()
        self._sync_timer_label()
        self._update_progress()

        self.start_btn.config(text="▶  Start", bg=C.WORK,
                              fg=C.FG, activeforeground=C.FG,
                              activebackground=C.HOVER_WORK)
        self.root.title("Pomodoro Timer")
        self._notify()

    # ══════════════════════════════════════════════════════════════
    #  通知：蜂鸣 + 窗口短暂置顶闪烁
    # ══════════════════════════════════════════════════════════════
    def _notify(self):
        """
        在独立线程中播放蜂鸣音效（避免阻塞 UI 线程）。
        同时让窗口短暂置顶闪烁，吸引注意力后恢复原有置顶状态。
        """
        def _play():
            if self.mode == "work":
                # 工作开始 — 激昂的三连音
                winsound.Beep(880, 120)
                winsound.Beep(1100, 250)
                time.sleep(0.08)
                winsound.Beep(880, 120)
                winsound.Beep(1320, 350)
            elif self.mode in ("short_break", "long_break"):
                # 休息开始 — 舒缓上升音
                winsound.Beep(1000, 150)
                time.sleep(0.12)
                winsound.Beep(1200, 150)
                time.sleep(0.12)
                winsound.Beep(1400, 300)

        threading.Thread(target=_play, daemon=True).start()

        # 窗口短暂置顶：强制置顶 180ms 后恢复用户设定
        self.root.attributes('-topmost', True)
        self.root.after(180, lambda: self.root.attributes(
            '-topmost', self.always_on_top.get()))

    # ══════════════════════════════════════════════════════════════
    #  UI 同步工具方法：将数据状态反映到界面上
    # ══════════════════════════════════════════════════════════════
    def _sync_mode_ui(self):
        """
        根据当前 mode 更新：
          - 模式标签文字和颜色
          - 进度条填充颜色
        """
        if self.mode == "work":
            self.mode_lbl.config(text="🔴  FOCUS TIME", fg=C.WORK)
            self.progress.itemconfig(self._progress_rect, fill=C.WORK)
        elif self.mode == "short_break":
            self.mode_lbl.config(text="🟢  SHORT BREAK", fg=C.SHORT_BRK)
            self.progress.itemconfig(self._progress_rect, fill=C.SHORT_BRK)
        else:
            self.mode_lbl.config(text="🔵  LONG BREAK", fg=C.LONG_BRK)
            self.progress.itemconfig(self._progress_rect, fill=C.LONG_BRK)

    def _sync_timer_label(self):
        """把 remaining 秒数格式化为 MM:SS 并写入标签。"""
        self.timer_lbl.config(text=self._fmt(self.remaining))

    def _update_progress(self):
        """
        更新进度条。
        进度 = 1 - remaining / total_seconds。
        当 remaining == 0 时进度为 100%。
        Canvas 的 coords() 方法直接设置填充矩形的右边界坐标。
        """
        if self.total_seconds > 0:
            frac = min(1.0, 1 - self.remaining / self.total_seconds)
        else:
            frac = 1.0   # 防御：总时长为 0 时视为完成
        w = self.progress.winfo_width()
        self.progress.coords(self._progress_rect, 0, 0, w * frac, 5)

    def _update_dots(self):
        """
        更新番茄圆点。
        每 4 个番茄为一组：◉ = 已完成，○ = 待完成。
        例：完成 3 个 → "◉  ◉  ◉  ○"
        """
        n = self.sessions % self.LONG_BREAK_INTERVAL
        total = self.LONG_BREAK_INTERVAL
        chars = ["◉"] * n + ["○"] * (total - n)
        self.dots_lbl.config(text="  ".join(chars))

    def toggle_always_on_top(self):
        """切换窗口置顶状态。"""
        self.root.attributes('-topmost', self.always_on_top.get())

    @staticmethod
    def _fmt(seconds):
        """
        秒数 → MM:SS 格式化。
        负数保护：max(0, int(seconds)) 确保不会显示负时间。
        """
        s = max(0, int(seconds))
        m, sec = divmod(s, 60)
        return f"{m:02d}:{sec:02d}"

    # ══════════════════════════════════════════════════════════════
    #  关闭窗口：清理资源 + 保存设置
    # ══════════════════════════════════════════════════════════════
    def _on_close(self):
        """
        用户点击窗口关闭按钮时的回调。
        1. 取消所有待执行的 after 回调
        2. 保存用户自定义时长
        3. 销毁窗口
        """
        self._cancel_tick()
        self._save_settings()
        self.root.destroy()

    def run(self):
        """启动 tkinter 主事件循环。"""
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════
#  程序入口
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    PomodoroTimer().run()
