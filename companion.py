"""
Marbles on Teams — Companion  v0.5.4.0
Watches the Marbles on Stream save folder and sends results to the MoT server.
Requires: requests
"""
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tkinter import filedialog
from urllib.parse import parse_qs, urlencode, urlparse
import tkinter as tk

import requests

# ── Configuration ─────────────────────────────────────────────────────────────

VERSION        = "0.5.4.0"
_BASE          = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
_EXE_DIR       = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

OBS_FILES = {
    "total_pts":     "MoT_TotalPoints.txt",
    "pts_today":     "MoT_PointsToday.txt",
    "world_records": "MoT_WorldRecords.txt",
    "avg_ppr":       "MoT_AvgPointsPerRace.txt",
}
OAUTH_PORT     = 17243
POLL_INTERVAL    = 2.0       # seconds between file checks
RETRY_INTERVAL   = 20        # seconds between server connection retries

SERVER_URL = "https://marblesonteams.app"

DEFAULT_SAVE_PATH = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "MarblesOnStream" / "Saved" / "SaveGames"
)
CONFIG_PATH = (
    Path(os.environ.get("APPDATA", ""))
    / "MarblesOnTeams" / "config.json"
)

WATCHED = ["LastSeasonRace.csv", "LastSeasonRoyale.csv", "LastCustomRaceMapPlayed.csv"]

# ── Persistent config ─────────────────────────────────────────────────────────

def load_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}

