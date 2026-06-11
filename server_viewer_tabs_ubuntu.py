#!/usr/bin/env python3
import configparser
import csv
import io
import json
import math
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError as exc:
    raise SystemExit(
        "tkinter is required on Ubuntu. Install it with:\n"
        "sudo apt update && sudo apt install -y python3-tk"
    ) from exc

import requests
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5000
DEFAULT_DEBUG = False
DEFAULT_VIEWER_URL = "http://127.0.0.1:5000/latest"
DEFAULT_NX_LOG_DIR = "/home/golfzon/.nxsensor/log"
SERVER_CONFIG_FILENAME = "server.ini"
VIEWER_CONFIG_FILENAME = "viewer.ini"
LATEST_JSON_FILENAME = "latest_shot.json"
HISTORY_CSV_FILENAME = "shot_history.csv"
MERGE_DIR_PREFIX = "ShotMerge"
MERGE_CSV_FILENAME = "DateMerge_NXPlus_GCQuad.csv"
MERGE_GRC_DIRNAME = "grc"
MERGE_GRC_DECRYPT_DIRNAME = "grc_decrypted"
GRC_DECRYPT_CLI_FILENAME = "grc-decrypt-cli"
GRC_DECRYPT_TIMEOUT_SECONDS = 60
LOG_POLL_MS = 250
SSE_RECONNECT_SECONDS = 2
NX_LOG_POLL_SECONDS = 0.25
NX_LOG_FILE_REFRESH_SECONDS = 2
NX_PACKED_WAIT_SECONDS = 10
NX_CSV_READ_RETRIES = 10
NX_CSV_READ_RETRY_DELAY_SECONDS = 0.2
COUNTERPART_RECEIVE_TIMEOUT_SECONDS = 10.0
SOURCE_SYSTEM_GCQUAD = "GCQuad"
SOURCE_SYSTEM_NX = "NX+"
AVAILABLE_COMPARE_METRICS = [
    ("club", "Club"),
    ("club_type", "Club Type"),
    ("ball_speed", "Ball Speed"),
    ("launch_angle", "Launch Angle"),
    ("ball_direction", "Ball Direction"),
    ("club_velocity", "Club Speed"),
    ("attack_angle", "Attack Angle"),
    ("club_path", "Club Path"),
    ("face_angle", "Face Angle"),
    ("dynamic_loft", "Dynamic Loft"),
    ("sidespin", "Side Spin"),
    ("backspin", "Back Spin"),
    ("totalspin", "Total Spin"),
    ("spin_axis", "Spin Axis"),
    ("carry", "Carry"),
    ("total_distance", "Total Distance"),
    ("offline", "Offline"),
    ("peak_height", "Peak Height"),
    ("descent_angle", "Descent Angle"),
    ("mat_type", "Mat Type"),
]
AVAILABLE_COMPARE_METRIC_KEYS = {key for key, _ in AVAILABLE_COMPARE_METRICS}
DEFAULT_COMPARE_METRIC_KEYS = ["ball_speed", "launch_angle", "sidespin", "backspin", "totalspin"]
MERGE_CSV_HEADER = [
    "SensorName",
    "Date-Time(GCQ)",
    "Club(GCQ)",
    "Ball(GCQ)",
    "Ball Speed(GCQ)",
    "Launch Angle(GCQ)",
    "Side Angle(GCQ)",
    "Backspin(GCQ)",
    "Sidespin(GCQ)",
    "Tilt Angle(GCQ)",
    "Total Spin(GCQ)",
    "Carry(GCQ)",
    "Total(GCQ)",
    "Offline(GCQ)",
    "Descent Angle(GCQ)",
    "Peak Height(GCQ)",
    "Club Speed(GCQ)",
    "Efficiency(GCQ)",
    "Angle of Attack(GCQ)",
    "Club Path(GCQ)",
    "Face to Path(GCQ)",
    "Lie(GCQ)",
    "Loft(GCQ)",
    "Closure Rate(GCQ)",
    "Face Impact Lateral(GCQ)",
    "Face Impact Vertical(GCQ)",
    "",
    "SensorName",
    "Date-Time(NX)",
    "ShotDB #(NX)",
    "Date(NX)",
    "ShotDB Name(NX)",
    "Ball Speed(NX)",
    "Ball Incidence(NX)",
    "Ball Direction(NX)",
    "Club Speed(NX)",
    "Attack Angle(NX)",
    "Club Path(NX)",
    "Face Angle(NX)",
    "Dynamic Loft(NX)",
    "Back Spin(NX)",
    "Side Spin(NX)",
    "Total Spin(NX)",
    "Spin Axis(NX)",
    "Proc Time(1st)(NX)",
    "Proc Time(2nd)(NX)",
    "Back Spin(Spin)(NX)",
    "Side Spin(Spin)(NX)",
    "Total Spin(Spin)(NX)",
    "Spin Axis(Spin)(NX)",
    "Confidence(Spin)(NX)",
    "Proc Time(Spin)(NX)",
    "Back Spin(AI)(NX)",
    "Side Spin(AI)(NX)",
    "Total Spin(AI)(NX)",
    "Spin Axis(AI)(NX)",
    "Spin Axis(AI)(NX)_Compensation",
    "Proc Time(AI)(NX)",
    "Light Level(Spin)(NX)",
    "Club Type(NX)",
    "Mat Type(NX)",
    "",
    "",
]


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
SERVER_CONFIG_PATH = APP_DIR / SERVER_CONFIG_FILENAME
VIEWER_CONFIG_PATH = APP_DIR / VIEWER_CONFIG_FILENAME
LATEST_JSON_PATH = APP_DIR / LATEST_JSON_FILENAME
HISTORY_CSV_PATH = APP_DIR / HISTORY_CSV_FILENAME

flask_app = Flask(__name__)

EMPTY_SHOT = {
    "timestamp": "",
    "shot_id": "",
    "source_system": "",
    "ball_speed": "",
    "launch_angle": "",
    "spin_rate": "",
    "club": "",
    "club_type": "",
    "carry": "",
    "total_distance": "",
    "offline": "",
    "peak_height": "",
    "descent_angle": "",
    "ball_velocity": "",
    "ball_incidence": "",
    "ball_direction": "",
    "club_velocity": "",
    "club_incidence": "",
    "club_direction": "",
    "attack_angle": "",
    "club_path": "",
    "face_angle": "",
    "dynamic_loft": "",
    "backspin": "",
    "sidespin": "",
    "totalspin": "",
    "spin_axis": "",
    "mat_type": "",
    "shotdb_id": "",
    "shotdb_filename": "",
    "shotdb_path": "",
    "nx_log_file": "",
    "nx_csv_file": "",
    "noti_match": "",
    "received_at": "",
    "source_format": "",
}

latest_shot = EMPTY_SHOT.copy()
data_lock = threading.Lock()
server_ui = None
sse_clients = []
sse_clients_lock = threading.Lock()


def load_server_config():
    config = configparser.ConfigParser()
    config.read(SERVER_CONFIG_PATH, encoding="utf-8")

    host = config.get("server", "host", fallback=DEFAULT_HOST)
    port = config.getint("server", "port", fallback=DEFAULT_PORT)
    debug = config.getboolean("server", "debug", fallback=DEFAULT_DEBUG)
    nx_log_dir = config.get("server", "nx_log_dir", fallback=DEFAULT_NX_LOG_DIR)
    nx_log_file = config.get("server", "nx_log_file", fallback="")
    counterpart_receive_timeout = config.getfloat(
        "server",
        "counterpart_receive_timeout_seconds",
        fallback=COUNTERPART_RECEIVE_TIMEOUT_SECONDS,
    )

    if not SERVER_CONFIG_PATH.exists():
        save_server_config(host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout)

    return host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout


def save_server_config(
    host,
    port,
    debug,
    nx_log_dir,
    nx_log_file="",
    counterpart_receive_timeout=COUNTERPART_RECEIVE_TIMEOUT_SECONDS,
):
    config = configparser.ConfigParser()
    config["server"] = {
        "host": host,
        "port": str(port),
        "debug": str(debug).lower(),
        "nx_log_dir": nx_log_dir,
        "nx_log_file": nx_log_file,
        "counterpart_receive_timeout_seconds": str(counterpart_receive_timeout),
    }

    with open(SERVER_CONFIG_PATH, "w", encoding="utf-8") as file_obj:
        config.write(file_obj)


def load_viewer_config():
    config = configparser.ConfigParser()
    config.read(VIEWER_CONFIG_PATH, encoding="utf-8")
    server_url = config.get("viewer", "server_url", fallback=DEFAULT_VIEWER_URL)
    selected_text = config.get("viewer", "selected_metrics", fallback=",".join(DEFAULT_COMPARE_METRIC_KEYS))
    selected_metric_keys = [
        key.strip()
        for key in selected_text.split(",")
        if key.strip() in AVAILABLE_COMPARE_METRIC_KEYS
    ]

    if not selected_metric_keys:
        selected_metric_keys = DEFAULT_COMPARE_METRIC_KEYS.copy()

    if not VIEWER_CONFIG_PATH.exists():
        save_viewer_config(server_url, selected_metric_keys)

    return server_url, selected_metric_keys


def save_viewer_config(server_url, selected_metric_keys=None):
    selected_metric_keys = selected_metric_keys or DEFAULT_COMPARE_METRIC_KEYS
    config = configparser.ConfigParser()
    config["viewer"] = {
        "server_url": server_url,
        "selected_metrics": ",".join(selected_metric_keys),
    }

    with open(VIEWER_CONFIG_PATH, "w", encoding="utf-8") as file_obj:
        config.write(file_obj)


