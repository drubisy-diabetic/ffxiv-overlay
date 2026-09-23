"""Pre-pull detection, persistence and UI for the FFXIV overlay.

How a pre-pull is identified: COUNTDOWN + ENMITY
------------------------------------------------
A pre-pull is "someone engaged the enemy before the countdown reached zero".

1. The COUNTDOWN opens the window. ``268`` (Countdown) log line:
   countdown_end = line time + N s. ``269`` (CountdownCancel) or a zone change
   (``01``) closes it. A countdown started while already in combat (``260``)
   is ignored. At the moment the countdown starts, every enemy that ALREADY
   has an enmity table (e.g. a dummy still being hit) is recorded as a
   baseline and ignored.

2. ENMITY decides who pulled. A second websocket (OverlayPlugin ``/ws``)
   subscribes to ``EnmityTargetData`` (the enmity table of your current
   target: every player's Enmity/HateRate on it) and ``EnmityAggroList``
   (enemies engaged with the party and who each one is targeting). The
   first new enemy to show a non-empty enmity table while the window is
   open = the pull; the player with the TOP enmity on it at that moment is
   the puller. A pet's enmity is credited to its owner (OwnerID).

3. Log-line actions are supporting evidence: a ``21``/``22`` ability from a
   player onto a non-pet NPC (damage, provoke, stun...), or a damaging hit
   from a non-pet NPC onto a player (body pull). They give the precise
   timestamp and the ability name, and act as a fallback when enmity data
   isn't available (you weren't targeting the boss, or /ws isn't reachable).
   Self-buffs, heals, potions and untargeted AoEs never count.

4. Whichever evidence arrives first starts a short confirmation window
   (``confirm_window`` s) so the other can arrive too - the two come over
   different sockets. Engagement time = the earliest evidence; puller = the
   enmity table's top entry when present, else the action's actor.

5. Pre-pull if engagement time < countdown_end - ``tolerance`` (covers a
   caster's pre-cast landing on "0"). Unused windows expire ``grace`` s
   after zero.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Log line helpers
# ---------------------------------------------------------------------------

JOB_ID_TO_ABBR = {
    1: "gla", 2: "pgl", 3: "mrd", 4: "lnc", 5: "arc", 6: "cnj", 7: "thm",
    19: "pld", 20: "mnk", 21: "war", 22: "drg", 23: "brd", 24: "whm",
    25: "blm", 26: "acn", 27: "smn", 28: "sch", 29: "rog", 30: "nin",
    31: "mch", 32: "drk", 33: "ast", 34: "sam", 35: "rdm", 36: "blu",
    37: "gnb", 38: "dnc", 39: "rpr", 40: "sge", 41: "vpr", 42: "pct",
}

# Low byte of the first effect flag in a 21/22 line.
EFFECT_HEAL = 0x04
HOSTILE_EFFECTS = {0x01, 0x03, 0x05, 0x06}  # miss, damage, blocked, parried

_TS_RE = re.compile(r"^(.*T\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$")


def parse_ts(text):
    """Parse ACT timestamps like 2026-09-23T20:30:58.1540000+02:00.

    ACT writes 7 fractional digits, which datetime.fromisoformat() rejects on
    older Pythons, so the fraction is normalised to microseconds first.
    """
    m = _TS_RE.match(text.strip())
    if not m:
        raise ValueError(f"bad timestamp: {text!r}")
    base, frac, tz = m.groups()
    frac = (frac or "0")[:6].ljust(6, "0")
    tz = "+00:00" if tz in (None, "Z") else tz
    return datetime.fromisoformat(f"{base}.{frac}{tz}")


def _is_player(actor_id):
    return len(actor_id) == 8 and actor_id.startswith("10")


def _is_npc(actor_id):
    return len(actor_id) == 8 and actor_id.startswith("40")


def _effect_type(flags):
    try:
        return int(flags, 16) & 0xFF
    except (TypeError, ValueError):
        return None


def extract_log_line(message):
    """Return the raw ``code|timestamp|...`` log line from a websocket message.

    Handles both the legacy MiniParse broadcast (what this overlay uses) and
    the newer OverlayPlugin ``LogLine`` event, or None for anything else.
    """
    if not isinstance(message, dict):
        return None
    if message.get("msgtype") == "Chat" and isinstance(message.get("msg"), str):
        return message["msg"]
    if message.get("type") == "LogLine":
        if isinstance(message.get("rawLine"), str):
            return message["rawLine"]
        if isinstance(message.get("line"), list):
            return "|".join(str(p) for p in message["line"])
    return None


# ---------------------------------------------------------------------------
# Detector (pure logic, no Qt - runs in the websocket thread)
# ---------------------------------------------------------------------------

def _hex_id(value):
    """Enmity events carry decimal uint IDs; log lines use 8-digit hex."""
    try:
        return f"{int(value):08X}"
    except (TypeError, ValueError):
        return None


def _utcnow():
    return datetime.now(timezone.utc)


class PrePullDetector:
    """Pure logic, no Qt. Feed it log lines (feed_line), enmity websocket
    messages (feed_enmity) and call tick() a few times a second; each
    returns a pre-pull event dict or None. Not thread-safe: call it from
    one thread (the overlay runs everything on its asyncio thread)."""

    ENMITY_LIVE_SECONDS = 5.0   # enmity counts as "live" if a message arrived this recently

    def __init__(self, tolerance=1.0, grace=30.0, confirm_window=0.75):
        self.tolerance = float(tolerance)
        self.grace = float(grace)
        self.confirm_window = float(confirm_window)
        self.jobs = {}          # actor id (hex) -> job abbreviation
        self.names = {}         # actor id (hex) -> name
        self.owners = {}        # pet id (hex) -> owner id (hex)
        self.in_combat = False
        self.last_enmity_at = None
        self.engaged_target = {}   # enemy id -> True/False, from EnmityTargetData
        self.aggro_ids = set()     # enemy ids currently in EnmityAggroList
        self.last_pull = None      # (name, timestamp, seconds_early, evidence) for debugging
        self._clear_countdown()

    # ----- state helpers -------------------------------------------------
    def _clear_countdown(self):
        self.countdown_end = None
        self.countdown_by = None
        self.baseline = set()
        self.pending = None

    def enmity_live(self, now=None):
        if self.last_enmity_at is None:
            return False
        now = now or _utcnow()
        return (now - self.last_enmity_at).total_seconds() < self.ENMITY_LIVE_SECONDS

    def _engaged_enemies(self):
        return {i for i, engaged in self.engaged_target.items() if engaged} | self.aggro_ids

    def _window_open(self, now):
        if self.countdown_end is None:
            return False
        if now > self.countdown_end + timedelta(seconds=self.grace):
            self._clear_countdown()  # stale countdown, nobody pulled
            return False
        return True

    def _owner_of(self, actor_id, name):
        owner = self.owners.get(actor_id)
        if owner:
            return owner, self.names.get(owner, name)
        return actor_id, name

    # ----- evidence --------------------------------------------------------
    def _add_evidence(self, when, kind, actor_id=None, name=None, job=None, ability=None, now=None):
        """Record engagement evidence; returns an event if it can be decided now."""
        p = self.pending
        if p is None:
            p = self.pending = {"time": when, "kinds": set(), "enmity": None, "action": None}
        p["time"] = min(p["time"], when)
        p["kinds"].add(kind)
        info = {"id": actor_id, "name": name, "job": job, "ability": ability}
        if kind == "enmity" and p["enmity"] is None:
            p["enmity"] = info
        elif kind == "action" and p["action"] is None:
            p["action"] = info
        # Decide immediately when both kinds are in, or when enmity isn't
        # available at all (nothing to wait for).
        if {"enmity", "action"} <= p["kinds"] or (kind == "action" and not self.enmity_live(now or when)):
            return self._finalize()
        return None

    def _finalize(self):
        p = self.pending
        if p is None or self.countdown_end is None:
            self.pending = None
            return None
        who = p["enmity"] or p["action"]
        action = p["action"] or {}
        # Ability name only if the action came from the same person enmity blames.
        ability = action.get("ability") if (not p["enmity"] or action.get("id") == who["id"]) else None
        early_by = (self.countdown_end - p["time"]).total_seconds()
        countdown_by = self.countdown_by
        evidence = "+".join(sorted(p["kinds"]))
        self._clear_countdown()
        self.last_pull = (who["name"], p["time"], early_by, evidence)
        if early_by <= self.tolerance:
            return None  # clean pull
        return {
            "type": "prepull",
            "id": who["id"],
            "name": who["name"] or "Unknown",
            "job": who.get("job") or self.jobs.get(who["id"], "default"),
            "early_by": round(early_by, 1),
            "ability": ability,
            "evidence": evidence,
            "countdown_by": countdown_by,
            "timestamp": p["time"].isoformat(),
        }

    def tick(self, now=None):
        """Call periodically: decides a pending engagement once its
        confirmation window has passed, and expires stale countdowns."""
        now = now or _utcnow()
        if self.pending and (now - self.pending["time"]).total_seconds() >= self.confirm_window:
            return self._finalize()
        if self.pending is None:
            self._window_open(now)
        return None

    # ----- log lines -------------------------------------------------------
    def feed_line(self, line):
        """Feed one log line. Returns a pre-pull event dict or None."""
        if not isinstance(line, str):
            return None
        parts = line.split("|")
        if len(parts) < 3:
            return None
        code = parts[0]
        if code not in ("01", "03", "21", "22", "260", "268", "269"):
            return None
        try:
            ts = parse_ts(parts[1])
        except ValueError:
            return None

        if code == "01":                      # ChangeZone
            self._clear_countdown()
            self.in_combat = False
            self.engaged_target.clear()
            self.aggro_ids.clear()
        elif code == "03":                    # AddCombatant
            self._on_add_combatant(parts)
        elif code == "260":                   # InCombat
            if len(parts) > 3:
                self.in_combat = parts[3] == "1"
        elif code == "268":                   # Countdown
            self._on_countdown(parts, ts)
        elif code == "269":                   # CountdownCancel
            self._clear_countdown()
        else:                                 # 21 / 22 abilities
            return self._on_ability(parts, ts)
        return None

    def _on_add_combatant(self, parts):
        if len(parts) < 7:
            return
        actor_id = parts[2].upper()
        self.names[actor_id] = parts[3]
        try:
            job = JOB_ID_TO_ABBR.get(int(parts[4], 16))
        except ValueError:
            job = None
        if job:
            self.jobs[actor_id] = job
        owner = parts[6].strip("0")
        if owner:
            self.owners[actor_id] = parts[6].upper().rjust(8, "0")

    def _on_countdown(self, parts, ts):
        # 268|ts|id|worldId|countdownTime|result|name
        if len(parts) < 7 or self.in_combat:
            return
        if parts[5] not in ("00", "0", ""):
            return  # countdown failed to start
        try:
            seconds = float(parts[4])
        except ValueError:
            return
        self._clear_countdown()
        self.countdown_end = ts + timedelta(seconds=seconds)
        self.countdown_by = parts[6] or None
        self.baseline = self._engaged_enemies()

    def _on_ability(self, parts, ts):
        if len(parts) < 9 or not self._window_open(ts):
            return None
        src, src_name = parts[2].upper(), parts[3]
        tgt, tgt_name = parts[6].upper(), parts[7]
        effect = _effect_type(parts[8])

        culprit = None
        if _is_player(src) and _is_npc(tgt) and tgt not in self.owners \
                and tgt not in self.baseline and effect != EFFECT_HEAL:
            culprit = (src, src_name)
        elif _is_npc(src) and src not in self.owners and src not in self.baseline \
                and _is_player(tgt) and effect in HOSTILE_EFFECTS:
            culprit = (tgt, tgt_name)
        if culprit is None:
            return None
        return self._add_evidence(ts, "action", culprit[0], culprit[1],
                                  self.jobs.get(culprit[0]), parts[5], now=ts)

    # ----- enmity ------------------------------------------------------------
    def feed_enmity(self, message, now=None):
        """Feed one OverlayPlugin EnmityTargetData / EnmityAggroList message."""
        if not isinstance(message, dict):
            return None
        now = now or _utcnow()
        kind = message.get("type")
        if kind == "EnmityTargetData":
            self.last_enmity_at = now
            return self._on_target_data(message, now)
        if kind == "EnmityAggroList":
            self.last_enmity_at = now
            return self._on_aggro_list(message, now)
        return None

    def _on_target_data(self, message, now):
        target = message.get("Target") or {}
        enemy = _hex_id(target.get("ID"))
        if not enemy or not _is_npc(enemy):
            return None
        entries = [e for e in (message.get("Entries") or [])
                   if isinstance(e, dict) and (e.get("Enmity") or 0) > 0]
        was_engaged = self.engaged_target.get(enemy, False)
        self.engaged_target[enemy] = bool(entries)
        if not entries or was_engaged or not self._window_open(now) or enemy in self.baseline:
            return None

        # Top enmity = the puller (HateRate 100). Pets credited to the owner.
        top = max(entries, key=lambda e: e.get("Enmity") or 0)
        top_id = _hex_id(top.get("ID"))
        name = top.get("Name")
        owner_id = _hex_id(top.get("OwnerID"))
        job = JOB_ID_TO_ABBR.get(top.get("Job") or 0)
        if owner_id and owner_id != "00000000":
            owner = next((e for e in entries if _hex_id(e.get("ID")) == owner_id), None)
            top_id = owner_id
            name = (owner or {}).get("Name") or self.names.get(owner_id, name)
            job = JOB_ID_TO_ABBR.get((owner or {}).get("Job") or 0) or self.jobs.get(owner_id)
        if not _is_player(top_id or ""):
            return None
        return self._add_evidence(now, "enmity", top_id, name, job, now=now)

    def _on_aggro_list(self, message, now):
        new_ids, first_new = set(), None
        for entry in message.get("AggroList") or []:
            if not isinstance(entry, dict):
                continue
            enemy = _hex_id(entry.get("ID"))
            if not enemy:
                continue
            new_ids.add(enemy)
            if first_new is None and enemy not in self.aggro_ids and enemy not in self.baseline:
                first_new = entry
        self.aggro_ids = new_ids
        if first_new is None or not self._window_open(now):
            return None
        target = first_new.get("Target") or {}
        who_id = _hex_id(target.get("ID"))
        if not who_id:
            return None
        who_id, name = self._owner_of(who_id, target.get("Name"))
        if not _is_player(who_id):
            return None
        job = JOB_ID_TO_ABBR.get(target.get("Job") or 0) or self.jobs.get(who_id)
        return self._add_evidence(now, "enmity", who_id, name, job, now=now)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _data_dir():
    # Next to the executable when frozen, otherwise the working directory
    # (same place websocket_logs.jsonl is written).
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(".")


class PrePullStore:
    def __init__(self, path=None):
        self.path = path or os.path.join(_data_dir(), "prepull_stats.json")
        self.players = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.players = json.load(f).get("players", {})
        except (OSError, json.JSONDecodeError, AttributeError):
            self.players = {}

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"players": self.players}, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"Could not save pre-pull stats: {e}")

    def add(self, event):
        entry = self.players.setdefault(
            event["name"], {"count": 0, "job": "default", "worst": 0.0, "last": None})
        entry["count"] += 1
        if event.get("job") and event["job"] != "default":
            entry["job"] = event["job"]
        entry["worst"] = max(entry.get("worst", 0.0), event.get("early_by", 0.0))
        entry["last"] = event.get("timestamp")
        self.save()
        return entry["count"]

    def reset(self):
        self.players = {}
        self.save()

    def ranking(self):
        return sorted(self.players.items(),
                      key=lambda kv: (-kv[1]["count"], kv[0].lower()))

    def total(self):
        return sum(p["count"] for p in self.players.values())


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

from PyQt6.QtCore import Qt, QTimer, QPoint  # noqa: E402
from PyQt6.QtGui import QPixmap, QColor, QGuiApplication  # noqa: E402
from PyQt6.QtWidgets import (QWidget, QFrame, QLabel, QVBoxLayout,  # noqa: E402
                             QHBoxLayout, QPushButton)

from player import JOB_NAME_MAP, JOB_COLORS, resource_path  # noqa: E402


def load_job_colors():
    colors = dict(JOB_COLORS)
    try:
        with open(resource_path("colors.json"), "r", encoding="utf-8") as f:
            colors.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass
    return colors


def job_icon(job, size=16):
    name = JOB_NAME_MAP.get(job, job)
    pix = QPixmap(resource_path(os.path.join("icons", f"{name}.png")))
    if pix.isNull():
        return None
    return pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


class PrePullPopup(QWidget):
    """Big warning banner, top-centre of the screen, click-through."""

    def __init__(self, seconds=3.0):
        super().__init__()
        self.seconds = seconds
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        frame = QFrame(self)
        frame.setObjectName("PopupFrame")
        frame.setStyleSheet("""
            #PopupFrame { background: rgba(25, 10, 10, 225);
                          border: 2px solid #ff4444; border-radius: 10px; }
            QLabel { background: transparent; border: none; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(24, 12, 24, 12)
        lay.setSpacing(2)

        self.title = QLabel("PRE-PULL!")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("color: #ff4444; font-size: 26px; font-weight: 900;")

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addStretch()
        self.icon = QLabel()
        self.icon.setFixedSize(22, 22)
        self.name = QLabel()
        self.name.setStyleSheet("font-size: 18px; font-weight: bold;")
        name_row.addWidget(self.icon)
        name_row.addWidget(self.name)
        name_row.addStretch()

        self.detail = QLabel()
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setStyleSheet("color: #ccc; font-size: 12px;")

        lay.addWidget(self.title)
        lay.addLayout(name_row)
        lay.addWidget(self.detail)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def show_event(self, event, count=None, colors=None, anchor=None):
        colors = colors or {}
        job = event.get("job", "default")
        self.name.setText(event["name"])
        self.name.setStyleSheet(
            f"font-size: 18px; font-weight: bold; "
            f"color: {colors.get(job, colors.get('default', '#ffffff'))};")
        pix = job_icon(job, 22)
        self.icon.setVisible(pix is not None)
        if pix is not None:
            self.icon.setPixmap(pix)

        detail = f"{event['early_by']:.1f}s early"
        if event.get("ability"):
            detail += f"  ·  {event['ability']}"
        if event.get("evidence") and "enmity" in event["evidence"]:
            detail += "  ·  enmity"
        if count:
            detail += f"  ·  #{count}"
        self.detail.setText(detail)

        self.adjustSize()
        screen = (anchor.screen() if anchor is not None else None) \
            or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2,
                  area.top() + int(area.height() * 0.18))
        self.show()
        self.raise_()
        self.timer.start(int(self.seconds * 1000))  # restarts if already showing


