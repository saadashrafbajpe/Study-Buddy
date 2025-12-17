import json
import os
import threading
import time
from datetime import datetime, date, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

DATA_FILE = "study_data.json"
LOCK = threading.Lock()

# ---------- Data Layer ----------

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "tasks": [],
            "settings": {
                "pomodoro_work_mins": 25,
                "pomodoro_break_mins": 5,
                "long_break_mins": 15,
                "pomodoro_cycles_before_long": 4
            },
            "history": {}
        }
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with LOCK:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)


def add_task(data, title, est_mins, deadline=None, priority=3):
    task = {
        "id": int(time.time() * 1000),
        "title": title,
        "est_mins": int(est_mins),
        "deadline": deadline.isoformat() if isinstance(deadline, date) else deadline,
        "priority": int(priority),
        "done": False,
        "created": datetime.now().isoformat()
    }
    data["tasks"].append(task)
    save_data(data)
    return task


def update_task(data, task_id, **kwargs):
    for t in data["tasks"]:
        if t["id"] == task_id:
            for k, v in kwargs.items():
                if k == "deadline" and isinstance(v, date):
                    t[k] = v.isoformat()
                else:
                    t[k] = v
            save_data(data)
            return t
    return None


def remove_task(data, task_id):
    before = len(data["tasks"])
    data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
    if len(data["tasks"]) != before:
        save_data(data)
        return True
    return False

# ---------- Scheduler (heuristic "AI") ----------

def generate_schedule_for_day(data, study_start_time=None, study_end_time=None):
    """
    Creates a simple schedule for today by selecting unfinished tasks and fitting
    them into available study time between study_start_time and study_end_time.
    Uses a greedy approach: sort by (done, deadline soonness, priority, shorter est).
    study_start_time and study_end_time are datetime.time objects (optional).
    Returns a list of schedule entries with start/end datetimes and task reference.
    """
    today = date.today()
    # Default study window: 08:00 - 22:00
    if study_start_time is None:
        study_start_time = datetime.combine(today, datetime.min.time()).replace(hour=8)
    else:
        study_start_time = datetime.combine(today, study_start_time)
    if study_end_time is None:
        study_end_time = datetime.combine(today, datetime.min.time()).replace(hour=22)
    else:
        study_end_time = datetime.combine(today, study_end_time)

    unfinished = [t for t in data["tasks"] if not t.get("done", False)]

    def deadline_key(t):
        if t.get("deadline"):
            try:
                return datetime.fromisoformat(t["deadline"])
            except Exception:
                return datetime.max
        return datetime.max

    # Sort by soonest deadline, highest priority (lower number = higher priority), shorter estimate
    unfinished.sort(key=lambda t: (deadline_key(t), t.get("priority", 3), t.get("est_mins", 60)))

    schedule = []
    cursor = study_start_time
    while unfinished and cursor < study_end_time:
        t = unfinished[0]
        dur = timedelta(minutes=t.get("est_mins", 30))
        end_time = cursor + dur
        # If task has a deadline earlier than end_time, still schedule but mark as urgent
        if end_time > study_end_time:
            break
        schedule.append({
            "task_id": t["id"],
            "title": t["title"],
            "start": cursor.isoformat(),
            "end": end_time.isoformat(),
            "est_mins": t["est_mins"],
            "priority": t.get("priority", 3)
        })
        cursor = end_time
        unfinished.pop(0)

    return schedule

# ---------- Pomodoro Timer ----------