def load_latest_shot():
    if not LATEST_JSON_PATH.exists():
        return EMPTY_SHOT.copy()

    try:
        with open(LATEST_JSON_PATH, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except Exception:
        return EMPTY_SHOT.copy()

    shot = EMPTY_SHOT.copy()
    for key in shot:
        shot[key] = data.get(key, "")
    shot["raw_text"] = data.get("raw_text", "")
    shot["parsed"] = data.get("parsed", {})
    return shot


def save_latest_shot_json(data):
    with open(LATEST_JSON_PATH, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


def append_shot_history_csv(data):
    fieldnames = [
        "timestamp",
        "shot_id",
        "source_system",
        "club",
        "club_type",
        "ball_speed",
        "launch_angle",
        "spin_rate",
        "carry",
        "total_distance",
        "offline",
        "peak_height",
        "descent_angle",
        "ball_velocity",
        "ball_incidence",
        "ball_direction",
        "club_velocity",
        "club_incidence",
        "club_direction",
        "attack_angle",
        "club_path",
        "face_angle",
        "dynamic_loft",
        "backspin",
        "sidespin",
        "totalspin",
        "spin_axis",
        "club_type",
        "mat_type",
        "shotdb_id",
        "shotdb_filename",
        "nx_csv_file",
        "noti_match",
        "received_at",
        "source_format",
    ]
    file_exists = HISTORY_CSV_PATH.exists()

    with open(HISTORY_CSV_PATH, "a", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({field: data.get(field, "") for field in fieldnames})


def get_merge_dir():
    date_token = datetime.now().strftime("%Y%m%d")
    return APP_DIR / f"{MERGE_DIR_PREFIX}_{date_token}"


def get_merge_value(data, parsed_keys=(), fallback_keys=()):
    parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else {}
    for key in parsed_keys:
        value = parsed.get(key)
        if value not in (None, ""):
            return value.strip() if isinstance(value, str) else value

    normalized = {
        key.lower().replace(" ", ""): value
        for key, value in parsed.items()
    }
    for key in parsed_keys:
        value = normalized.get(key.lower().replace(" ", ""))
        if value not in (None, ""):
            return value.strip() if isinstance(value, str) else value

    for key in fallback_keys:
        value = data.get(key)
        if value not in (None, ""):
            return value.strip() if isinstance(value, str) else value

    return ""


def parse_merge_float(value):
    if value in (None, ""):
        return None

    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def format_merge_float(value):
    return f"{value:.6f}".rstrip("0").rstrip(".")


def calculate_tilt_angle(backspin, sidespin):
    backspin_value = parse_merge_float(backspin)
    sidespin_value = parse_merge_float(sidespin)
    if backspin_value in (None, 0) or sidespin_value is None:
        return ""

    return format_merge_float(math.degrees(math.atan(sidespin_value / backspin_value)))


def format_compact_datetime(value):
    text = str(value or "").strip()
    match = re.match(r"^(\d{4})[./-](\d{2})[./-](\d{2})[_ T](\d{2})[.:](\d{2})[.:](\d{2})", text)
    if match:
        year, month, day, hour, minute, second = match.groups()
        return f"{year}{month}{day}_{hour}{minute}{second}"
    return text


def build_merge_gcq_values(gcq_data):
    backspin = get_merge_value(gcq_data, ("Backspin", "Back Spin", "Back Spin (rpm)", "Backspin(GCQ)"), ("backspin",))
    sidespin = get_merge_value(gcq_data, ("Sidespin", "Side Spin", "Side Spin (rpm)", "Sidespin(GCQ)"), ("sidespin",))
    tilt_angle = calculate_tilt_angle(backspin, sidespin)
    attack_angle = get_merge_value(
        gcq_data,
        ("Vert Path", "Vert Path (deg)", "Vert Path(deg)", "Vert Path(GCQ)", "Angle of Attack", "Angle of Attack (deg)", "Attack Angle", "Attack Angle (deg)", "Angle of Attack(GCQ)"),
        ("attack_angle", "club_incidence"),
    )
    club_path = get_merge_value(
        gcq_data,
        ("Horiz Path", "Horiz Path (deg)", "Horiz Path(deg)", "Horiz Path(GCQ)", "Club Path", "Club Path (deg)", "Club Path(GCQ)"),
        ("club_path", "club_direction"),
    )
    face_to_target = get_merge_value(
        gcq_data,
        ("Face to Target", "Face to Target (deg)", "Face to Target(deg)", "Face to Target(GCQ)", "Face Angle", "Face Angle (deg)", "Face Angle(GCQ)", "Face to Path", "Face to Path (deg)", "Face to Path(GCQ)"),
        ("face_angle",),
    )
    gcq_datetime = get_merge_value(gcq_data, ("Date-Time", "Date-Time(GCQ)"), ("received_at",))
    if not gcq_datetime:
        gcq_datetime = get_merge_value(gcq_data, ("Shot ID",), ("timestamp", "shot_id"))

    return [
        "GCQ",
        gcq_datetime,
        get_merge_value(gcq_data, ("Club", "Club(GCQ)"), ("club", "club_type")),
        get_merge_value(gcq_data, ("Ball", "Ball(GCQ)", "Ball Type"), ()),
        get_merge_value(gcq_data, ("Ball Speed", "Ball Speed (m/s)", "Ball Speed(GCQ)"), ("ball_speed",)),
        get_merge_value(gcq_data, ("Launch Angle", "Launch Angle (deg)", "Launch Angle(GCQ)"), ("launch_angle",)),
        get_merge_value(gcq_data, ("Side Angle", "Side Angle (deg)", "Azimuth (deg)", "Side Angle(GCQ)"), ("ball_direction",)),
        backspin,
        sidespin,
        tilt_angle,
        get_merge_value(gcq_data, ("Total Spin", "Total Spin (rpm)", "Total Spin(GCQ)"), ("totalspin", "spin_rate")),
        get_merge_value(gcq_data, ("Carry", "Carry (m)", "Carry(GCQ)"), ("carry",)),
        get_merge_value(gcq_data, ("Total", "Total Distance", "Total Distance (m)", "Total(GCQ)"), ("total_distance",)),
        get_merge_value(gcq_data, ("Offline", "Offline (m)", "Offline(GCQ)"), ("offline",)),
        get_merge_value(gcq_data, ("Descent Angle", "Descent Angle (deg)", "Descent Angle(GCQ)"), ("descent_angle",)),
        get_merge_value(gcq_data, ("Peak Height", "Peak Height (m)", "Peak Height(GCQ)"), ("peak_height",)),
        get_merge_value(gcq_data, ("Club Speed", "Club head Speed (m/s)", "Club Speed (m/s)", "Club Speed(GCQ)"), ("club_velocity",)),
        get_merge_value(gcq_data, ("Efficiency", "Efficiency(GCQ)"), ()),
        attack_angle,
        club_path,
        face_to_target,
        get_merge_value(gcq_data, ("Lie", "Lie(GCQ)"), ()),
        get_merge_value(gcq_data, ("Loft", "Loft(GCQ)"), ()),
        get_merge_value(gcq_data, ("Closure Rate", "Closure Rate(GCQ)"), ()),
        get_merge_value(gcq_data, ("Face Impact Lateral", "Face Impact Lateral(GCQ)"), ()),
        get_merge_value(gcq_data, ("Face Impact Vertical", "Face Impact Vertical(GCQ)"), ()),
    ]


def build_merge_nx_values(nx_data):
    nx_date = get_merge_value(nx_data, ("Date", "Date(NX)"), ("timestamp",))
    shotdb_name = get_merge_value(nx_data, ("ShotDB Name", "ShotDB Name(NX)"), ("shotdb_name",))
    if not shotdb_name:
        shotdb_name = Path(str(nx_data.get("shotdb_filename", ""))).stem

    return [
        "NX",
        get_merge_value(nx_data, ("Date-Time", "Date-Time(NX)"), ()) or format_compact_datetime(nx_date),
        get_merge_value(nx_data, ("ShotDB #", "ShotDB #(NX)"), ("shotdb_id", "shot_id")),
        nx_date,
        shotdb_name,
        get_merge_value(nx_data, ("Ball Speed", "Ball Speed(NX)"), ("ball_velocity", "ball_speed")),
        get_merge_value(nx_data, ("Ball Incidence", "Ball Incidence(NX)"), ("ball_incidence", "launch_angle")),
        get_merge_value(nx_data, ("Ball Direction", "Ball Direction(NX)"), ("ball_direction",)),
        get_merge_value(nx_data, ("Club Speed", "Club Speed(NX)"), ("club_velocity",)),
        get_merge_value(nx_data, ("Attack Angle", "Attack Angle(NX)"), ("attack_angle", "club_incidence")),
        get_merge_value(nx_data, ("Club Path", "Club Path(NX)"), ("club_path", "club_direction")),
        get_merge_value(nx_data, ("Face Angle", "Face Angle(NX)"), ("face_angle",)),
        get_merge_value(nx_data, ("Dynamic Loft", "Dynamic Loft(NX)"), ("dynamic_loft",)),
        get_merge_value(nx_data, ("Back Spin", "Back Spin(NX)"), ("backspin",)),
        get_merge_value(nx_data, ("Side Spin", "Side Spin(NX)"), ("sidespin",)),
        get_merge_value(nx_data, ("Total Spin", "Total Spin(NX)"), ("totalspin", "spin_rate")),
        get_merge_value(nx_data, ("Spin Axis", "Spin Axis(NX)"), ("spin_axis",)),
        get_merge_value(nx_data, ("Proc Time(1st)", "Proc Time(1st)(NX)"), ("proc_time_1st",)),
        get_merge_value(nx_data, ("Proc Time(2nd)", "Proc Time(2nd)(NX)"), ("proc_time_2nd",)),
        get_merge_value(nx_data, ("Back Spin(Spin)", "Back Spin(Spin)(NX)"), ("backspin_spin",)),
        get_merge_value(nx_data, ("Side Spin(Spin)", "Side Spin(Spin)(NX)"), ("sidespin_spin",)),
        get_merge_value(nx_data, ("Total Spin(Spin)", "Total Spin(Spin)(NX)"), ("totalspin_spin",)),
        get_merge_value(nx_data, ("Spin Axis(Spin)", "Spin Axis(Spin)(NX)"), ("spin_axis_spin",)),
        get_merge_value(nx_data, ("Confidence(Spin)", "Confidence(Spin)(NX)"), ("confidence_spin",)),
        get_merge_value(nx_data, ("Proc Time(Spin)", "Proc Time(Spin)(NX)"), ("proc_time_spin",)),
        get_merge_value(nx_data, ("Back Spin(AI)", "Back Spin(AI)(NX)"), ("backspin_ai",)),
        get_merge_value(nx_data, ("Side Spin(AI)", "Side Spin(AI)(NX)"), ("sidespin_ai",)),
        get_merge_value(nx_data, ("Total Spin(AI)", "Total Spin(AI)(NX)"), ("totalspin_ai",)),
        get_merge_value(nx_data, ("Spin Axis(AI)", "Spin Axis(AI)(NX)"), ("spin_axis_ai",)),
        get_merge_value(nx_data, ("Spin Axis(AI)_Compensation", "Spin Axis(AI)(NX)_Compensation"), ()),
        get_merge_value(nx_data, ("Proc Time(AI)", "Proc Time(AI)(NX)"), ("proc_time_ai",)),
        get_merge_value(nx_data, ("Light Level(Spin)", "Light Level(Spin)(NX)"), ("light_level_spin",)),
        get_merge_value(nx_data, ("Club Type", "Club Type(NX)"), ("club_type",)),
        get_merge_value(nx_data, ("Mat Type", "Mat Type(NX)"), ("mat_type",)),
    ]


def build_merge_csv_row(gcq_data, nx_data):
    return build_merge_gcq_values(gcq_data) + [""] + build_merge_nx_values(nx_data) + ["", ""]


def append_shot_merge_csv(gcq_data, nx_data):
    merge_dir = get_merge_dir()
    merge_dir.mkdir(parents=True, exist_ok=True)
    merge_csv_path = merge_dir / MERGE_CSV_FILENAME
    file_exists = merge_csv_path.exists()

    with open(merge_csv_path, "a", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.writer(file_obj)
        if not file_exists:
            writer.writerow(MERGE_CSV_HEADER)
        writer.writerow(build_merge_csv_row(gcq_data, nx_data))

    return merge_csv_path


def get_shot_merge_key_from_values(gcq_key, nx_shotdb_id="", nx_shotdb_name=""):
    return str(gcq_key or ""), str(nx_shotdb_name or nx_shotdb_id or "")


def load_existing_shot_merge_keys():
    merge_csv_path = get_merge_dir() / MERGE_CSV_FILENAME
    if not merge_csv_path.exists():
        return set()

    try:
        with open(merge_csv_path, "r", encoding="utf-8-sig", errors="replace", newline="") as file_obj:
            rows = list(csv.reader(file_obj))
    except OSError:
        return set()

    if not rows:
        return set()

    header = rows[0]
    try:
        gcq_index = header.index("Date-Time(GCQ)")
        nx_id_index = header.index("ShotDB #(NX)")
        nx_name_index = header.index("ShotDB Name(NX)")
    except ValueError:
        return set()

    keys = set()
    for row in rows[1:]:
        gcq_key = row[gcq_index].strip() if gcq_index < len(row) else ""
        nx_shotdb_id = row[nx_id_index].strip() if nx_id_index < len(row) else ""
        nx_shotdb_name = row[nx_name_index].strip() if nx_name_index < len(row) else ""
        key = get_shot_merge_key_from_values(gcq_key, nx_shotdb_id, nx_shotdb_name)
        if any(key):
            keys.add(key)
        legacy_key = get_shot_merge_key_from_values(gcq_key, nx_shotdb_id, "")
        if any(legacy_key):
            keys.add(legacy_key)

    return keys


def load_existing_shot_merge_source_keys():
    merge_csv_path = get_merge_dir() / MERGE_CSV_FILENAME
    if not merge_csv_path.exists():
        return set(), set()

    try:
        with open(merge_csv_path, "r", encoding="utf-8-sig", errors="replace", newline="") as file_obj:
            rows = list(csv.reader(file_obj))
    except OSError:
        return set(), set()

    if not rows:
        return set(), set()

    header = rows[0]
    try:
        gcq_index = header.index("Date-Time(GCQ)")
        nx_id_index = header.index("ShotDB #(NX)")
        nx_name_index = header.index("ShotDB Name(NX)")
    except ValueError:
        return set(), set()

    gcq_keys = set()
    nx_keys = set()
    for row in rows[1:]:
        gcq_key = row[gcq_index].strip() if gcq_index < len(row) else ""
        nx_shotdb_id = row[nx_id_index].strip() if nx_id_index < len(row) else ""
        nx_shotdb_name = row[nx_name_index].strip() if nx_name_index < len(row) else ""

        if gcq_key:
            gcq_keys.add(gcq_key)
        if nx_shotdb_name:
            nx_keys.add(nx_shotdb_name)
        if nx_shotdb_id:
            nx_keys.add(nx_shotdb_id)

    return gcq_keys, nx_keys


def copy_nx_grc_to_merge_dir(nx_data):
    shotdb_path = str(nx_data.get("shotdb_path", "")).strip()
    if not shotdb_path:
        shotdb_filename = str(nx_data.get("shotdb_filename", "")).strip()
        nx_csv_file = str(nx_data.get("nx_csv_file", "")).strip()
        if shotdb_filename and nx_csv_file:
            shotdb_path = str(Path(nx_csv_file).expanduser().parent / shotdb_filename)

    if not shotdb_path:
        return None

    source_path = Path(shotdb_path).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"NX+ grc file was not found: {source_path}")

    grc_dir = get_merge_dir() / MERGE_GRC_DIRNAME
    grc_dir.mkdir(parents=True, exist_ok=True)
    destination_path = grc_dir / source_path.name
    shutil.copy2(source_path, destination_path)
    return destination_path


def find_grc_decrypt_cli():
    candidates = [
        APP_DIR / GRC_DECRYPT_CLI_FILENAME,
        Path.cwd() / GRC_DECRYPT_CLI_FILENAME,
    ]

    path_from_env = shutil.which(GRC_DECRYPT_CLI_FILENAME)
    if path_from_env:
        candidates.append(Path(path_from_env))

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return None


def run_grc_decrypt_cli(grc_path):
    cli_path = find_grc_decrypt_cli()
    if cli_path is None:
        raise FileNotFoundError(
            f"{GRC_DECRYPT_CLI_FILENAME} was not found. "
            f"Place it next to {Path(sys.argv[0]).name} or add it to PATH."
        )

    grc_path = Path(grc_path)
    output_dir = get_merge_dir() / MERGE_GRC_DECRYPT_DIRNAME / grc_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [str(cli_path), str(grc_path), str(output_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=GRC_DECRYPT_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to run {cli_path}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{GRC_DECRYPT_CLI_FILENAME} timed out after {GRC_DECRYPT_TIMEOUT_SECONDS}s") from exc

    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{GRC_DECRYPT_CLI_FILENAME} exited with code {result.returncode}: {output}")

    return output_dir, (result.stdout or "").strip()


def parse_optional_float(value, field_name):
    if value in (None, ""):
        return ""

    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc


def normalize_shot_data(data):
    timestamp = str(data.get("timestamp", "")).strip()
    if not timestamp:
        raise ValueError("Missing timestamp")

    return {
        "timestamp": timestamp,
        "shot_id": str(data.get("shot_id", "")).strip(),
        "source_system": str(data.get("source_system", SOURCE_SYSTEM_GCQUAD)).strip() or SOURCE_SYSTEM_GCQUAD,
        "ball_speed": parse_optional_float(data.get("ball_speed", ""), "ball_speed"),
        "launch_angle": parse_optional_float(data.get("launch_angle", ""), "launch_angle"),
        "spin_rate": parse_optional_float(data.get("spin_rate", ""), "spin_rate"),
        "club": str(data.get("club", "")).strip(),
        "carry": parse_optional_float(data.get("carry", ""), "carry"),
        "total_distance": parse_optional_float(data.get("total_distance", ""), "total_distance"),
        "source_format": "json",
    }


def decode_request_text():
    raw_bytes = request.get_data(cache=False)
    if not raw_bytes:
        return ""

    charset = request.mimetype_params.get("charset") or "utf-8"
    return raw_bytes.decode(charset, errors="replace")


def parse_csv_text_payload(raw_text):
    lines = [line for line in raw_text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("CSV text payload must include a header and data row")

    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    rows = list(reader)
    if not rows:
        raise ValueError("CSV text payload has no data row")

    return {
        key.strip(): (value or "").strip()
        for key, value in rows[-1].items()
        if key is not None and key.strip()
    }


def get_first_csv_value(parsed, *field_names):
    for field_name in field_names:
        value = parsed.get(field_name)
        if value not in (None, ""):
            return value

    normalized = {
        key.lower().replace(" ", ""): value
        for key, value in parsed.items()
    }
    for field_name in field_names:
        value = normalized.get(field_name.lower().replace(" ", ""))
        if value not in (None, ""):
            return value

    return ""


def normalize_csv_text_shot(raw_text):
    parsed = parse_csv_text_payload(raw_text)
    shot_id = parsed.get("Shot ID", "")
    side_spin = parsed.get("Side Spin (rpm)", "")
    back_spin = parsed.get("Back Spin (rpm)", "")
    total_spin = parsed.get("Total Spin (rpm)", "")
    tilt_angle = calculate_tilt_angle(back_spin, side_spin)
    horiz_path = get_first_csv_value(parsed, "Horiz Path (deg)", "Horiz Path(deg)")
    vert_path = get_first_csv_value(parsed, "Vert Path (deg)", "Vert Path(deg)")
    face_to_target = get_first_csv_value(parsed, "Face to Target (deg)", "Face to Target(deg)")

    return {
        "timestamp": shot_id,
        "shot_id": shot_id,
        "source_system": SOURCE_SYSTEM_GCQUAD,
        "club": parsed.get("Club", ""),
        "club_type": parsed.get("Club", ""),
        "ball_speed": parse_optional_float(parsed.get("Ball Speed (m/s)", ""), "Ball Speed (m/s)"),
        "launch_angle": parse_optional_float(parsed.get("Launch Angle (deg)", ""), "Launch Angle (deg)"),
        "ball_direction": parse_optional_float(parsed.get("Azimuth (deg)", ""), "Azimuth (deg)"),
        "club_velocity": parse_optional_float(parsed.get("Club head Speed (m/s)", ""), "Club head Speed (m/s)"),
        "attack_angle": parse_optional_float(vert_path, "Vert Path (deg)"),
        "club_path": parse_optional_float(horiz_path, "Horiz Path (deg)"),
        "face_angle": parse_optional_float(face_to_target, "Face to Target (deg)"),
        "spin_rate": parse_optional_float(total_spin or back_spin, "Spin (rpm)"),
        "sidespin": parse_optional_float(side_spin, "Side Spin (rpm)"),
        "backspin": parse_optional_float(back_spin, "Back Spin (rpm)"),
        "totalspin": parse_optional_float(total_spin, "Total Spin (rpm)"),
        "spin_axis": parse_optional_float(tilt_angle, "Tilt Angle"),
        "descent_angle": parse_optional_float(parsed.get("Descent Angle (deg)", ""), "Descent Angle (deg)"),
        "carry": parse_optional_float(parsed.get("Carry (m)", ""), "Carry (m)"),
        "total_distance": parse_optional_float(parsed.get("Total Distance (m)", ""), "Total Distance (m)"),
        "offline": parse_optional_float(parsed.get("Offline (m)", ""), "Offline (m)"),
        "peak_height": parse_optional_float(parsed.get("Peak Height (m)", ""), "Peak Height (m)"),
        "received_at": "",
        "source_format": "csv_text",
        "raw_text": raw_text,
        "parsed": parsed,
    }


def normalize_request_shot():
    data = request.get_json(silent=True)
    if data:
        return normalize_shot_data(data)

    raw_text = decode_request_text()
    if raw_text.strip():
        return normalize_csv_text_shot(raw_text)

    raise ValueError("Invalid JSON or empty text payload")


LOG_TIMESTAMP_RE = re.compile(r"^\[([^\]]+)\]")
NX_FLOAT_RE = r"([-+]?\d+(?:\.\d+)?)"
NX_NOTI_BALL_RE = re.compile(
    r"\[NOTI_2ND\]\s+"
    r"ball_velocity\s+" + NX_FLOAT_RE + r"\s+"
    r"ball_incidence\s+" + NX_FLOAT_RE + r"\s+"
    r"ball_direction\s+" + NX_FLOAT_RE + r"\s+"
    r"club_velocity\s+" + NX_FLOAT_RE + r"\s+"
    r"club_incidence\s+" + NX_FLOAT_RE + r"\s+"
    r"club_direction\s+" + NX_FLOAT_RE
)
NX_NOTI_TV_RE = re.compile(
    r"\[NOTI_2ND-TV\]\s+spinaxis\s+" + NX_FLOAT_RE + r",\s+"
    r"backspin\s+" + NX_FLOAT_RE + r"\s+"
    r"sidespin\s+" + NX_FLOAT_RE + r"\s+"
    r"totalspin\s+" + NX_FLOAT_RE
)
NX_SHOTDB_RE = re.compile(r"SHOTDB #:\s*(\d+)(?:\s*-\s*Cancel)?")
NX_PACKED_RE = re.compile(r"ShotDB packed:\s*(\S+\.grc)")
NX_VALUE_KEYS = [
    "ball_velocity",
    "ball_incidence",
    "ball_direction",
    "club_velocity",
    "club_incidence",
    "club_direction",
    "backspin",
    "sidespin",
    "totalspin",
]
NX_CSV_FLOAT_FIELDS = {
    "Ball Speed": "ball_velocity",
    "Ball Incidence": "ball_incidence",
    "Ball Direction": "ball_direction",
    "Club Speed": "club_velocity",
    "Attack Angle": "attack_angle",
    "Club Path": "club_path",
    "Face Angle": "face_angle",
    "Dynamic Loft": "dynamic_loft",
    "Back Spin": "backspin",
    "Side Spin": "sidespin",
    "Total Spin": "totalspin",
    "Spin Axis": "spin_axis",
    "Proc Time(1st)": "proc_time_1st",
    "Proc Time(2nd)": "proc_time_2nd",
    "Back Spin(Spin)": "backspin_spin",
    "Side Spin(Spin)": "sidespin_spin",
    "Total Spin(Spin)": "totalspin_spin",
    "Spin Axis(Spin)": "spin_axis_spin",
    "Confidence(Spin)": "confidence_spin",
    "Proc Time(Spin)": "proc_time_spin",
    "Back Spin(AI)": "backspin_ai",
    "Side Spin(AI)": "sidespin_ai",
    "Total Spin(AI)": "totalspin_ai",
    "Spin Axis(AI)": "spin_axis_ai",
    "Proc Time(AI)": "proc_time_ai",
    "Light Level(Spin)": "light_level_spin",
    "Frame Drop(Spin)": "frame_drop_spin",
    "Frame Drop(Side)": "frame_drop_side",
    "Frame Drop(Top)": "frame_drop_top",
}


def get_log_timestamp(line):
    match = LOG_TIMESTAMP_RE.search(line)
    return match.group(1) if match else ""


def get_today_nx_log_search_info(log_dir):
    directory = Path(log_dir).expanduser()
    date_token = datetime.now().strftime("%Y%m%d")
    name_token = f"NXSensorLog_{date_token}"
    search_pattern = f"{name_token}*.log"
    return directory, name_token, search_pattern


def get_today_nx_log_file(log_dir):
    directory, name_token, _ = get_today_nx_log_search_info(log_dir)

    try:
        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file() and name_token in path.name and path.suffix.lower() == ".log"
        ]
    except OSError:
        return None

    if not candidates:
        return None

    return max(candidates, key=lambda path: path.stat().st_mtime)


def parse_nx_noti_ball_line(line, line_number):
    match = NX_NOTI_BALL_RE.search(line)
    if not match:
        return None

    values = {key: float(value) for key, value in zip(NX_VALUE_KEYS[:6], match.groups())}
    values.update({"noti_line": line_number, "noti_time": get_log_timestamp(line)})
    return values


def parse_nx_noti_tv_line(line, line_number):
    match = NX_NOTI_TV_RE.search(line)
    if not match:
        return None

    _, backspin, sidespin, totalspin = match.groups()
    return {
        "tv_line": line_number,
        "tv_time": get_log_timestamp(line),
        "backspin": float(backspin),
        "sidespin": float(sidespin),
        "totalspin": float(totalspin),
    }


def parse_nx_packed_line(line, line_number):
    match = NX_PACKED_RE.search(line)
    if not match:
        return None

    packed_path = match.group(1)
    filename = Path(packed_path).name
    return {
        "packed_line": line_number,
        "packed_time": get_log_timestamp(line),
        "shotdb_path": packed_path,
        "shotdb_filename": filename,
        "shotdb_name": filename.removesuffix(".grc"),
    }


def get_nx_csv_path_from_packed_path(packed_path):
    grc_file = Path(packed_path)
    date_dir = grc_file.parent.name
    return grc_file.parent / f"NXShotData_{date_dir}.csv"


def read_nx_shot_csv_rows(csv_file):
    with open(csv_file, "r", encoding="utf-8-sig", errors="replace", newline="") as file_obj:
        lines = [line for line in file_obj if line.strip()]

    if not lines:
        raise ValueError("NXShotData CSV is empty.")

    if lines[0].strip().lower() == "nxsensor":
        lines = lines[1:]

    if len(lines) < 2:
        raise ValueError("NXShotData CSV does not contain a header and data row.")

    return list(csv.DictReader(lines))


def find_nx_shot_csv_row(csv_file, shotdb_name, shotdb_id):
    rows = read_nx_shot_csv_rows(csv_file)
    shotdb_id_text = str(shotdb_id or "").strip()

    for row in reversed(rows):
        if (row.get("ShotDB Name") or "").strip() == shotdb_name:
            return row

    if shotdb_id_text:
        for row in reversed(rows):
            if (row.get("ShotDB #") or "").strip() == shotdb_id_text:
                return row

    raise ValueError(f"NXShotData row was not found: ShotDB Name={shotdb_name}, ShotDB #={shotdb_id_text}")


def read_nx_shot_csv_row_with_retry(csv_file, shotdb_name, shotdb_id, stop_event=None):
    last_error = None
    for attempt in range(1, NX_CSV_READ_RETRIES + 1):
        try:
            return find_nx_shot_csv_row(csv_file, shotdb_name, shotdb_id)
        except Exception as exc:
            last_error = exc
            if attempt < NX_CSV_READ_RETRIES:
                if stop_event is not None:
                    stop_event.wait(NX_CSV_READ_RETRY_DELAY_SECONDS)
                else:
                    time.sleep(NX_CSV_READ_RETRY_DELAY_SECONDS)

    raise RuntimeError(f"Could not read NXShotData CSV after {NX_CSV_READ_RETRIES} attempts: {last_error}")


def parse_nx_csv_float(row, csv_field):
    return parse_optional_float((row.get(csv_field) or "").strip(), csv_field)


def normalize_nx_csv_row(row):
    data = {}
    for csv_field, output_field in NX_CSV_FLOAT_FIELDS.items():
        data[output_field] = parse_nx_csv_float(row, csv_field)

    data["shotdb_id"] = (row.get("ShotDB #") or "").strip()
    data["shot_id"] = data["shotdb_id"]
    data["timestamp"] = (row.get("Date") or "").strip()
    data["shotdb_name"] = (row.get("ShotDB Name") or "").strip()
    data["club_type"] = (row.get("Club Type") or "").strip()
    data["mat_type"] = (row.get("Mat Type") or "").strip()
    data["club_incidence"] = data.get("attack_angle", "")
    data["club_direction"] = data.get("club_path", "")
    data["ball_speed"] = data.get("ball_velocity", "")
    data["launch_angle"] = data.get("ball_incidence", "")
    data["spin_rate"] = data.get("totalspin", "")
    data["parsed"] = row
    return data


def nx_values_match(noti_values, csv_values):
    if not noti_values:
        return False

    try:
        return all(abs(float(noti_values[key]) - float(csv_values[key])) <= 0.01 for key in NX_VALUE_KEYS)
    except (KeyError, TypeError, ValueError):
        return False


def apply_nx_ai_spin_values(csv_values):
    for output_field, ai_field in (
        ("backspin", "backspin_ai"),
        ("sidespin", "sidespin_ai"),
        ("totalspin", "totalspin_ai"),
        ("spin_axis", "spin_axis_ai"),
    ):
        ai_value = csv_values.get(ai_field)
        if ai_value != "":
            csv_values[output_field] = ai_value

    csv_values["spin_rate"] = csv_values.get("totalspin", "")


def build_nx_shot_data(shot_state, packed_info, csv_row, csv_file, log_file):
    noti_values = shot_state.get("noti_values") or {}
    csv_values = normalize_nx_csv_row(csv_row)
    shotdb_id = str(csv_values.get("shotdb_id") or shot_state.get("shotdb_id", ""))
    noti_match = nx_values_match(noti_values, csv_values)
    apply_nx_ai_spin_values(csv_values)

    data = {
        **csv_values,
        "shot_id": shotdb_id,
        "shotdb_id": shotdb_id,
        "source_system": SOURCE_SYSTEM_NX,
        "source_format": "nx_csv_after_log",
        "shotdb_filename": packed_info.get("shotdb_filename", ""),
        "shotdb_path": packed_info.get("shotdb_path", ""),
        "nx_log_file": str(log_file),
        "nx_csv_file": str(csv_file),
        "noti_match": noti_match,
        "noti_line": noti_values.get("noti_line", ""),
        "tv_line": noti_values.get("tv_line", ""),
        "packed_line": packed_info.get("packed_line", ""),
        "packed_time": packed_info.get("packed_time", ""),
    }

    return data


class NxShotLogParser:
    def __init__(self, on_shot, on_log=None, stop_event=None):
        self.on_shot = on_shot
        self.on_log = on_log
        self.stop_event = stop_event
        self.pending_ball_values = None
        self.last_noti_values = None
        self.current_shot = None
        self.awaiting_packed = None
        self.seen_packed_paths = set()

    def _log(self, message):
        if self.on_log is not None:
            self.on_log(message)

    def feed_line(self, line, line_number, log_file):
        ball_values = parse_nx_noti_ball_line(line, line_number)
        if ball_values:
            self.pending_ball_values = ball_values
            return

        tv_values = parse_nx_noti_tv_line(line, line_number)
        if tv_values and self.pending_ball_values:
            self.last_noti_values = {**self.pending_ball_values, **tv_values}
            self.pending_ball_values = None
            return

        if "CGzStateSaveShotDB::SaveShotDB done" in line:
            self.current_shot = {
                "start_line": line_number,
                "start_time": get_log_timestamp(line),
                "shotdb_id": "",
                "cancel": False,
                "noti_values": self.last_noti_values.copy() if self.last_noti_values else None,
            }
            return

        packed_values = parse_nx_packed_line(line, line_number)
        if packed_values:
            self._handle_packed(packed_values, log_file)
            return

        if self.current_shot is None:
            return

        shotdb_match = NX_SHOTDB_RE.search(line)
        if shotdb_match:
            self.current_shot["shotdb_id"] = shotdb_match.group(1)
            self.current_shot["shotdb_line"] = line_number
            self.current_shot["cancel"] = "Cancel" in line

        if "CGzStateSaveShotDB::Exit" in line:
            self.current_shot["exit_line"] = line_number
            self.current_shot["exit_time"] = get_log_timestamp(line)
            if self.current_shot.get("cancel"):
                self._log(f"[NX+] Cancel shot ignored at line {line_number}.")
            elif not self.current_shot.get("noti_values"):
                self._log(f"[NX+] Shot ignored because NOTI_2ND/TV was not detected before line {line_number}.")
            else:
                self.current_shot["packed_deadline"] = time.monotonic() + NX_PACKED_WAIT_SECONDS
                self.awaiting_packed = self.current_shot
            self.current_shot = None

    def _handle_packed(self, packed_info, log_file):
        packed_path = packed_info.get("shotdb_path", "")
        if packed_path in self.seen_packed_paths:
            return

        if self.awaiting_packed is None:
            self._log(f"[NX+] ShotDB packed found without pending shot: {packed_info.get('shotdb_filename', '')}")
            return

        csv_file = get_nx_csv_path_from_packed_path(packed_path)
        try:
            csv_row = read_nx_shot_csv_row_with_retry(
                csv_file,
                packed_info.get("shotdb_name", ""),
                self.awaiting_packed.get("shotdb_id", ""),
                self.stop_event,
            )
            data = build_nx_shot_data(self.awaiting_packed, packed_info, csv_row, csv_file, log_file)
        except Exception as exc:
            self._log(f"[NX+] Failed to read NXShotData CSV for {packed_info.get('shotdb_filename', '')}: {exc}")
            self.awaiting_packed = None
            return

        self.seen_packed_paths.add(packed_path)
        self.awaiting_packed = None
        self.on_shot(data)

    def expire_pending(self):
        if self.awaiting_packed and time.monotonic() > self.awaiting_packed.get("packed_deadline", 0):
            shotdb_id = self.awaiting_packed.get("shotdb_id", "")
            self._log(f"[NX+] Timed out waiting for ShotDB packed after SHOTDB #{shotdb_id}.")
            self.awaiting_packed = None


class NxLogMonitorThread(threading.Thread):
    def __init__(self, log_dir, log_file, on_shot, stop_event):
        super().__init__(daemon=True)
        self.log_dir = log_dir
        self.log_file = Path(log_file).expanduser() if log_file else None
        self.on_shot = on_shot
        self.stop_event = stop_event
        self.parser = NxShotLogParser(on_shot, emit_log, stop_event)

    def run(self):
        current_log_file = None
        file_obj = None
        line_number = 0
        last_refresh = 0
        missing_logged = False

        if self.log_file is not None:
            emit_log(f"[NX+] Monitoring selected log file: {self.log_file}")
        else:
            emit_log(f"[NX+] Monitoring log directory: {self.log_dir}")

        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if self.log_file is not None and file_obj is None:
                    if not self.log_file.is_file():
                        if not missing_logged:
                            emit_log(f"[NX+] Selected NX+ log file was not found: {self.log_file}")
                            missing_logged = True
                        self.stop_event.wait(NX_LOG_POLL_SECONDS)
                        continue

                    missing_logged = False
                    current_log_file = self.log_file
                    file_obj = open(current_log_file, "r", encoding="utf-8", errors="replace")
                    file_obj.seek(0, 2)
                    line_number = 0
                    emit_log(f"[NX+] Tailing log file: {current_log_file}")

                elif self.log_file is None and (file_obj is None or now - last_refresh >= NX_LOG_FILE_REFRESH_SECONDS):
                    last_refresh = now
                    today_log_file = get_today_nx_log_file(self.log_dir)
                    if today_log_file is None:
                        if not missing_logged:
                            directory, _, search_pattern = get_today_nx_log_search_info(self.log_dir)
                            emit_log(
                                f"[NX+] Today's NXSensorLog file was not found yet. "
                                f"Searched: {directory / search_pattern}"
                            )
                            missing_logged = True
                        self.stop_event.wait(NX_LOG_POLL_SECONDS)
                        continue

                    missing_logged = False
                    if today_log_file != current_log_file:
                        if file_obj is not None:
                            file_obj.close()
                        current_log_file = today_log_file
                        file_obj = open(current_log_file, "r", encoding="utf-8", errors="replace")
                        file_obj.seek(0, 2)
                        line_number = 0
                        emit_log(f"[NX+] Tailing log file: {current_log_file}")

                position = file_obj.tell()
                line = file_obj.readline()
                if line:
                    line_number += 1
                    self.parser.feed_line(line.rstrip("\r\n"), line_number, current_log_file)
                    continue

                try:
                    if current_log_file.stat().st_size < position:
                        emit_log("[NX+] Log file was truncated. Reopening from the beginning.")
                        file_obj.close()
                        file_obj = open(current_log_file, "r", encoding="utf-8", errors="replace")
                        line_number = 0
                except OSError:
                    if file_obj is not None:
                        file_obj.close()
                    file_obj = None
                    current_log_file = None

                self.parser.expire_pending()
                self.stop_event.wait(NX_LOG_POLL_SECONDS)
        except Exception as exc:
            emit_log(f"[NX+] Monitor stopped with error: {exc}")
        finally:
            if file_obj is not None:
                file_obj.close()
            emit_log("[NX+] Monitor stopped.")


def store_latest_shot(normalized, log_message):
    global latest_shot

    with data_lock:
        latest_shot = EMPTY_SHOT.copy()
        latest_shot.update(normalized)
        latest_shot["received_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        shot_to_publish = latest_shot.copy()

        json_saved = True
        csv_saved = True

        try:
            save_latest_shot_json(shot_to_publish)
        except Exception as exc:
            json_saved = False
            emit_log(f"Failed to save latest JSON: {exc}")

        try:
            append_shot_history_csv(shot_to_publish)
        except Exception as exc:
            csv_saved = False
            emit_log(f"Failed to append history CSV: {exc}")

    publish_shot_event(shot_to_publish)
    if server_ui is not None:
        server_ui.root.after(0, lambda shot=shot_to_publish: server_ui.apply_latest_data(shot))
    emit_log(f"{log_message}: {shot_to_publish}")
    return json_saved, csv_saved, shot_to_publish


def emit_log(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    if server_ui is not None:
        server_ui.enqueue_log(line)


def get_bind_display_host(host):
    if host == "0.0.0.0":
        return "127.0.0.1"
    return host


def get_local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def get_events_url(server_url):
    url = (server_url or DEFAULT_VIEWER_URL).strip().rstrip("/")
    for suffix in ("/latest", "/shot", "/events"):
        if url.endswith(suffix):
            return f"{url[:-len(suffix)]}/events"
    return f"{url}/events"


def make_sse_event(event_name, data):
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_name}\ndata: {payload}\n\n"


def remove_sse_client(client_queue):
    with sse_clients_lock:
        if client_queue in sse_clients:
            sse_clients.remove(client_queue)


def publish_shot_event(data):
    event_text = make_sse_event("shot", data)

    with sse_clients_lock:
        clients = list(sse_clients)

    for client_queue in clients:
        try:
            client_queue.put_nowait(event_text)
        except Full:
            remove_sse_client(client_queue)


def stream_shot_events():
    client_queue = Queue(maxsize=10)
    with sse_clients_lock:
        sse_clients.append(client_queue)

    try:
        yield make_sse_event("connected", {"ok": True})
        while True:
            try:
                yield client_queue.get(timeout=15)
            except Empty:
                yield ": keep-alive\n\n"
    finally:
        remove_sse_client(client_queue)


@flask_app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            "ok": True,
            "message": "Shot data server is running.",
            "endpoints": {
                "latest": "/latest",
                "shot_post": "/shot",
                "events": "/events",
            },
        }
    )


@flask_app.route("/shot", methods=["POST"])
def receive_shot():
    try:
        normalized = normalize_request_shot()
    except ValueError as exc:
        emit_log(f"Rejected invalid payload: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 400

    json_saved, csv_saved, _ = store_latest_shot(normalized, "Received GCQuad shot")
    return jsonify({"ok": True, "json_saved": json_saved, "csv_saved": csv_saved})


@flask_app.route("/latest", methods=["GET"])
def get_latest():
    with data_lock:
        return jsonify(latest_shot.copy())


@flask_app.route("/events", methods=["GET"])
def shot_events():
    return Response(
        stream_shot_events(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class ServerThread(threading.Thread):
    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.http_server = None
        self.start_error = None
        self.ready_event = threading.Event()

    def run(self):
        try:
            self.http_server = make_server(self.host, self.port, flask_app, threaded=True)
        except Exception as exc:
            self.start_error = exc
            self.ready_event.set()
            return

        self.ready_event.set()
        self.http_server.serve_forever()

    def shutdown(self):
        if self.http_server is not None:
            self.http_server.shutdown()


class ServerViewerTabsApp:
    def __init__(self, root):
        global latest_shot, server_ui

        server_ui = self
        latest_shot = load_latest_shot()

        self.root = root
        self.root.title("Shot Data Server / Viewer")
        self.root.geometry("920x760")

        self.server_thread = None
        self.nx_monitor_thread = None
        self.nx_monitor_stop_event = threading.Event()
        self.log_queue = Queue()

        self.sse_thread = None
        self.sse_stop_event = threading.Event()
        self.sse_response = None
        self.sse_response_lock = threading.Lock()
        self.last_data_signature = None
        self.saved_merge_keys = load_existing_shot_merge_keys()
        self.saved_gcq_merge_keys, self.saved_nx_merge_keys = load_existing_shot_merge_source_keys()

        host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout = load_server_config()
        viewer_url, selected_metric_keys = load_viewer_config()

        self.host_var = tk.StringVar(value=host)
        self.port_var = tk.StringVar(value=str(port))
        self.debug_var = tk.BooleanVar(value=debug)
        self.nx_log_dir_var = tk.StringVar(value=nx_log_dir)
        self.nx_log_file_var = tk.StringVar(value=nx_log_file)
        self.counterpart_receive_timeout_var = tk.StringVar(value=str(counterpart_receive_timeout))
        self.merge_require_both_var = tk.BooleanVar(value=True)
        self.server_status_var = tk.StringVar(value="Status: Stopped")
        self.nx_status_var = tk.StringVar(value="NX+ Monitor: Stopped")
        self.listen_var = tk.StringVar(value="")
        self.local_url_var = tk.StringVar(value="")
        self.network_url_var = tk.StringVar(value="")

        self.viewer_url_var = tk.StringVar(value=viewer_url)
        self.events_url_var = tk.StringVar(value=get_events_url(viewer_url))
        self.viewer_status_var = tk.StringVar(value="Status: Disconnected")
        self.selected_metric_keys = selected_metric_keys
        self.displayed_metrics_var = tk.StringVar(value="")
        self.source_header_vars = {
            SOURCE_SYSTEM_GCQUAD: tk.StringVar(value=SOURCE_SYSTEM_GCQUAD),
            SOURCE_SYSTEM_NX: tk.StringVar(value=SOURCE_SYSTEM_NX),
        }
        self.source_unavailable = {
            SOURCE_SYSTEM_GCQUAD: False,
            SOURCE_SYSTEM_NX: False,
        }
        self.last_source_received_at = {
            SOURCE_SYSTEM_GCQUAD: None,
            SOURCE_SYSTEM_NX: None,
        }
        self.counterpart_timer_generation = 0
        self.latest_source_data = {
            SOURCE_SYSTEM_GCQUAD: {},
            SOURCE_SYSTEM_NX: {},
        }
        self.compare_vars = {
            source: {key: tk.StringVar(value="-") for key, _ in AVAILABLE_COMPARE_METRICS}
            for source in (SOURCE_SYSTEM_GCQUAD, SOURCE_SYSTEM_NX)
        }

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)

        self.server_tab = ttk.Frame(self.notebook, padding=16)
        self.viewer_tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(self.server_tab, text="Server")
        self.notebook.add(self.viewer_tab, text="Viewer")

        self._build_server_tab()
        self._build_viewer_tab()
        self._set_connection_labels(host, port)
        self.apply_latest_data(latest_shot, record_receive=False)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(LOG_POLL_MS, self.poll_logs)
        self.start_server()
        self.start_nx_monitor(prompt_on_missing=False)
        self.connect_sse()

    def _build_server_tab(self):
        ttk.Label(self.server_tab, text="Shot Data Server", font=("Arial", 16, "bold")).pack(anchor="w", pady=(0, 12))

        settings_frame = ttk.LabelFrame(self.server_tab, text="Settings", padding=12)
        settings_frame.pack(fill="x", pady=(0, 10))

        host_row = ttk.Frame(settings_frame)
        host_row.pack(fill="x", pady=4)
        ttk.Label(host_row, text="Host", width=12).pack(side="left")
        ttk.Entry(host_row, textvariable=self.host_var, width=24).pack(side="left")

        port_row = ttk.Frame(settings_frame)
        port_row.pack(fill="x", pady=4)
        ttk.Label(port_row, text="Port", width=12).pack(side="left")
        ttk.Entry(port_row, textvariable=self.port_var, width=12).pack(side="left")
        ttk.Checkbutton(port_row, text="Debug", variable=self.debug_var).pack(side="left", padx=(12, 0))

        nx_row = ttk.Frame(settings_frame)
        nx_row.pack(fill="x", pady=4)
        ttk.Label(nx_row, text="NX+ Log Dir", width=12).pack(side="left")
        ttk.Entry(nx_row, textvariable=self.nx_log_dir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(nx_row, text="Browse", command=self.browse_nx_log_dir).pack(side="left", padx=(8, 0))

        nx_file_row = ttk.Frame(settings_frame)
        nx_file_row.pack(fill="x", pady=4)
        ttk.Label(nx_file_row, text="NX+ Log File", width=12).pack(side="left")
        ttk.Entry(nx_file_row, textvariable=self.nx_log_file_var).pack(side="left", fill="x", expand=True)
        ttk.Button(nx_file_row, text="Browse", command=self.browse_nx_log_file).pack(side="left", padx=(8, 0))
        ttk.Button(nx_file_row, text="Clear", command=lambda: self.nx_log_file_var.set("")).pack(side="left", padx=(8, 0))

        merge_row = ttk.Frame(settings_frame)
        merge_row.pack(fill="x", pady=4)
        ttk.Label(merge_row, text="ShotMerge", width=12).pack(side="left")
        ttk.Checkbutton(
            merge_row,
            text="Save only when both GCQuad and NX+ shots arrive",
            variable=self.merge_require_both_var,
        ).pack(side="left")

        timeout_row = ttk.Frame(settings_frame)
        timeout_row.pack(fill="x", pady=4)
        ttk.Label(timeout_row, text="Wait Seconds", width=12).pack(side="left")
        ttk.Entry(timeout_row, textvariable=self.counterpart_receive_timeout_var, width=12).pack(side="left")

        button_row = ttk.Frame(settings_frame)
        button_row.pack(fill="x", pady=(8, 0))
        ttk.Button(button_row, text="Save", command=self.save_server_settings).pack(side="left")
        ttk.Button(button_row, text="Start Server", command=self.start_server).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Stop Server", command=self.stop_server).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Start NX+ Monitor", command=self.start_nx_monitor).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Stop NX+ Monitor", command=self.stop_nx_monitor).pack(side="left", padx=(8, 0))

        status_frame = ttk.LabelFrame(self.server_tab, text="Status", padding=12)
        status_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(status_frame, textvariable=self.server_status_var).pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.nx_status_var).pack(anchor="w", pady=(6, 0))
        ttk.Label(status_frame, textvariable=self.listen_var).pack(anchor="w", pady=(6, 0))
        ttk.Label(status_frame, textvariable=self.local_url_var).pack(anchor="w", pady=(2, 0))
        ttk.Label(status_frame, textvariable=self.network_url_var).pack(anchor="w", pady=(2, 0))

        log_frame = ttk.LabelFrame(self.server_tab, text="Logs", padding=8)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, height=16, state="disabled", wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _build_viewer_tab(self):
        ttk.Label(self.viewer_tab, text="Shot Viewer", font=("Arial", 16, "bold")).pack(anchor="w", pady=(0, 12))

        settings_frame = ttk.LabelFrame(self.viewer_tab, text="Connection", padding=12)
        settings_frame.pack(fill="x", pady=(0, 10))

        url_row = ttk.Frame(settings_frame)
        url_row.pack(fill="x", pady=4)
        ttk.Label(url_row, text="Latest URL", width=12).pack(side="left")
        ttk.Entry(url_row, textvariable=self.viewer_url_var).pack(side="left", fill="x", expand=True)

        events_row = ttk.Frame(settings_frame)
        events_row.pack(fill="x", pady=4)
        ttk.Label(events_row, text="SSE URL", width=12).pack(side="left")
        ttk.Label(events_row, textvariable=self.events_url_var).pack(side="left", fill="x", expand=True)

        button_row = ttk.Frame(settings_frame)
        button_row.pack(fill="x", pady=(8, 0))
        ttk.Button(button_row, text="Save", command=self.save_viewer_settings).pack(side="left")
        ttk.Button(button_row, text="Refresh Once", command=self.refresh_latest_once).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Connect SSE", command=self.connect_sse).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Disconnect SSE", command=self.disconnect_sse).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Select Metrics", command=self.open_metric_selector).pack(side="left", padx=(8, 0))
        ttk.Label(settings_frame, textvariable=self.displayed_metrics_var).pack(anchor="w", pady=(6, 0))

        shot_frame = ttk.LabelFrame(self.viewer_tab, text="Latest Shot", padding=12)
        shot_frame.pack(fill="x", pady=(0, 10))

        self.compare_grid = ttk.Frame(shot_frame)
        self.compare_grid.pack(fill="x")
        self.compare_grid.columnconfigure(0, weight=1)
        self.compare_grid.columnconfigure(1, weight=1)
        self.compare_grid.columnconfigure(2, weight=1)
        self.render_compare_table()

        status_frame = ttk.LabelFrame(self.viewer_tab, text="Status", padding=12)
        status_frame.pack(fill="x")
        ttk.Label(status_frame, textvariable=self.viewer_status_var).pack(anchor="w")

    def render_compare_table(self):
        for child in self.compare_grid.winfo_children():
            child.destroy()

        ttk.Label(self.compare_grid, text="Metric", font=("Arial", 11, "bold")).grid(
            row=0, column=0, sticky="w", padx=6, pady=5
        )
        ttk.Label(
            self.compare_grid,
            textvariable=self.source_header_vars[SOURCE_SYSTEM_GCQUAD],
            font=("Arial", 11, "bold"),
        ).grid(
            row=0, column=1, sticky="w", padx=6, pady=5
        )
        ttk.Label(
            self.compare_grid,
            textvariable=self.source_header_vars[SOURCE_SYSTEM_NX],
            font=("Arial", 11, "bold"),
        ).grid(
            row=0, column=2, sticky="w", padx=6, pady=5
        )

        metric_labels = dict(AVAILABLE_COMPARE_METRICS)
        for row_index, metric_key in enumerate(self.selected_metric_keys, start=1):
            self._add_compare_row(self.compare_grid, row_index, metric_labels.get(metric_key, metric_key), metric_key)

        self.update_displayed_metrics_label()
        self.refresh_compare_table()

    def _add_compare_row(self, parent, row_index, label_text, metric_key):
        ttk.Label(parent, text=label_text).grid(row=row_index, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(
            parent,
            textvariable=self.compare_vars[SOURCE_SYSTEM_GCQUAD][metric_key],
            font=("Arial", 11, "bold"),
            width=16,
        ).grid(row=row_index, column=1, sticky="w", padx=6, pady=5)
        ttk.Label(
            parent,
            textvariable=self.compare_vars[SOURCE_SYSTEM_NX][metric_key],
            font=("Arial", 11, "bold"),
            width=16,
        ).grid(
            row=row_index, column=2, sticky="w", padx=6, pady=5
        )

    def update_displayed_metrics_label(self):
        self.displayed_metrics_var.set(f"Displayed metrics: {len(self.selected_metric_keys)} selected")

    def open_metric_selector(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Select Viewer Metrics")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("620x560")
        dialog.resizable(False, False)

        outer = ttk.Frame(dialog, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Viewer Metrics", font=("Arial", 13, "bold")).pack(anchor="w", pady=(0, 8))

        content = ttk.Frame(outer)
        content.pack(fill="both", expand=True)

        button_panel = ttk.LabelFrame(content, text="Actions", padding=8, width=150)
        button_panel.pack(side="left", fill="y", padx=(0, 14), anchor="nw")
        button_panel.pack_propagate(False)

        list_panel = ttk.Frame(content)
        list_panel.pack(side="left", fill="both", expand=True)

        canvas = tk.Canvas(list_panel, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_panel, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        metric_vars = {}
        selected = set(self.selected_metric_keys)
        for metric_key, label_text in AVAILABLE_COMPARE_METRICS:
            var = tk.BooleanVar(value=metric_key in selected)
            metric_vars[metric_key] = var
            ttk.Checkbutton(body, text=label_text, variable=var).pack(anchor="w", pady=3)

        def set_all(value):
            for var in metric_vars.values():
                var.set(value)

        def set_default():
            defaults = set(DEFAULT_COMPARE_METRIC_KEYS)
            for key, var in metric_vars.items():
                var.set(key in defaults)

        def apply_selection():
            selected_keys = [key for key, _ in AVAILABLE_COMPARE_METRICS if metric_vars[key].get()]
            if not selected_keys:
                messagebox.showerror("No metrics selected", "Please select at least one metric.")
                return

            self.selected_metric_keys = selected_keys
            self.render_compare_table()
            self.save_viewer_settings()
            dialog.destroy()

        ttk.Button(button_panel, text="All", command=lambda: set_all(True), width=16).pack(fill="x", pady=(0, 8))
        ttk.Button(button_panel, text="Default", command=set_default, width=16).pack(fill="x", pady=(0, 8))
        ttk.Button(button_panel, text="Clear", command=lambda: set_all(False), width=16).pack(fill="x", pady=(0, 18))
        ttk.Button(button_panel, text="Apply & Save", command=apply_selection, width=16).pack(fill="x", pady=(0, 8))
        ttk.Button(button_panel, text="Cancel", command=dialog.destroy, width=16).pack(fill="x")

    def validate_server_settings(self):
        host = self.host_var.get().strip() or DEFAULT_HOST
        port_text = self.port_var.get().strip() or str(DEFAULT_PORT)
        nx_log_dir = self.nx_log_dir_var.get().strip() or DEFAULT_NX_LOG_DIR
        nx_log_file = self.nx_log_file_var.get().strip()
        timeout_text = self.counterpart_receive_timeout_var.get().strip() or str(COUNTERPART_RECEIVE_TIMEOUT_SECONDS)

        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be a number between 1 and 65535.")
            return None

        try:
            counterpart_receive_timeout = float(timeout_text)
            if counterpart_receive_timeout <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid wait seconds", "Wait Seconds must be a number greater than 0.")
            return None

        return host, port, self.debug_var.get(), nx_log_dir, nx_log_file, counterpart_receive_timeout

    def save_server_settings(self):
        settings = self.validate_server_settings()
        if not settings:
            return

        host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout = settings
        save_server_config(host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout)
        self.host_var.set(host)
        self.port_var.set(str(port))
        self.nx_log_dir_var.set(nx_log_dir)
        self.nx_log_file_var.set(nx_log_file)
        self.counterpart_receive_timeout_var.set(str(counterpart_receive_timeout))
        self._set_connection_labels(host, port)
        self.enqueue_log("[INFO] Server settings saved.")

    def get_existing_log_initial_dir(self, preferred_dir=None):
        candidates = [
            preferred_dir,
            self.nx_log_dir_var.get().strip(),
            DEFAULT_NX_LOG_DIR,
            str(APP_DIR),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            if path.is_file():
                path = path.parent
            if path.is_dir():
                return str(path)
        return str(APP_DIR)

    def select_nx_log_file(self, initial_dir=None):
        selected = filedialog.askopenfilename(
            initialdir=self.get_existing_log_initial_dir(initial_dir),
            title="Select NX+ log file",
            filetypes=[
                ("NX+ log files", "NXSensorLog_*.log"),
                ("Log files", "*.log"),
                ("All files", "*"),
            ],
        )
        if not selected:
            return None

        selected_path = Path(selected).expanduser()
        self.nx_log_file_var.set(str(selected_path))
        self.nx_log_dir_var.set(str(selected_path.parent))
        return selected_path

    def get_valid_selected_nx_log_file(self, nx_log_file):
        if not nx_log_file:
            return None
        path = Path(nx_log_file).expanduser()
        if path.is_file():
            return path
        return None

    def browse_nx_log_dir(self):
        initial_dir = self.get_existing_log_initial_dir(self.nx_log_dir_var.get().strip() or DEFAULT_NX_LOG_DIR)
        selected = filedialog.askdirectory(initialdir=initial_dir, title="Select NX+ log directory")
        if selected:
            self.nx_log_dir_var.set(selected)
            self.nx_log_file_var.set("")

    def browse_nx_log_file(self):
        self.select_nx_log_file(self.nx_log_dir_var.get().strip() or DEFAULT_NX_LOG_DIR)

    def start_server(self):
        if self.server_thread is not None:
            self.enqueue_log("[INFO] Server is already running.")
            return

        settings = self.validate_server_settings()
        if not settings:
            return

        host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout = settings
        save_server_config(host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout)
        self._set_connection_labels(host, port)

        self.server_thread = ServerThread(host, port)
        self.server_thread.start()
        self.server_thread.ready_event.wait(timeout=5)

        if self.server_thread.start_error:
            self.server_status_var.set("Status: Failed to start")
            self.enqueue_log(f"[ERROR] Failed to start server: {self.server_thread.start_error}")
            self.server_thread = None
            return

        self.server_status_var.set("Status: Running")
        self.enqueue_log(f"[START] App directory: {APP_DIR}")
        self.enqueue_log(f"[START] Config file: {SERVER_CONFIG_PATH}")
        self.enqueue_log(f"[START] Latest JSON: {LATEST_JSON_PATH}")
        self.enqueue_log(f"[START] History CSV: {HISTORY_CSV_PATH}")
        self.enqueue_log(f"[START] Listening on http://{host}:{port}")

    def stop_server(self):
        if self.server_thread is None:
            return

        self.server_status_var.set("Status: Stopping")
        self.enqueue_log("[STOP] Shutting down server...")
        self.server_thread.shutdown()
        self.server_thread.join(timeout=5)
        self.server_thread = None
        self.server_status_var.set("Status: Stopped")
        self.enqueue_log("[STOP] Server stopped.")

    def start_nx_monitor(self, prompt_on_missing=True):
        if self.nx_monitor_thread is not None and self.nx_monitor_thread.is_alive():
            self.nx_status_var.set("NX+ Monitor: Already running")
            return

        settings = self.validate_server_settings()
        if not settings:
            return

        host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout = settings
        today_log_file = get_today_nx_log_file(nx_log_dir)
        selected_log_file = self.get_valid_selected_nx_log_file(nx_log_file)
        manual_log_file = None
        active_log_file = today_log_file

        if today_log_file is None:
            directory, _, search_pattern = get_today_nx_log_search_info(nx_log_dir)
            searched_log_file = directory / search_pattern

            if nx_log_file and selected_log_file is None:
                self.enqueue_log(f"[NX+] Selected NX+ log file is not available: {nx_log_file}")

            if selected_log_file is None and prompt_on_missing:
                self.enqueue_log(
                    f"[NX+] Today's NXSensorLog file was not found. Searched: {searched_log_file}. "
                    "Select a log file to monitor."
                )
                selected_log_file = self.select_nx_log_file(DEFAULT_NX_LOG_DIR)
                if selected_log_file is not None:
                    nx_log_dir = str(selected_log_file.parent)
                    nx_log_file = str(selected_log_file)

            if selected_log_file is None:
                self.nx_status_var.set("NX+ Monitor: Stopped")
                self.enqueue_log(
                    f"[NX+] Today's NXSensorLog file was not found. Searched: {searched_log_file}. "
                    "NX+ monitor was not started."
                )
                return

            manual_log_file = selected_log_file
            active_log_file = selected_log_file
            nx_log_dir = str(selected_log_file.parent)
            nx_log_file = str(selected_log_file)
            self.enqueue_log(f"[NX+] Using selected NX+ log file: {selected_log_file}")
        else:
            nx_log_file = str(today_log_file)

        save_server_config(host, port, debug, nx_log_dir, nx_log_file, counterpart_receive_timeout)
        self.nx_log_dir_var.set(nx_log_dir)
        self.nx_log_file_var.set(nx_log_file)

        self.nx_monitor_stop_event.clear()
        self.nx_monitor_thread = NxLogMonitorThread(
            nx_log_dir,
            manual_log_file,
            self.handle_nx_shot,
            self.nx_monitor_stop_event,
        )
        self.nx_monitor_thread.start()
        self.nx_status_var.set(f"NX+ Monitor: Running ({active_log_file})")
        self.enqueue_log(f"[NX+] Monitor started. Log file: {active_log_file}")

    def stop_nx_monitor(self):
        if self.nx_monitor_thread is None:
            self.nx_status_var.set("NX+ Monitor: Stopped")
            return

        self.nx_status_var.set("NX+ Monitor: Stopping")
        self.nx_monitor_stop_event.set()
        self.nx_monitor_thread.join(timeout=3)
        self.nx_monitor_thread = None
        self.nx_status_var.set("NX+ Monitor: Stopped")
        self.enqueue_log("[NX+] Monitor stop requested.")

    def handle_nx_shot(self, data):
        _, _, shot_to_publish = store_latest_shot(data, "Received NX+ shot")
        shot_id = shot_to_publish.get("shot_id", "-") or "-"
        self.root.after(0, lambda: self.nx_status_var.set(f"NX+ Monitor: Latest SHOTDB #{shot_id}"))

    def _set_connection_labels(self, host, port):
        display_host = get_bind_display_host(host)
        local_ip = get_local_ip()
        self.listen_var.set(f"Listening: {host}:{port}")
        self.local_url_var.set(f"Local URL: http://{display_host}:{port}/")
        self.network_url_var.set(f"Network URL: http://{local_ip}:{port}/")

    def enqueue_log(self, message):
        self.log_queue.put(message)

    def append_log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def poll_logs(self):
        try:
            while True:
                self.append_log(self.log_queue.get_nowait())
        except Empty:
            pass

        self.root.after(LOG_POLL_MS, self.poll_logs)

    def save_viewer_settings(self):
        server_url = self.viewer_url_var.get().strip() or DEFAULT_VIEWER_URL
        save_viewer_config(server_url, self.selected_metric_keys)
        self.viewer_url_var.set(server_url)
        self.events_url_var.set(get_events_url(server_url))
        self.update_displayed_metrics_label()
        self.viewer_status_var.set("Status: Viewer settings saved")

    def refresh_latest_once(self):
        self.save_viewer_settings()
        thread = threading.Thread(target=self._refresh_latest_worker, daemon=True)
        thread.start()

    def _refresh_latest_worker(self):
        server_url = self.viewer_url_var.get().strip() or DEFAULT_VIEWER_URL

        try:
            response = requests.get(server_url, timeout=3)
            response.raise_for_status()
            data = response.json()
            self.root.after(0, lambda: self.apply_latest_data(data, record_receive=False))
            self.root.after(0, lambda: self.viewer_status_var.set("Status: Latest data refreshed"))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda: self.viewer_status_var.set(f"Status: Refresh failed ({message})"))

    def connect_sse(self):
        if self.sse_thread is not None and self.sse_thread.is_alive():
            self.viewer_status_var.set("Status: SSE already connected")
            return

        self.save_viewer_settings()
        self.sse_stop_event.clear()
        self.sse_thread = threading.Thread(target=self._sse_worker, daemon=True)
        self.sse_thread.start()
        self.viewer_status_var.set("Status: Connecting SSE")

    def disconnect_sse(self):
        self.sse_stop_event.set()
        with self.sse_response_lock:
            if self.sse_response is not None:
                self.sse_response.close()
                self.sse_response = None
        self.viewer_status_var.set("Status: SSE disconnected")

    def _sse_worker(self):
        while not self.sse_stop_event.is_set():
            events_url = get_events_url(self.viewer_url_var.get())

            try:
                with requests.get(events_url, stream=True, timeout=(3, 20)) as response:
                    response.raise_for_status()
                    with self.sse_response_lock:
                        self.sse_response = response

                    self.root.after(0, lambda: self.viewer_status_var.set("Status: SSE connected"))
                    self._read_sse_response(response)
            except Exception as exc:
                if not self.sse_stop_event.is_set():
                    message = str(exc)
                    self.root.after(0, lambda: self.viewer_status_var.set(f"Status: SSE reconnecting ({message})"))
                    self.sse_stop_event.wait(SSE_RECONNECT_SECONDS)
            finally:
                with self.sse_response_lock:
                    self.sse_response = None

        self.root.after(0, lambda: self.viewer_status_var.set("Status: SSE disconnected"))

    def _read_sse_response(self, response):
        event_name = "message"
        data_lines = []

        for raw_line in response.iter_lines(decode_unicode=True):
            if self.sse_stop_event.is_set():
                break

            line = raw_line or ""
            if line == "":
                self._dispatch_sse_event(event_name, data_lines)
                event_name = "message"
                data_lines = []
                continue

            if line.startswith(":"):
                continue

            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

    def _dispatch_sse_event(self, event_name, data_lines):
        if not data_lines:
            return

        payload = "\n".join(data_lines)
        if event_name == "connected":
            self.root.after(0, lambda: self.viewer_status_var.set("Status: SSE connected"))
            return

        if event_name != "shot":
            return

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self.root.after(0, lambda: self.viewer_status_var.set("Status: Invalid SSE data"))
            return

        self.root.after(0, lambda: self.apply_latest_data(data, record_receive=False))

    def format_display_value(self, value):
        if value in (None, ""):
            return "-"
        if isinstance(value, bool):
            return "OK" if value else "Mismatch"
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)

    def infer_source_system(self, data):
        source_system = data.get("source_system", "")
        if source_system:
            return SOURCE_SYSTEM_GCQUAD if source_system == "FSX" else source_system

        source_format = data.get("source_format", "")
        if str(source_format).startswith("nx_"):
            return SOURCE_SYSTEM_NX
        if source_format:
            return SOURCE_SYSTEM_GCQUAD
        return "-"

    def get_compare_value(self, data, metric_key):
        if metric_key == "ball_speed":
            return data.get("ball_speed", "") or data.get("ball_velocity", "")
        if metric_key == "launch_angle":
            return data.get("launch_angle", "") or data.get("ball_incidence", "")
        if metric_key == "attack_angle":
            return data.get("attack_angle", "") or data.get("club_incidence", "")
        if metric_key == "club_path":
            return data.get("club_path", "") or data.get("club_direction", "")
        if metric_key == "totalspin":
            return data.get("totalspin", "") or data.get("spin_rate", "")
        return data.get(metric_key, "")

    def refresh_compare_table(self):
        for source, source_data in self.latest_source_data.items():
            for metric_key in self.selected_metric_keys:
                self.compare_vars[source][metric_key].set(
                    self.format_display_value(self.get_compare_value(source_data, metric_key))
                )

    def update_source_headers(self):
        for source in (SOURCE_SYSTEM_GCQUAD, SOURCE_SYSTEM_NX):
            title = source
            if self.source_unavailable.get(source):
                title = f"{source} (수신 불가)"
            self.source_header_vars[source].set(title)

    def get_counterpart_source(self, source_system):
        if source_system == SOURCE_SYSTEM_GCQUAD:
            return SOURCE_SYSTEM_NX
        if source_system == SOURCE_SYSTEM_NX:
            return SOURCE_SYSTEM_GCQUAD
        return None

    def get_counterpart_receive_timeout_seconds(self):
        try:
            value = float(self.counterpart_receive_timeout_var.get().strip())
            if value > 0:
                return value
        except ValueError:
            pass
        return COUNTERPART_RECEIVE_TIMEOUT_SECONDS

    def track_source_receive_status(self, source_system):
        counterpart = self.get_counterpart_source(source_system)
        if counterpart is None:
            return

        timeout_seconds = self.get_counterpart_receive_timeout_seconds()
        now = time.monotonic()
        self.last_source_received_at[source_system] = now
        self.source_unavailable[source_system] = False

        counterpart_received_at = self.last_source_received_at.get(counterpart)
        if counterpart_received_at is not None and abs(now - counterpart_received_at) <= timeout_seconds:
            self.source_unavailable[counterpart] = False
            self.update_source_headers()
            return

        self.source_unavailable[counterpart] = False
        self.counterpart_timer_generation += 1
        generation = self.counterpart_timer_generation
        delay_ms = int(timeout_seconds * 1000)
        self.root.after(delay_ms, lambda: self.mark_counterpart_unavailable_if_needed(counterpart, now, generation))
        self.update_source_headers()

    def mark_counterpart_unavailable_if_needed(self, counterpart, trigger_time, generation):
        if generation != self.counterpart_timer_generation:
            return

        counterpart_received_at = self.last_source_received_at.get(counterpart)
        if counterpart_received_at is not None and counterpart_received_at >= trigger_time:
            return

        self.source_unavailable[counterpart] = True
        self.update_source_headers()

    def get_shot_merge_key(self, gcq_data, nx_data):
        gcq_shot_key = str(gcq_data.get("shot_id") or gcq_data.get("timestamp") or "").strip()
        gcq_received_at = str(gcq_data.get("received_at") or "").strip()
        if gcq_shot_key and gcq_received_at:
            gcq_key = f"{gcq_shot_key}|{gcq_received_at}"
        else:
            gcq_key = gcq_shot_key or gcq_received_at

        nx_key = str(
            nx_data.get("shotdb_name")
            or nx_data.get("shotdb_filename")
            or nx_data.get("shotdb_id")
            or nx_data.get("shot_id")
            or ""
        )
        return gcq_key, nx_key

    def get_source_data_merge_key(self, source_system, data):
        if source_system == SOURCE_SYSTEM_GCQUAD:
            gcq_key, _ = self.get_shot_merge_key(data or {}, {})
            return gcq_key
        if source_system == SOURCE_SYSTEM_NX:
            _, nx_key = self.get_shot_merge_key({}, data or {})
            return nx_key
        return ""

    def is_source_merge_key_used(self, source_system, data):
        source_key = self.get_source_data_merge_key(source_system, data)
        if not source_key:
            return False
        if source_system == SOURCE_SYSTEM_GCQUAD:
            return source_key in self.saved_gcq_merge_keys
        if source_system == SOURCE_SYSTEM_NX:
            return source_key in self.saved_nx_merge_keys
        return False

    def get_recent_merge_pair(self, source_system):
        source_data = self.latest_source_data.get(source_system) or {}
        if not source_data:
            return
        if self.is_source_merge_key_used(source_system, source_data):
            return

        counterpart = self.get_counterpart_source(source_system)
        source_received_at = self.last_source_received_at.get(source_system)
        counterpart_received_at = self.last_source_received_at.get(counterpart)
        counterpart_data = self.latest_source_data.get(counterpart) or {}
        timeout_seconds = self.get_counterpart_receive_timeout_seconds()

        include_counterpart = (
            counterpart_data
            and source_received_at is not None
            and counterpart_received_at is not None
            and abs(source_received_at - counterpart_received_at) <= timeout_seconds
            and not self.is_source_merge_key_used(counterpart, counterpart_data)
        )

        if source_system == SOURCE_SYSTEM_GCQUAD:
            return source_data, counterpart_data if include_counterpart else {}
        if source_system == SOURCE_SYSTEM_NX:
            return counterpart_data if include_counterpart else {}, source_data

        return

    def write_shot_merge(self, gcq_data, nx_data):
        merge_key = self.get_shot_merge_key(gcq_data, nx_data)
        if not any(merge_key):
            return False
        if merge_key in self.saved_merge_keys:
            return False
        gcq_key, nx_key = merge_key
        if gcq_key and gcq_key in self.saved_gcq_merge_keys:
            return False
        if nx_key and nx_key in self.saved_nx_merge_keys:
            return False

        try:
            merge_csv_path = append_shot_merge_csv(gcq_data, nx_data)
        except Exception as exc:
            self.enqueue_log(f"[MERGE] Failed to append DateMerge CSV: {exc}")
            return False

        self.saved_merge_keys.add(merge_key)
        if gcq_key:
            self.saved_gcq_merge_keys.add(gcq_key)
        if nx_key:
            self.saved_nx_merge_keys.add(nx_key)
        self.enqueue_log(f"[MERGE] Saved shot merge CSV: {merge_csv_path}")

        try:
            grc_path = copy_nx_grc_to_merge_dir(nx_data)
        except Exception as exc:
            self.enqueue_log(f"[MERGE] Failed to copy NX+ grc file: {exc}")
            return True

        if grc_path is not None:
            self.enqueue_log(f"[MERGE] Copied NX+ grc file: {grc_path}")
            try:
                decrypt_dir, cli_output = run_grc_decrypt_cli(grc_path)
            except Exception as exc:
                self.enqueue_log(f"[MERGE] Failed to decrypt NX+ grc file: {exc}")
            else:
                self.enqueue_log(f"[MERGE] Decrypted NX+ grc file to: {decrypt_dir}")
                if cli_output:
                    self.enqueue_log(f"[MERGE] grc-decrypt-cli: {cli_output}")

        return True

    def get_source_merge_key(self, source_system):
        if source_system == SOURCE_SYSTEM_GCQUAD:
            gcq_key, _ = self.get_shot_merge_key(self.latest_source_data.get(source_system) or {}, {})
            return gcq_key
        if source_system == SOURCE_SYSTEM_NX:
            _, nx_key = self.get_shot_merge_key({}, self.latest_source_data.get(source_system) or {})
            return nx_key
        return ""

    def schedule_single_shot_merge(self, source_system):
        source_data = (self.latest_source_data.get(source_system) or {}).copy()
        if source_system == SOURCE_SYSTEM_GCQUAD:
            source_key, _ = self.get_shot_merge_key(source_data, {})
        elif source_system == SOURCE_SYSTEM_NX:
            _, source_key = self.get_shot_merge_key({}, source_data)
        else:
            return

        source_received_at = self.last_source_received_at.get(source_system)
        delay_ms = int(self.get_counterpart_receive_timeout_seconds() * 1000)
        self.root.after(
            delay_ms,
            lambda: self.save_single_shot_merge_if_still_missing(
                source_system,
                source_data,
                source_key,
                source_received_at,
            ),
        )

    def save_single_shot_merge_if_still_missing(self, source_system, source_data, source_key, source_received_at):
        if self.merge_require_both_var.get():
            return
        if not source_key:
            return
        if source_received_at is None:
            return

        counterpart = self.get_counterpart_source(source_system)
        counterpart_received_at = self.last_source_received_at.get(counterpart)
        timeout_seconds = self.get_counterpart_receive_timeout_seconds()
        if (
            counterpart_received_at is not None
            and counterpart_received_at >= source_received_at
            and abs(counterpart_received_at - source_received_at) <= timeout_seconds
        ):
            return

        if source_system == SOURCE_SYSTEM_GCQUAD:
            gcq_data, nx_data = source_data, {}
        elif source_system == SOURCE_SYSTEM_NX:
            gcq_data, nx_data = {}, source_data
        else:
            return
        self.write_shot_merge(gcq_data, nx_data)

    def save_shot_merge_if_ready(self, source_system):
        gcq_data, nx_data = self.get_recent_merge_pair(source_system) or ({}, {})

        if gcq_data and nx_data:
            self.write_shot_merge(gcq_data, nx_data)
            return

        if self.merge_require_both_var.get():
            return

        self.schedule_single_shot_merge(source_system)

    def apply_latest_data(self, data, record_receive=True):
        signature = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
        is_duplicate_signature = signature == self.last_data_signature

        if is_duplicate_signature and not record_receive:
            self.viewer_status_var.set("Status: SSE connected")
            return

        source_system = self.infer_source_system(data)
        if source_system in self.latest_source_data:
            self.latest_source_data[source_system] = data.copy()
            if record_receive:
                self.track_source_receive_status(source_system)
                self.save_shot_merge_if_ready(source_system)

        self.refresh_compare_table()
        self.last_data_signature = signature
        self.viewer_status_var.set(f"Status: {source_system} shot updated")

    def on_close(self):
        try:
            self.save_server_settings()
            self.save_viewer_settings()
        finally:
            self.stop_nx_monitor()
            self.disconnect_sse()
            self.stop_server()
            self.root.destroy()


def main():
    root = tk.Tk()
    ServerViewerTabsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
