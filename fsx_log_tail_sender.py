import configparser
import csv
import os
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests


DEFAULT_LOG_FILE = r"C:\Program Files (x86)\Foresight Sport Experience\Logfiles\FSXLogFile.txt"
DEFAULT_LAST_SHOT_CSV = r"C:\Program Files (x86)\Foresight Sport Experience\System\LastShot.CSV"
DEFAULT_SERVER_URL = "http://127.0.0.1:5000/shot"
CONFIG_FILENAME = "fsx_log_tail_sender.ini"
LOG_POLL_INTERVAL_SEC = 0.2
CSV_READ_DELAY_SEC = 0.2
CSV_READ_RETRIES = 5
CSV_READ_RETRY_DELAY_SEC = 0.2
SHOT_DONE_PATTERN = re.compile(r"after\s+OutputLastShotCSV\s+ShotID=(\d+)", re.IGNORECASE)


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
CONFIG_PATH = APP_DIR / CONFIG_FILENAME


def decode_log_bytes(data):
    if not data:
        return ""

    if data.count(b"\x00") > len(data) // 8:
        return data.decode("utf-16-le", errors="replace").replace("\ufeff", "")

    return data.decode("utf-8-sig", errors="replace")


def read_last_shot_csv_payload(csv_file):
    with open(csv_file, "r", encoding="utf-8-sig", errors="replace", newline="") as file_obj:
        lines = [line.strip("\r\n") for line in file_obj if line.strip()]

    if len(lines) < 2:
        raise ValueError("LastShot CSV does not contain a header and data row.")

    header = lines[0]
    row = lines[-1]
    reader = csv.DictReader([header, row])
    row_data = next(reader, None) or {}
    shot_id = (row_data.get("Shot ID") or "").strip()

    return f"{header}\n{row}", shot_id


class FsxLogTailSenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("FSX Log Tail Sender")
        self.root.geometry("840x560")

        self.tail_thread = None
        self.stop_event = threading.Event()
        self.send_lock = threading.Lock()
        self.last_sent_signature = None

        self.log_file_var = tk.StringVar()
        self.csv_file_var = tk.StringVar()
        self.server_url_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Status: Idle")

        self._build_ui()
        self.load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="FSX Log Tail Sender", font=("Arial", 16, "bold")).pack(anchor="w", pady=(0, 12))

        log_row = ttk.Frame(frame)
        log_row.pack(fill="x", pady=4)
        ttk.Label(log_row, text="Log File", width=14).pack(side="left")
        ttk.Entry(log_row, textvariable=self.log_file_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(log_row, text="Browse...", command=self.choose_log_file).pack(side="left")

        csv_row = ttk.Frame(frame)
        csv_row.pack(fill="x", pady=4)
        ttk.Label(csv_row, text="LastShot CSV", width=14).pack(side="left")
        ttk.Entry(csv_row, textvariable=self.csv_file_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(csv_row, text="Browse...", command=self.choose_csv_file).pack(side="left")

        url_row = ttk.Frame(frame)
        url_row.pack(fill="x", pady=4)
        ttk.Label(url_row, text="Server URL", width=14).pack(side="left")
        ttk.Entry(url_row, textvariable=self.server_url_var).pack(side="left", fill="x", expand=True)

        action_row = ttk.Frame(frame)
        action_row.pack(fill="x", pady=(12, 10))
        self.start_button = ttk.Button(action_row, text="Start", command=self.start_monitoring)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(action_row, text="Stop", command=self.stop_monitoring, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(action_row, text="Send Now", command=self.send_now).pack(side="left", padx=(8, 0))
        ttk.Button(action_row, text="Test Connection", command=self.test_connection).pack(side="left", padx=(8, 0))
        ttk.Button(action_row, text="Save Settings", command=self.save_settings).pack(side="left", padx=(8, 0))

        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(frame, textvariable=self.status_var).pack(anchor="w")

        log_frame = ttk.Frame(frame)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = tk.Text(log_frame, height=16, state="disabled", wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def load_settings(self):
        config = configparser.ConfigParser()
        config.read(CONFIG_PATH, encoding="utf-8")

        self.log_file_var.set(config.get("sender", "log_file", fallback=DEFAULT_LOG_FILE))
        self.csv_file_var.set(config.get("sender", "last_shot_csv", fallback=DEFAULT_LAST_SHOT_CSV))
        self.server_url_var.set(config.get("sender", "server_url", fallback=DEFAULT_SERVER_URL))

        self.log(f"Config file: {CONFIG_PATH}")
        self.log(f"Default log file: {self.log_file_var.get()}")
        self.log(f"Default LastShot CSV: {self.csv_file_var.get()}")

    def save_settings(self):
        config = configparser.ConfigParser()
        config["sender"] = {
            "log_file": self.log_file_var.get().strip(),
            "last_shot_csv": self.csv_file_var.get().strip(),
            "server_url": self.server_url_var.get().strip() or DEFAULT_SERVER_URL,
        }

        with open(CONFIG_PATH, "w", encoding="utf-8") as file_obj:
            config.write(file_obj)

        self.set_status("Settings saved")
        self.log("Settings saved.")

    def choose_log_file(self):
        selected = filedialog.askopenfilename(
            title="Select FSX log file",
            initialdir=str(Path(self.log_file_var.get() or DEFAULT_LOG_FILE).parent),
            filetypes=[("Text files", "*.txt;*.log"), ("All files", "*.*")],
        )
        if selected:
            self.log_file_var.set(selected)

    def choose_csv_file(self):
        selected = filedialog.askopenfilename(
            title="Select LastShot CSV",
            initialdir=str(Path(self.csv_file_var.get() or DEFAULT_LAST_SHOT_CSV).parent),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.csv_file_var.set(selected)

    def validate_inputs(self):
        log_file = self.log_file_var.get().strip()
        csv_file = self.csv_file_var.get().strip()
        server_url = self.server_url_var.get().strip() or DEFAULT_SERVER_URL

        if not log_file:
            messagebox.showerror("Missing log file", "Please select FSXLogFile.txt.")
            return None

        if not csv_file:
            messagebox.showerror("Missing LastShot CSV", "Please select LastShot.CSV.")
            return None

        if not Path(log_file).exists():
            messagebox.showerror("Invalid log file", f"Log file does not exist:\n{log_file}")
            return None

        if not Path(csv_file).exists():
            messagebox.showerror("Invalid LastShot CSV", f"LastShot CSV does not exist:\n{csv_file}")
            return None

        return log_file, csv_file, server_url

    def start_monitoring(self):
        validated = self.validate_inputs()
        if not validated:
            return

        if self.tail_thread is not None and self.tail_thread.is_alive():
            self.log("Monitoring is already running.")
            return

        self.save_settings()
        self.stop_event.clear()
        self.last_sent_signature = None
        self.tail_thread = threading.Thread(target=self.tail_log_worker, args=validated, daemon=True)
        self.tail_thread.start()

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.set_status("Monitoring started")
        self.log(f"Monitoring log file: {validated[0]}")
        self.log(f"Reading LastShot CSV: {validated[1]}")
        self.log(f"Server URL: {validated[2]}")

    def stop_monitoring(self):
        if self.tail_thread is None:
            return

        self.stop_event.set()
        self.tail_thread.join(timeout=3)
        self.tail_thread = None

        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.set_status("Monitoring stopped")
        self.log("Monitoring stopped.")

    def tail_log_worker(self, log_file, csv_file, server_url):
        position = self.get_initial_position(log_file)
        pending_text = ""

        while not self.stop_event.is_set():
            try:
                current_size = os.path.getsize(log_file)
                if current_size < position:
                    self._async_log("Log file size decreased. Restarting tail position.")
                    position = 0
                    pending_text = ""

                if current_size > position:
                    with open(log_file, "rb") as file_obj:
                        file_obj.seek(position)
                        chunk = file_obj.read(current_size - position)
                        position = file_obj.tell()

                    if len(chunk) % 2 == 1:
                        position -= 1
                        chunk = chunk[:-1]

                    pending_text = self.process_log_text(pending_text + decode_log_bytes(chunk), csv_file, server_url)

            except FileNotFoundError:
                self._async_status("Waiting for log file")
                position = 0
                pending_text = ""
            except PermissionError:
                self._async_status("Log file is locked; retrying")
            except Exception as exc:
                self._async_log(f"Log tail error: {exc}")
                self._async_status("Log tail error")

            self.stop_event.wait(LOG_POLL_INTERVAL_SEC)

    def get_initial_position(self, log_file):
        try:
            return os.path.getsize(log_file)
        except OSError:
            return 0

    def process_log_text(self, text, csv_file, server_url):
        lines = text.splitlines(keepends=True)
        if not lines:
            return ""

        if not lines[-1].endswith(("\n", "\r")):
            pending = lines.pop()
        else:
            pending = ""

        for line in lines:
            match = SHOT_DONE_PATTERN.search(line)
            if match:
                shot_id = match.group(1)
                self._async_log(f"Detected OutputLastShotCSV complete: ShotID={shot_id}")
                self.send_csv_after_log_signal(csv_file, server_url, shot_id, line.strip())

        return pending

    def send_csv_after_log_signal(self, csv_file, server_url, log_shot_id, log_line):
        thread = threading.Thread(
            target=self._send_csv_after_log_signal_worker,
            args=(csv_file, server_url, log_shot_id, log_line),
            daemon=True,
        )
        thread.start()

    def _send_csv_after_log_signal_worker(self, csv_file, server_url, log_shot_id, log_line):
        if not self.send_lock.acquire(blocking=False):
            self._async_log("Send is already in progress; skipping duplicate trigger.")
            return

        try:
            self.stop_event.wait(CSV_READ_DELAY_SEC)
            payload = None
            csv_shot_id = ""
            last_error = None

            for attempt in range(1, CSV_READ_RETRIES + 1):
                try:
                    payload, csv_shot_id = read_last_shot_csv_payload(csv_file)
                    if not log_shot_id or csv_shot_id == log_shot_id:
                        break

                    self._async_log(
                        f"Shot ID mismatch on attempt {attempt}: log={log_shot_id}, csv={csv_shot_id}"
                    )
                except Exception as exc:
                    last_error = exc
                    self._async_log(f"CSV read attempt {attempt} failed: {exc}")

                self.stop_event.wait(CSV_READ_RETRY_DELAY_SEC)

            if payload is None:
                raise RuntimeError(f"Could not read LastShot CSV: {last_error}")

            if log_shot_id and csv_shot_id and csv_shot_id != log_shot_id:
                self._async_log(f"Sending latest CSV despite Shot ID mismatch: log={log_shot_id}, csv={csv_shot_id}")

            self.post_payload(server_url, payload, log_shot_id, csv_shot_id, log_line)
        except Exception as exc:
            self._async_log(f"Send failed: {exc}")
            self._async_status("Send failed")
        finally:
            self.send_lock.release()

    def post_payload(self, server_url, payload, log_shot_id="", csv_shot_id="", log_line=""):
        signature = (log_shot_id, payload)
        if signature == self.last_sent_signature:
            self._async_log(f"Skipped duplicate payload: ShotID={log_shot_id or csv_shot_id}")
            return

        response = requests.post(
            server_url,
            data=payload.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            timeout=3,
        )
        response.raise_for_status()

        self.last_sent_signature = signature
        self._async_log(f"Sent LastShot CSV payload: log ShotID={log_shot_id}, csv Shot ID={csv_shot_id}")
        self._async_log(f"Trigger: {log_line}")
        self._async_status("Send successful")

    def send_now(self):
        validated = self.validate_inputs()
        if not validated:
            return

        _, csv_file, server_url = validated
        self.save_settings()

        def worker():
            try:
                payload, csv_shot_id = read_last_shot_csv_payload(csv_file)
                self.post_payload(server_url, payload, csv_shot_id, csv_shot_id, "Manual send")
            except Exception as exc:
                self._async_log(f"Send now failed: {exc}")
                self._async_status("Send failed")

        threading.Thread(target=worker, daemon=True).start()

    def test_connection(self):
        validated = self.validate_inputs()
        if not validated:
            return

        _, _, server_url = validated
        self.save_settings()
        self.set_status("Testing connection")
        threading.Thread(target=self._test_connection_worker, args=(server_url,), daemon=True).start()

    def _test_connection_worker(self, server_url):
        health_url = server_url.rstrip("/")
        if health_url.endswith("/shot"):
            health_url = f"{health_url[:-5]}/"

        try:
            response = requests.get(health_url, timeout=3)
            response.raise_for_status()
            self._async_log(f"Connection OK: {health_url}")
            self._async_status("Connection test successful")
        except Exception as exc:
            self._async_log(f"Connection failed: {health_url} ({exc})")
            self._async_status("Connection test failed")

    def log(self, message):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _async_log(self, message):
        self.root.after(0, lambda: self.log(message))

    def set_status(self, message):
        self.status_var.set(f"Status: {message}")

    def _async_status(self, message):
        self.root.after(0, lambda: self.set_status(message))

    def on_close(self):
        try:
            self.save_settings()
        finally:
            self.stop_monitoring()
            self.root.destroy()


def main():
    root = tk.Tk()
    FsxLogTailSenderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