class PomodoroTimer(threading.Thread):
    def __init__(self, work_mins, break_mins, long_break_mins, cycles_before_long, on_tick=None, on_state_change=None):
        super().__init__(daemon=True)
        self.work_mins = work_mins
        self.break_mins = break_mins
        self.long_break_mins = long_break_mins
        self.cycles_before_long = cycles_before_long
        self.on_tick = on_tick
        self.on_state_change = on_state_change
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._lock = threading.Lock()
        self.state = "stopped"  # running, break, long_break, stopped
        self._current_seconds = 0
        self._cycle_count = 0

    def run_timer_seconds(self, seconds):
        self._current_seconds = seconds
        while self._current_seconds > 0 and not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(0.3)
                continue
            time.sleep(1)
            self._current_seconds -= 1
            if self.on_tick:
                self.on_tick(self._current_seconds)
        return self._current_seconds <= 0 and not self._stop_event.is_set()

    def run(self):
        while not self._stop_event.is_set():
            # Work interval
            self.state = "running"
            if self.on_state_change:
                self.on_state_change(self.state)
            if not self.run_timer_seconds(self.work_mins * 60):
                break
            self._cycle_count += 1
            # Decide between long or short break
            if self._cycle_count % self.cycles_before_long == 0:
                self.state = "long_break"
                if self.on_state_change:
                    self.on_state_change(self.state)
                if not self.run_timer_seconds(self.long_break_mins * 60):
                    break
            else:
                self.state = "break"
                if self.on_state_change:
                    self.on_state_change(self.state)
                if not self.run_timer_seconds(self.break_mins * 60):
                    break
        self.state = "stopped"
        if self.on_state_change:
            self.on_state_change(self.state)

    def start_timer(self):
        if self.is_alive():
            return
        self._stop_event.clear()
        self._pause_event.clear()
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop_timer(self):
        self._stop_event.set()
        self._pause_event.clear()

    def pause(self):
        self._pause_event.set()

    def resume(self):
        self._pause_event.clear()

# ---------- GUI ----------

class StudyAssistantApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Study Buddy - Your AI Study Assistant")
        self.data = load_data()
        self.pomo = PomodoroTimer(
            work_mins=self.data["settings"]["pomodoro_work_mins"],
            break_mins=self.data["settings"]["pomodoro_break_mins"],
            long_break_mins=self.data["settings"]["long_break_mins"],
            cycles_before_long=self.data["settings"]["pomodoro_cycles_before_long"],
            on_tick=self.on_pomo_tick,
            on_state_change=self.on_pomo_state_change
        )
        self.create_widgets()
        self.refresh_task_list()
        self.schedule = []

    def create_widgets(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True)

        # Tasks tab
        frame_tasks = ttk.Frame(nb)
        nb.add(frame_tasks, text="Tasks & Checklist")

        # create tree with title as a normal column (recommended)
        self.task_tree = ttk.Treeview(frame_tasks,
                                      columns=("title", "est", "deadline", "prio", "done"),
                                      show="headings")
        self.task_tree.heading("title", text="Title")
        self.task_tree.heading("est", text="Est (mins)")
        self.task_tree.heading("deadline", text="Deadline")
        self.task_tree.heading("prio", text="Priority")
        self.task_tree.heading("done", text="Done")

        # column widths & alignment (adjust as you like)
        self.task_tree.column("title", width=450, anchor=tk.W)
        self.task_tree.column("est", width=80, anchor=tk.CENTER)
        self.task_tree.column("deadline", width=130, anchor=tk.CENTER)
        self.task_tree.column("prio", width=80, anchor=tk.CENTER)
        self.task_tree.column("done", width=80, anchor=tk.CENTER)
        task_controls = ttk.Frame(frame_tasks)
        task_controls.pack(side=tk.RIGHT, fill=tk.Y, padx=8, pady=8)

        ttk.Button(task_controls, text="Add Task", command=self.add_task_dialog).pack(fill=tk.X)
        ttk.Button(task_controls, text="Edit Selected", command=self.edit_selected).pack(fill=tk.X)
        ttk.Button(task_controls, text="Remove Selected", command=self.remove_selected).pack(fill=tk.X)
        ttk.Button(task_controls, text="Toggle Done", command=self.toggle_done_selected).pack(fill=tk.X)
        ttk.Button(task_controls, text="Generate Today's Schedule", command=self.generate_schedule).pack(fill=tk.X)
        ttk.Button(task_controls, text="Save Now", command=lambda: save_data(self.data)).pack(fill=tk.X)

        # pack the tree so it becomes visible, and allow it to expand
        self.task_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # bind double click to show task details
        self.task_tree.bind('<Double-1>', self.on_task_double_click)

        # optional: add a vertical scrollbar for the tree
        vsb = ttk.Scrollbar(frame_tasks, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.LEFT, fill=tk.Y)

        # Schedule tab
        frame_sched = ttk.Frame(nb)
        nb.add(frame_sched, text="Today's Schedule")
        self.schedule_list = tk.Listbox(frame_sched)
        self.schedule_list.pack(fill=tk.BOTH, expand=True)
        ttk.Button(frame_sched, text="Apply Schedule (mark tasks done as scheduled)", command=self.apply_schedule).pack(fill=tk.X)

        # Pomodoro tab
        frame_pomo = ttk.Frame(nb)
        nb.add(frame_pomo, text="Pomodoro Timer")
        pomo_frame = ttk.Frame(frame_pomo)
        pomo_frame.pack(pady=12)

        self.pomo_label = ttk.Label(pomo_frame, text="Timer: 00:00")
        self.pomo_label.pack()
        self.pomo_state_label = ttk.Label(pomo_frame, text="State: stopped")
        self.pomo_state_label.pack()

        btns = ttk.Frame(frame_pomo)
        btns.pack(pady=8)
        ttk.Button(btns, text="Start", command=self.start_pomo).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="Pause", command=self.pause_pomo).grid(row=0, column=1, padx=4)
        ttk.Button(btns, text="Resume", command=self.resume_pomo).grid(row=0, column=2, padx=4)
        ttk.Button(btns, text="Stop", command=self.stop_pomo).grid(row=0, column=3, padx=4)

        # Settings tab
        frame_settings = ttk.Frame(nb)
        nb.add(frame_settings, text="Settings & Stats")
        settings_frame = ttk.Frame(frame_settings)
        settings_frame.pack(padx=10, pady=10)

        ttk.Label(settings_frame, text="Work minutes:").grid(row=0, column=0, sticky=tk.W)
        self.work_var = tk.IntVar(value=self.data["settings"]["pomodoro_work_mins"])
        ttk.Entry(settings_frame, textvariable=self.work_var).grid(row=0, column=1)

        ttk.Label(settings_frame, text="Break minutes:").grid(row=1, column=0, sticky=tk.W)
        self.break_var = tk.IntVar(value=self.data["settings"]["pomodoro_break_mins"])
        ttk.Entry(settings_frame, textvariable=self.break_var).grid(row=1, column=1)

        ttk.Label(settings_frame, text="Long break minutes:").grid(row=2, column=0, sticky=tk.W)
        self.long_var = tk.IntVar(value=self.data["settings"]["long_break_mins"])
        ttk.Entry(settings_frame, textvariable=self.long_var).grid(row=2, column=1)

        ttk.Label(settings_frame, text="Cycles before long break:").grid(row=3, column=0, sticky=tk.W)
        self.cycles_var = tk.IntVar(value=self.data["settings"]["pomodoro_cycles_before_long"])
        ttk.Entry(settings_frame, textvariable=self.cycles_var).grid(row=3, column=1)

        ttk.Button(settings_frame, text="Save Settings", command=self.save_settings).grid(row=4, column=0, columnspan=2, pady=8)

        # Stats
        stats_frame = ttk.LabelFrame(frame_settings, text="Today's Summary")
        stats_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.stats_label = ttk.Label(stats_frame, text="(no stats yet)")
        self.stats_label.pack()
        ttk.Button(stats_frame, text="Refresh Stats", command=self.refresh_stats).pack()

    # ---------- Task UI operations ----------

    def refresh_task_list(self):
        for i in self.task_tree.get_children():
            self.task_tree.delete(i)
        for t in sorted(self.data["tasks"], key=lambda x: x.get("priority", 3)):
            deadline = t.get("deadline") or ""
            self.task_tree.insert("", tk.END, iid=str(t["id"]),
                                  values=(t.get("title"),
                                          t.get("est_mins"),
                                          deadline,
                                          t.get("priority"),
                                          str(t.get("done", False))))

    def add_task_dialog(self):
        dlg = AddTaskDialog(self.root)
        self.root.wait_window(dlg.top)
        if dlg.result:
            title, est, dl, pr = dlg.result
            add_task(self.data, title, est, dl, pr)
            self.refresh_task_list()

    def on_task_double_click(self, event):
        item = self.task_tree.focus()
        if not item:
            return
        tid = int(item)
        t = next((x for x in self.data["tasks"] if x["id"] == tid), None)
        if not t:
            return
        # show details
        msg = f"Title: {t['title']}\nEstimate: {t['est_mins']} mins\nPriority: {t.get('priority')}\nDeadline: {t.get('deadline')}\nDone: {t.get('done')}"
        messagebox.showinfo("Task details", msg)

    def edit_selected(self):
        sel = self.task_tree.selection()
        if not sel:
            messagebox.showwarning("No select", "Select a task first")
            return
        tid = int(sel[0])
        t = next((x for x in self.data["tasks"] if x["id"] == tid), None)
        if not t:
            return
        dlg = AddTaskDialog(self.root, prefill=t)
        self.root.wait_window(dlg.top)
        if dlg.result:
            title, est, dl, pr = dlg.result
            update_task(self.data, tid, title=title, est_mins=int(est), deadline=dl.isoformat() if dl else None, priority=int(pr))
            self.refresh_task_list()

    def remove_selected(self):
        sel = self.task_tree.selection()
        if not sel:
            messagebox.showwarning("No select", "Select a task first")
            return
        tid = int(sel[0])
        if messagebox.askyesno("Confirm", "Remove selected task?"):
            remove_task(self.data, tid)
            self.refresh_task_list()

    def toggle_done_selected(self):
        sel = self.task_tree.selection()
        if not sel:
            messagebox.showwarning("No select", "Select a task first")
            return
        tid = int(sel[0])
        t = next((x for x in self.data["tasks"] if x["id"] == tid), None)
        if not t:
            return
        update_task(self.data, tid, done=not t.get("done", False))
        self.refresh_task_list()

    # ---------- Scheduling ----------

    def generate_schedule(self):
        # Ask user for study window optionally
        start_str = simpledialog.askstring("Start time", "Enter study start time (HH:MM) or leave blank for 08:00")
        end_str = simpledialog.askstring("End time", "Enter study end time (HH:MM) or leave blank for 22:00")
        def parse_t(s):
            if not s:
                return None
            try:
                h,m = map(int, s.split(":"))
                return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0).time()
            except Exception:
                return None
        st = parse_t(start_str)
        et = parse_t(end_str)
        self.schedule = generate_schedule_for_day(self.data, study_start_time=st, study_end_time=et)
        self.schedule_list.delete(0, tk.END)
        for e in self.schedule:
            s = f"{e['start'][11:16]} - {e['end'][11:16]} | {e['title']} ({e['est_mins']}m)"
            self.schedule_list.insert(tk.END, s)
        if not self.schedule:
            messagebox.showinfo("Schedule", "No tasks could be scheduled in the given window.")

    def apply_schedule(self):
        if not self.schedule:
            messagebox.showwarning("No schedule", "Generate a schedule first")
            return
        for e in self.schedule:
            update_task(self.data, e['task_id'], done=True)
        save_data(self.data)
        self.refresh_task_list()
        messagebox.showinfo("Applied", "All scheduled tasks have been marked done.")

    # ---------- Pomodoro controls and callbacks ----------

    def start_pomo(self):
        # update settings to timer
        self.pomo.work_mins = self.work_var.get()
        self.pomo.break_mins = self.break_var.get()
        self.pomo.long_break_mins = self.long_var.get()
        self.pomo.cycles_before_long = self.cycles_var.get()
        # start
        if hasattr(self.pomo, '_thread') and self.pomo._thread.is_alive():
            messagebox.showinfo("Already running", "Pomodoro is already running")
            return
        self.pomo = PomodoroTimer(
            work_mins=self.work_var.get(),
            break_mins=self.break_var.get(),
            long_break_mins=self.long_var.get(),
            cycles_before_long=self.cycles_var.get(),
            on_tick=self.on_pomo_tick,
            on_state_change=self.on_pomo_state_change
        )
        self.pomo.start_timer()

    def pause_pomo(self):
        self.pomo.pause()

    def resume_pomo(self):
        self.pomo.resume()

    def stop_pomo(self):
        self.pomo.stop_timer()

    def on_pomo_tick(self, seconds_left):
        mins = seconds_left // 60
        secs = seconds_left % 60
        self.pomo_label.config(text=f"Timer: {mins:02d}:{secs:02d}")

    def on_pomo_state_change(self, state):
        self.pomo_state_label.config(text=f"State: {state}")
        # Very simple local notification
        if state == "break":
            print("Take a short break!")
        elif state == "long_break":
            print("Take a long break!")
        elif state == "running":
            print("Focus time!")

    # ---------- Settings & Stats ----------

    def save_settings(self):
        self.data["settings"]["pomodoro_work_mins"] = int(self.work_var.get())
        self.data["settings"]["pomodoro_break_mins"] = int(self.break_var.get())
        self.data["settings"]["long_break_mins"] = int(self.long_var.get())
        self.data["settings"]["pomodoro_cycles_before_long"] = int(self.cycles_var.get())
        save_data(self.data)
        messagebox.showinfo("Saved", "Settings saved")

    def refresh_stats(self):
        total = len(self.data.get("tasks", []))
        done = len([t for t in self.data.get("tasks", []) if t.get("done")])
        today_done = 0
        # quick check in history
        s = f"Total tasks: {total}\nCompleted: {done}\n"
        self.stats_label.config(text=s)