def save_cfg(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

# ── Twitch OAuth ──────────────────────────────────────────────────────────────

class _OAuthHandler(BaseHTTPRequestHandler):
    token: str | None = None

    def do_GET(self):
        if self.path.startswith("/callback"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<script>var p=new URLSearchParams(location.hash.slice(1));"
                b"location='/token?'+p;</script>"
            )
        elif self.path.startswith("/token"):
            params = parse_qs(urlparse(self.path).query)
            _OAuthHandler.token = (params.get("access_token") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2 style='font-family:sans-serif'>Logged in! You can close this tab.</h2>")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *_): pass


def _fetch_client_id(server_url: str) -> str:
    """Fetch the Twitch client ID from the MoT server config endpoint."""
    try:
        r = requests.get(server_url.rstrip("/") + "/config", timeout=5)
        return r.json().get("twitch_client_id", "")
    except Exception:
        return ""


def twitch_login(client_id: str) -> tuple[str, str] | None:
    """Opens Twitch OAuth and returns (login, display_name) or None."""
    if not client_id:
        return None
    _OAuthHandler.token = None
    url = "https://id.twitch.tv/oauth2/authorize?" + urlencode({
        "client_id":     client_id,
        "redirect_uri":  f"http://localhost:{OAUTH_PORT}/callback",
        "response_type": "token",
        "scope":         "",
    })
    srv = HTTPServer(("localhost", OAUTH_PORT), _OAuthHandler)
    webbrowser.open(url)
    srv.handle_request()
    srv.handle_request()

    token = _OAuthHandler.token
    if not token:
        return None
    try:
        resp = requests.get(
            "https://api.twitch.tv/helix/users",
            headers={"Authorization": f"Bearer {token}", "Client-Id": client_id},
            timeout=10,
        )
        d = resp.json()["data"][0]
        return d["login"], d["display_name"]
    except Exception:
        return None


# ── File watcher ──────────────────────────────────────────────────────────────

class Watcher(threading.Thread):
    def __init__(self, folder: Path, server_url: str, username: str, on_event):
        super().__init__(daemon=True)
        self.folder     = folder
        self.server_url = server_url.rstrip("/")
        self.username   = username
        self.on_event   = on_event
        self._stop      = threading.Event()
        self._seen: dict[str, str] = {}

    def stop(self): self._stop.set()

    def _snapshot(self):
        """Record current fingerprints so pre-existing files are not sent on startup."""
        for filename in WATCHED:
            path = self.folder / filename
            try:
                stat = path.stat()
                self._seen[filename] = f"{stat.st_mtime:.3f}:{stat.st_size}"
            except FileNotFoundError:
                pass

    def _file_status(self) -> dict:
        """Which watched files currently exist — reported to the server so the
        admin diagnostics panel can show save-file presence per streamer."""
        out = {}
        for filename in WATCHED:
            try:
                out[filename] = (self.folder / filename).exists()
            except Exception:
                out[filename] = False
        return out

    def _heartbeat(self):
        try:
            try:
                folder_exists = self.folder.exists()
            except Exception:
                folder_exists = False
            requests.post(
                self.server_url + "/ingest/ping",
                json={
                    "streamer_username": self.username,
                    "folder_exists":     folder_exists,
                    "files":             self._file_status(),
                },
                timeout=5,
            )
        except Exception:
            pass

    def _check_files(self):
        """One-shot save-folder/file diagnostic posted to the activity log, so a
        wrong folder (the usual cause of 'running but never sends') is visible."""
        try:
            exists = self.folder.exists()
        except Exception:
            exists = False
        if not exists:
            self.on_event(f"⚠ Save folder not found: {self.folder}", "error")
            self.on_event("   Click Browse and select your MarblesOnStream 'SaveGames' folder.", "error")
            return
        found  = self._file_status()
        race   = found.get("LastSeasonRace.csv")
        royale = found.get("LastSeasonRoyale.csv")
        if not race and not royale:
            self.on_event("⚠ No race/royale result files in this folder yet.", "error")
            self.on_event("   If you've already run races, this is the wrong folder "
                          "(or they're in a sub-folder) — click Browse.", "error")
        else:
            present = [f for f, ok in found.items() if ok]
            self.on_event("Save folder OK — found: " + ", ".join(present), "success")

    def run(self):
        self._snapshot()
        self._check_files()
        self._ensure_obs_files()
        self._heartbeat()
        self._fetch_obs_stats()
        _ping_counter = 0
        while not self._stop.wait(POLL_INTERVAL):
            _ping_counter += 1
            if _ping_counter >= 15:
                _ping_counter = 0
                self._heartbeat()
            changed = {}
            for filename in WATCHED:
                path = self.folder / filename
                try:
                    stat = path.stat()
                    fp   = f"{stat.st_mtime:.3f}:{stat.st_size}"
                    if self._seen.get(filename) == fp:
                        continue
                    self._seen[filename] = fp
                    changed[filename] = path.read_text(encoding="utf-8", errors="replace")
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    self.on_event(f"Read error {filename}: {exc}", "error")

            if "LastSeasonRace.csv" in changed:
                map_csv = changed.get("LastCustomRaceMapPlayed.csv", "")
                self._post_race(changed["LastSeasonRace.csv"], map_csv)
            elif "LastSeasonRoyale.csv" in changed:
                self._post_royale(changed["LastSeasonRoyale.csv"])

    def _ensure_obs_files(self):
        """Create OBS txt files with default value 0 if they don't exist yet."""
        for filename in OBS_FILES.values():
            p = _EXE_DIR / filename
            if not p.exists():
                try:
                    p.write_text("0", encoding="utf-8")
                except Exception:
                    pass

    def _write_obs_stats(self, stats: dict):
        for key, filename in OBS_FILES.items():
            try:
                (_EXE_DIR / filename).write_text(str(stats.get(key, 0)), encoding="utf-8")
            except Exception:
                pass

    def _fetch_obs_stats(self):
        import time as _time
        try:
            r = requests.get(
                self.server_url + f"/leaderboard/streamers/{self.username}/obs-stats",
                params={"_t": int(_time.time())},
                timeout=10,
            )
            if r.ok:
                self._write_obs_stats(r.json())
            else:
                self.on_event(f"Stats fetch failed ({r.status_code})", "error")
        except Exception as exc:
            self.on_event(f"Stats fetch error: {exc}", "error")

    def _post_race(self, content: str, map_csv: str = ""):
        payload = {
            "streamer_username": self.username,
            "csv_content":       content,
            "map_csv_content":   map_csv,
        }
        try:
            resp = requests.post(self.server_url + "/ingest/race", json=payload, timeout=15)
            try:
                data = resp.json()
            except Exception:
                data = {}
            if resp.status_code == 409:
                self.on_event("Race not saved — Twitch shows you offline. Results only save while you're live.", "error")
                return
            if not resp.ok:
                self.on_event(f"Race ingest error ({resp.status_code}): {data.get('detail') or 'server error'}", "error")
                return
            map_label = f' on "{data["map_name"]}"' if data.get("map_name") else ""
            self.on_event(f"Race saved{map_label} — {data['entries_saved']} players", "success")
            if data.get("new_world_record"):
                self.on_event(f"  🏅 NEW WORLD RECORD — {data['wr_holder']}!", "wr")
            threading.Thread(target=self._fetch_obs_stats, daemon=True).start()
        except requests.ConnectionError:
            self.on_event("Cannot reach server.", "error")
        except Exception as exc:
            self.on_event(f"POST failed: {exc}", "error")

    def _post_royale(self, content: str):
        payload = {"streamer_username": self.username, "csv_content": content}
        try:
            resp = requests.post(self.server_url + "/ingest/royale", json=payload, timeout=15)
            try:
                data = resp.json()
            except Exception:
                data = {}
            if resp.status_code == 409:
                self.on_event("Royale not saved — Twitch shows you offline. Results only save while you're live.", "error")
                return
            if not resp.ok:
                self.on_event(f"Royale ingest error ({resp.status_code}): {data.get('detail') or 'server error'}", "error")
                return
            self.on_event(f"Royale saved — {data['entries_saved']} players", "success")
            threading.Thread(target=self._fetch_obs_stats, daemon=True).start()
        except requests.ConnectionError:
            self.on_event("Cannot reach server.", "error")
        except Exception as exc:
            self.on_event(f"POST failed: {exc}", "error")

# ── GUI ───────────────────────────────────────────────────────────────────────

BG      = "#0d0d0d"
SURFACE = "#181818"
BORDER  = "#2a2a2a"
ACCENT  = "#f5a623"
GREEN   = "#4caf7d"
RED     = "#e05555"
BLUE    = "#5b9cf6"
GOLD    = "#ffd700"
MUTED   = "#666666"
TEXT    = "#e2e2e2"
FONT    = ("Segoe UI", 9)
FONT_B  = ("Segoe UI", 9, "bold")
FONT_SM = ("Segoe UI", 8)
MONO    = ("Consolas", 8)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg    = load_cfg()
        self.watcher: Watcher | None = None
        self._retry_job = None
        self._chat_poll_job = None
        self._chat_tick_job = None
        self._chat_remaining = 0
        self._was_connected = None
        self._reconnect_remaining = 0

        self.title(f"Marbles on Teams  v{VERSION}")
        self.resizable(False, False)
        self.configure(bg=BG)
        try:
            self.iconbitmap(str(_BASE / "logo.ico"))
        except Exception:
            pass
        self._build_ui()
        self._apply_saved_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=SURFACE, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Marbles on Teams", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=12)
        self._ver_lbl = tk.Label(hdr, text=f"v{VERSION}", bg=SURFACE, fg=MUTED,
                                 font=FONT_SM)
        self._ver_lbl.pack(side="left")
        self._status_dot = tk.Label(hdr, text="●", bg=SURFACE, fg=MUTED, font=FONT_B)
        self._status_dot.pack(side="right", padx=(4, 12))
        self._status_lbl = tk.Label(hdr, text="Connecting…", bg=SURFACE, fg=MUTED,
                                    font=FONT_SM)
        self._status_lbl.pack(side="right")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", padx=12, pady=8)

        # Account
        self._section(body, "Twitch Account")
        acc_row = tk.Frame(body, bg=BG)
        acc_row.pack(fill="x", pady=(0, 8))
        self._account_lbl = tk.Label(acc_row, text="Not logged in", bg=BG,
                                     fg=MUTED, font=FONT)
        self._account_lbl.pack(side="left")
        self._login_btn = tk.Button(acc_row, text="Login with Twitch",
                                    bg=ACCENT, fg="#111", font=FONT_B,
                                    relief="flat", bd=0, padx=10,
                                    command=self._do_login)
        self._login_btn.pack(side="right")

        # Save folder
        self._section(body, "Save Folder")
        folder_row = tk.Frame(body, bg=BG)
        folder_row.pack(fill="x", pady=(0, 8))
        self._folder_lbl = tk.Label(folder_row, text="Detecting…", bg=BG,
                                    fg=MUTED, font=FONT_SM, anchor="w")
        self._folder_lbl.pack(side="left", fill="x", expand=True)
        tk.Button(folder_row, text="Browse", bg=SURFACE, fg=TEXT,
                  font=FONT_SM, relief="flat", bd=0, padx=8,
                  activebackground=BORDER,
                  command=self._browse_folder).pack(side="right")

        # Watcher status
        self._watch_lbl = tk.Label(body, text="", bg=BG, fg=MUTED, font=FONT_SM)
        self._watch_lbl.pack(anchor="w")
        self._chat_lbl = tk.Label(body, text="", bg=BG, fg=MUTED, font=FONT_SM)
        self._chat_lbl.pack(anchor="w", pady=(0, 4))

        # OBS text files
        self._section(body, "OBS Text Files")
        obs_row = tk.Frame(body, bg=BG)
        obs_row.pack(fill="x", pady=(0, 4))
        self._obs_lbl = tk.Label(obs_row, text=str(_EXE_DIR), bg=BG, fg=MUTED,
                                 font=FONT_SM, anchor="w")
        self._obs_lbl.pack(side="left", fill="x", expand=True)
        tk.Button(obs_row, text="Open Folder", bg=SURFACE, fg=TEXT,
                  font=FONT_SM, relief="flat", bd=0, padx=8,
                  activebackground=BORDER,
                  command=lambda: os.startfile(str(_EXE_DIR))).pack(side="right")
        tk.Label(body,
                 text="MoT_TotalPoints.txt  •  MoT_PointsToday.txt  •  MoT_WorldRecords.txt  •  MoT_AvgPointsPerRace.txt",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), wraplength=390, justify="left",
                 ).pack(anchor="w", pady=(0, 6))

        # Warning banners — hidden by default, shown via grid() when needed
        _wf = tk.Frame(body, bg=BG)
        _wf.pack(fill="x")
        _wf.columnconfigure(0, weight=1)
        self._token_warn = tk.Label(_wf, text="", bg="#2a1500", fg=ACCENT,
                                    font=FONT_SM, anchor="w", cursor="hand2",
                                    wraplength=380, justify="left", padx=6, pady=4)
        self._token_warn.bind("<Button-1>", lambda e: self._open_dashboard())
        self._update_warn = tk.Label(_wf, text="", bg="#0a1a2a", fg=BLUE,
                                     font=FONT_SM, anchor="w", cursor="hand2",
                                     wraplength=380, justify="left", padx=6, pady=4)
        self._update_warn.bind("<Button-1>", lambda e: webbrowser.open(
            self.cfg.get("server_url", SERVER_URL).rstrip("/") + "/companion/download/exe"))

        # Activity log
        self._section(body, "Activity")
        self._log = tk.Text(body, height=14, bg=SURFACE, fg=MUTED, font=MONO,
                            relief="flat", state="disabled", wrap="word",
                            bd=4, highlightthickness=0)
        self._log.pack(fill="x", pady=(0, 8))
        for tag, color in [("success", GREEN), ("error", RED), ("info", BLUE),
                            ("result", TEXT), ("wr", GOLD)]:
            self._log.tag_config(tag, foreground=color)

        # Footer
        foot = tk.Frame(body, bg=BG)
        foot.pack(fill="x")
        tk.Button(foot, text="Open Dashboard", bg=SURFACE, fg=TEXT,
                  font=FONT, relief="flat", bd=0, padx=12, pady=4,
                  activebackground=BORDER,
                  command=self._open_dashboard).pack(side="left")

        self.geometry("420x560")

    def _section(self, parent, label: str):
        tk.Label(parent, text=label.upper(), bg=BG, fg=MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(4, 2))

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log_msg(self, msg: str, level: str = ""):
        self._log.configure(state="normal")
        tag = level if level in ("success", "error", "info", "result", "wr") else ""
        self._log.insert("1.0", msg + "\n", tag)
        lines = int(self._log.index("end-1c").split(".")[0])
        if lines > 300:
            self._log.delete("300.0", "end")
        self._log.configure(state="disabled")

    # ── Connection ────────────────────────────────────────────────────────────

    def _try_connect(self):
        self._cancel_reconnect()
        server = self.cfg.get("server_url", SERVER_URL)
        try:
            r = requests.get(server.rstrip("/") + "/admin/season/active", timeout=5)
            data = r.json()
            season = data.get("season")
            label  = season["name"] if season else "No active season"
            self._set_status(True, label)
            self._clear_status_click()
            if self._was_connected is False:
                self._log_msg("Reconnected to server.", "success")
            self._was_connected = True
        except Exception:
            if self._was_connected is not False:   # first failure after being connected
                self._log_msg(
                    f"⚠ Can't reach server — auto-retrying every {RETRY_INTERVAL}s. "
                    "Click the status indicator (top-right) to retry now.", "error")
            self._was_connected = False
            self._begin_reconnect_countdown(RETRY_INTERVAL)

    def _begin_reconnect_countdown(self, seconds: int):
        self._reconnect_remaining = seconds
        for w in (self._status_lbl, self._status_dot):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self._try_connect())
        self._tick_reconnect()

    def _tick_reconnect(self):
        n = self._reconnect_remaining
        if n <= 0:
            self._try_connect()        # time's up — attempt now (reschedules on failure)
            return
        self._set_status(False, f"Reconnecting… {n}s")
        self._reconnect_remaining = n - 1
        self._retry_job = self.after(1000, self._tick_reconnect)

    def _cancel_reconnect(self):
        if self._retry_job:
            self.after_cancel(self._retry_job)
            self._retry_job = None

    def _clear_status_click(self):
        for w in (self._status_lbl, self._status_dot):
            w.configure(cursor="")
            w.unbind("<Button-1>")

    def _set_status(self, ok: bool, label: str):
        self._status_dot.configure(fg=GREEN if ok else RED)
        self._status_lbl.configure(fg=(GREEN if ok else RED), text=label)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _do_login(self):
        self._login_btn.configure(state="disabled", text="Opening browser…")
        self.update()
        server = self.cfg.get("server_url", SERVER_URL)
        client_id = _fetch_client_id(server)
        if not client_id:
            self._log_msg("Cannot reach server to get config. Is the server running?", "error")
            self._login_btn.configure(state="normal", text="Login with Twitch")
            return
        result = twitch_login(client_id)
        self._login_btn.configure(state="normal", text="Login with Twitch")
        if result:
            login, display = result
            self.cfg.update({"twitch_login": login, "twitch_display": display})
            save_cfg(self.cfg)
            self._account_lbl.configure(text=display, fg=GREEN)
            self._login_btn.configure(text="Switch Account")
            self._log_msg(f"Logged in as {display}", "success")
            self._start_watcher()
        else:
            self._log_msg("Login failed or was cancelled.", "error")

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select MarblesOnStream save folder")
        if path:
            self.cfg["save_folder"] = path
            save_cfg(self.cfg)
            self._folder_lbl.configure(text=path, fg=TEXT)
            self._restart_watcher()

    def _open_dashboard(self):
        webbrowser.open(self.cfg.get("server_url", SERVER_URL))

    # ── Watcher lifecycle ─────────────────────────────────────────────────────

    def _check_token_linked(self):
        username = self.cfg.get("twitch_login")
        server   = self.cfg.get("server_url", SERVER_URL)
        if not username or not server:
            return
        try:
            r = requests.get(
                server.rstrip("/") + f"/streamer/settings/{username}",
                timeout=5,
            )
            data = r.json()
            linked = data.get("token_linked", False)
            self.after(0, self._set_token_warn, not linked)
            if not linked:
                self.after(0, self._log_msg,
                           "⚠ Chat/IRC features not active — link your broadcaster account on the dashboard (Config → Chat Settings).",
                           "error")
        except Exception:
            pass

    def _set_token_warn(self, show: bool):
        if show:
            self._token_warn.configure(
                text="⚠ Broadcaster account not linked — race results & chat won't post. Click here to open Dashboard →"
            )
            self._token_warn.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        else:
            self._token_warn.grid_remove()

    def _check_for_update(self):
        try:
            server = self.cfg.get("server_url", SERVER_URL)
            r = requests.get(server.rstrip("/") + "/companion/version", timeout=5)
            latest = r.json().get("version", "")
            if latest and latest != VERSION:
                self._update_warn.configure(text=f"↑ Update available (v{latest}) — click to download")
                self.after(0, lambda: self._update_warn.grid(row=1, column=0, sticky="ew", pady=(0, 4)))
        except Exception:
            pass

    def _start_watcher(self):
        username = self.cfg.get("twitch_login")
        folder   = Path(self.cfg.get("save_folder", DEFAULT_SAVE_PATH))
        server   = self.cfg.get("server_url", SERVER_URL)
        if not username or not server:
            return
        if self.watcher:
            self.watcher.stop()
        self.watcher = Watcher(folder, server, username,
                               lambda m, l: self.after(0, self._log_msg, m, l))
        self.watcher.start()
        self._watch_lbl.configure(text=f"● Watching {folder.name}", fg=GREEN)
        self._log_msg(f"Watching {folder}", "info")
        threading.Thread(target=self._check_token_linked, daemon=True).start()
        self._start_chat_monitor()

    # ── Chat / IRC status with a live reconnect countdown ─────────────────────

    def _cancel_chat_jobs(self):
        for attr in ("_chat_poll_job", "_chat_tick_job"):
            job = getattr(self, attr, None)
            if job:
                self.after_cancel(job)
                setattr(self, attr, None)

    def _start_chat_monitor(self):
        self._cancel_chat_jobs()
        self._chat_check_now()

    def _chat_check_now(self):
        self._chat_poll_job = None
        threading.Thread(target=self._fetch_chat_status, daemon=True).start()

    def _fetch_chat_status(self):
        username = self.cfg.get("twitch_login")
        server   = self.cfg.get("server_url", SERVER_URL)
        if not username or not server:
            return
        active = False
        try:
            r = requests.get(server.rstrip("/") + f"/ingest/ping/{username}", timeout=5)
            active = r.json().get("chat_active", False)
        except Exception:
            pass
        self.after(0, self._update_chat_lbl, active)

    def _update_chat_lbl(self, active: bool):
        self._cancel_chat_jobs()
        if active:
            self._chat_lbl.configure(text="● Chat: Active", fg=GREEN, cursor="")
            self._chat_lbl.unbind("<Button-1>")
            # routine re-check so a drop is noticed
            self._chat_poll_job = self.after(30_000, self._chat_check_now)
        else:
            self._chat_lbl.configure(cursor="hand2")
            self._chat_lbl.bind("<Button-1>", lambda e: self._chat_reconnect_now())
            self._chat_remaining = 30
            self._tick_chat()

    def _tick_chat(self):
        n = self._chat_remaining
        if n <= 0:
            self._chat_check_now()      # time's up — re-check (auto-reconnects server-side)
            return
        self._chat_lbl.configure(
            text=f"● Chat: reconnecting… {n}s — click here to reconnect now", fg=ACCENT)
        self._chat_remaining = n - 1
        self._chat_tick_job = self.after(1000, self._tick_chat)

    def _chat_reconnect_now(self):
        """Force the server to (re)start the IRC listener for this channel, then re-check."""
        self._cancel_chat_jobs()
        self._chat_lbl.configure(text="● Chat: reconnecting…", fg=ACCENT, cursor="")
        def _do():
            username = self.cfg.get("twitch_login")
            server   = self.cfg.get("server_url", SERVER_URL)
            if not username or not server:
                return
            try:
                requests.post(server.rstrip("/") + "/ingest/ping",
                              json={"streamer_username": username}, timeout=5)
            except Exception:
                pass
            self._fetch_chat_status()
        threading.Thread(target=_do, daemon=True).start()

    def _restart_watcher(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self._start_watcher()

    # ── Startup / shutdown ────────────────────────────────────────────────────

    def _apply_saved_state(self):
        folder = self.cfg.get("save_folder")
        if not folder and DEFAULT_SAVE_PATH.exists():
            folder = str(DEFAULT_SAVE_PATH)
            self.cfg["save_folder"] = folder
        if folder:
            self._folder_lbl.configure(text=folder, fg=TEXT)

        display = self.cfg.get("twitch_display")
        if display:
            self._account_lbl.configure(text=display, fg=GREEN)
            self._login_btn.configure(text="Switch Account")

        self.after(200, self._try_connect)
        threading.Thread(target=self._check_for_update, daemon=True).start()

        if self.cfg.get("twitch_login") and folder:
            self.after(500, self._start_watcher)

    def _on_close(self):
        if self.watcher:
            self.watcher.stop()
        if self._retry_job:
            self.after_cancel(self._retry_job)
        save_cfg(self.cfg)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