class PrePullBoard(QWidget):
    """Leaderboard window docked to the bottom (or top) edge of the overlay."""

    MAX_ROWS = 8

    def __init__(self, store, on_hide=None):
        super().__init__()
        self.store = store
        self.on_hide = on_hide
        self.colors = load_job_colors()
        self.anchor = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.frame = QFrame(self)
        self.frame.setObjectName("BoardFrame")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)

        self.layout_ = QVBoxLayout(self.frame)
        self.layout_.setContentsMargins(6, 4, 6, 6)
        self.layout_.setSpacing(2)

        header = QHBoxLayout()
        self.enmity_dot = QLabel()
        self.enmity_dot.setFixedSize(6, 6)
        self.title = QLabel("PRE-PULLS")
        self.title.setStyleSheet("color: #ff6b6b; font-size: 10px; font-weight: bold;")
        hide_btn = QPushButton("×")
        hide_btn.setFixedSize(14, 14)
        hide_btn.setToolTip("Hide pre-pull board (right-click the parser to show it again)")
        hide_btn.setStyleSheet(
            "QPushButton { color: #888; background: transparent; border: none;"
            " font-size: 12px; font-weight: bold; padding: 0; }"
            "QPushButton:hover { color: #fff; }")
        hide_btn.clicked.connect(self._hide_clicked)
        header.addWidget(self.enmity_dot)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(hide_btn)
        self.layout_.addLayout(header)

        self.rows = QVBoxLayout()
        self.rows.setSpacing(1)
        self.layout_.addLayout(self.rows)

        self.set_opacity(220)
        self.set_enmity_status(False)
        self.refresh()

    def set_opacity(self, alpha):
        self.frame.setStyleSheet(f"""
            #BoardFrame {{ background: rgba(20, 20, 20, {alpha});
                           border: 1px solid rgba(255, 255, 255, 30); border-radius: 6px; }}
            QLabel {{ background: transparent; border: none; color: #ddd; font-size: 11px; }}
        """)

    def set_enmity_status(self, live):
        color = "#44ff44" if live else "#666666"
        self.enmity_dot.setStyleSheet(f"background-color: {color}; border-radius: 3px; border: none;")
        self.enmity_dot.setToolTip(
            "Enmity data connected: pre-pulls are judged by countdown + enmity" if live else
            "No enmity data (OverlayPlugin /ws not reachable): judging by countdown + log lines only")

    def _hide_clicked(self):
        self.hide()
        if self.on_hide:
            self.on_hide()

    def refresh(self, highlight=None):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        ranking = self.store.ranking()
        self.title.setText(f"PRE-PULLS  ({self.store.total()})")
        if not ranking:
            empty = QLabel("Nobody yet. Clean pulls only!")
            empty.setStyleSheet("color: #777; font-size: 10px; font-style: italic;")
            self.rows.addWidget(empty)

        for rank, (name, info) in enumerate(ranking[:self.MAX_ROWS], start=1):
            self.rows.addWidget(self._make_row(rank, name, info, name == highlight))
        if len(ranking) > self.MAX_ROWS:
            more = QLabel(f"+{len(ranking) - self.MAX_ROWS} more")
            more.setStyleSheet("color: #777; font-size: 10px;")
            self.rows.addWidget(more)

        for i in range(self.rows.count()):
            w = self.rows.itemAt(i).widget()
            if w is not None:
                w.show()
        self.layout_.activate()
        self.follow()

    def _make_row(self, rank, name, info, highlight):
        row = QFrame()
        row.setObjectName("BoardRow")
        row.setFixedHeight(20)
        job = info.get("job", "default")
        color = QColor(self.colors.get(job, self.colors.get("default", "#555555")))
        bg = "rgba(255, 68, 68, 60)" if highlight else "rgba(40, 40, 40, 140)"
        row.setStyleSheet(f"""
            #BoardRow {{ background: {bg}; border-radius: 3px;
                         border-left: 3px solid {color.name()}; }}
        """)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(6)

        rank_lbl = QLabel(f"{rank}.")
        rank_lbl.setFixedWidth(16)
        rank_lbl.setStyleSheet("color: #888;")
        lay.addWidget(rank_lbl)

        pix = job_icon(job, 14)
        if pix is not None:
            icon = QLabel()
            icon.setFixedSize(14, 14)
            icon.setPixmap(pix)
            lay.addWidget(icon)

        name_lbl = QLabel(name)
        lay.addWidget(name_lbl)
        lay.addStretch()

        count_lbl = QLabel(str(info["count"]))
        count_lbl.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        count_lbl.setToolTip(f"Worst: {info.get('worst', 0):.1f}s early")
        lay.addWidget(count_lbl)
        return row

    def attach(self, widget):
        self.anchor = widget
        self.follow()

    def follow(self):
        """Dock under the overlay, or above it when there's no room below."""
        if self.anchor is None:
            return
        geo = self.anchor.frameGeometry()
        self.setFixedWidth(geo.width())
        self.layout().activate()
        self.setFixedHeight(self.sizeHint().height())
        screen = self.anchor.screen()
        pos = QPoint(geo.left(), geo.bottom() + 3)
        if screen is not None:
            area = screen.availableGeometry()
            if pos.y() + self.height() > area.bottom():
                pos = QPoint(geo.left(), geo.top() - self.height() - 3)
        self.move(pos)