class AddTaskDialog:
    def __init__(self, parent, prefill=None):
        top = self.top = tk.Toplevel(parent)
        top.title("Add / Edit Task")
        self.result = None

        ttk.Label(top, text="Title:").grid(row=0, column=0, sticky=tk.W)
        self.title_var = tk.StringVar(value=(prefill and prefill.get('title')))
        ttk.Entry(top, textvariable=self.title_var, width=40).grid(row=0, column=1)

        ttk.Label(top, text="Estimate (mins):").grid(row=1, column=0, sticky=tk.W)
        self.est_var = tk.IntVar(value=(prefill and prefill.get('est_mins',30)))
        ttk.Entry(top, textvariable=self.est_var).grid(row=1, column=1)

        ttk.Label(top, text="Deadline (YYYY-MM-DD) optional:").grid(row=2, column=0, sticky=tk.W)
        self.dead_var = tk.StringVar(value=(prefill and prefill.get('deadline')))
        ttk.Entry(top, textvariable=self.dead_var).grid(row=2, column=1)

        ttk.Label(top, text="Priority (1 high - 5 low):").grid(row=3, column=0, sticky=tk.W)
        self.pr_var = tk.IntVar(value=(prefill and prefill.get('priority',3)))
        ttk.Entry(top, textvariable=self.pr_var).grid(row=3, column=1)

        ttk.Button(top, text="OK", command=self.on_ok).grid(row=4, column=0)
        ttk.Button(top, text="Cancel", command=top.destroy).grid(row=4, column=1)

    def on_ok(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("Missing", "Title is required")
            return
        est = self.est_var.get()
        dl = self.dead_var.get().strip()
        dl_parsed = None
        if dl:
            try:
                dl_parsed = date.fromisoformat(dl)
            except Exception:
                messagebox.showwarning("Bad date", "Deadline must be YYYY-MM-DD")
                return
        pr = self.pr_var.get()
        self.result = (title, est, dl_parsed, pr)
        self.top.destroy()

# ---------- Main ----------

if __name__ == "__main__":
    root = tk.Tk()
    app = StudyAssistantApp(root)
    root.mainloop()
