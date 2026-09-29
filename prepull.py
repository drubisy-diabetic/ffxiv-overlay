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

4. NOTHING IS DECIDED UNTIL THE COUNTDOWN REACHES ZERO (+ ``settle`` s for
   late packets). Until then every engagement is only collected.

5. At that point, early engagements (more than ``tolerance`` s before zero)
   are ORDERED BY THE SERVER'S ACTION SEQUENCE NUMBER (field 44 of every
   21/22 line: one counter per instance shared by every player, enemy and
   pet, assigned by the server) instead of by arrival time -- arrival order
   differs between clients (ping), the sequence doesn't. The lowest sequence
   is the first puller. Everyone else whose first early engagement is within
   ``tie_window`` s of the first puller's also counts ("jumped together");
   people reacting to a pre-pull (tank grabbing the boss) come later and
   don't. Enmity-only evidence has no sequence number: it can add or time a
   player but never outranks a sequenced action.

6. A countdown cancelled AFTER someone already engaged is still judged at
   the original zero (cancelling doesn't erase a pre-pull). Every decision --
   clean pulls too -- is returned as a "pull" record for prepull_history.jsonl.
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

    def __init__(self, tolerance=1.0, tie_window=0.5, settle=1.0):
        self.tolerance = float(tolerance)
        self.tie_window = float(tie_window)
        self.settle = float(settle)
        self.jobs = {}          # actor id (hex) -> job abbreviation
        self.names = {}         # actor id (hex) -> name
        self.owners = {}        # pet id (hex) -> owner id (hex)
        self.in_combat = False
        self.last_enmity_at = None
        self.engaged_target = {}   # enemy id -> True/False, from EnmityTargetData
        self.aggro_ids = set()     # enemy ids currently in EnmityAggroList
        self.last_pull = None      # last decision record, for debugging
        self._clear_countdown()

    # ----- state helpers -------------------------------------------------
    def _clear_countdown(self):
        self.countdown_end = None
        self.countdown_start = None
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
        """Evidence is collected from the countdown start until zero + settle."""
        return self.countdown_end is not None and now <= self.countdown_end + timedelta(seconds=self.settle)

    def _owner_of(self, actor_id, name):
        owner = self.owners.get(actor_id)
        if owner:
            return owner, self.names.get(owner, name)
        return actor_id, name

    # ----- evidence --------------------------------------------------------
    def _add_evidence(self, when, kind, actor_id, name=None, job=None, ability=None, seq=None):
        """Record that actor_id engaged at `when` (seen via `kind`)."""
        p = self.pending
        if p is None:
            p = self.pending = {"first": when, "engagements": {}}
        p["first"] = min(p["first"], when)
        rec = p["engagements"].get(actor_id)
        if rec is None:
            rec = p["engagements"][actor_id] = {
                "id": actor_id, "name": name, "job": job, "time": when,
                "ability": ability, "kinds": set(), "seq": seq}
        if when < rec["time"]:
            rec["time"] = when
            if ability:
                rec["ability"] = ability
        if seq is not None and (rec["seq"] is None or seq < rec["seq"]):
            rec["seq"] = seq
            rec["ability"] = ability or rec["ability"]
        rec["kinds"].add(kind)
        rec["name"] = rec["name"] or name
        rec["job"] = rec["job"] or job
        rec["ability"] = rec["ability"] or ability

    def _maybe_finalize(self, now):
        """Decide once the countdown has reached zero (+ settle)."""
        if self.countdown_end is None or now < self.countdown_end + timedelta(seconds=self.settle):
            return None
        if self.pending is None:
            self._clear_countdown()  # nobody engaged by zero: nothing to judge
            return None
        return self._finalize()

    def _finalize(self):
        p = self.pending
        end = self.countdown_end
        cutoff = end - timedelta(seconds=self.tolerance)
        recs = list(p["engagements"].values())
        early = [r for r in recs if r["time"] < cutoff]

        offenders = []
        if early:
            sequenced = [r for r in early if r["seq"] is not None]
            # First puller: lowest server sequence (identical on every client);
            # only when no early action was seen at all, earliest arrival time.
            first = min(sequenced, key=lambda r: r["seq"]) if sequenced else min(early, key=lambda r: r["time"])
            tie_limit = first["time"] + timedelta(seconds=self.tie_window)
            offenders = [r for r in early if r is first or r["time"] <= tie_limit]
            offenders.sort(key=lambda r: (r["seq"] is None, r["seq"] or 0, r["time"]))

        def public(r):
            return {
                "id": r["id"],
                "name": r["name"] or self.names.get(r["id"]) or "Unknown",
                "job": r["job"] or self.jobs.get(r["id"], "default"),
                "early_by": round((end - r["time"]).total_seconds(), 2),
                "ability": r["ability"],
                "seq": f"{r['seq']:X}" if r["seq"] is not None else None,
                "evidence": "+".join(sorted(r["kinds"])),
            }

        ordered = sorted(recs, key=lambda r: (r["time"], r["seq"] or 0))
        record = {
            "type": "pull",
            "timestamp": min(r["time"] for r in recs).isoformat(),
            "countdown_by": self.countdown_by,
            "countdown_start": self.countdown_start.isoformat() if self.countdown_start else None,
            "countdown_end": end.isoformat(),
            "cancelled": p.get("cancelled", False),
            "enmity_live": self.enmity_live(end),
            "offenders": [public(r) for r in offenders],
            "engagements": [public(r) for r in ordered],
        }
        self._clear_countdown()
        self.last_pull = record
        return record

    def tick(self, now=None):
        """Call a few times a second: makes the decision once the countdown
        has reached zero (+ settle)."""
        return self._maybe_finalize(now or _utcnow())

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
            if self.pending is None:
                self._clear_countdown()
            else:
                self.pending["cancelled"] = True  # someone already engaged: still judge it at zero
        else:                                 # 21 / 22 abilities
            self._on_ability(parts, ts)
        return self._maybe_finalize(ts)

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
        self.countdown_start = ts
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
        seq = None
        if len(parts) > 44:
            try:
                seq = int(parts[44], 16)  # server-assigned global action sequence
            except ValueError:
                seq = None
        self._add_evidence(ts, "action", culprit[0], culprit[1], self.jobs.get(culprit[0]), parts[5], seq=seq)
        return None

    # ----- enmity ------------------------------------------------------------
    def feed_enmity(self, message, now=None):
        """Feed one OverlayPlugin EnmityTargetData / EnmityAggroList message."""
        if not isinstance(message, dict):
            return None
        now = now or _utcnow()
        kind = message.get("type")
        if kind == "EnmityTargetData":
            self.last_enmity_at = now
            self._on_target_data(message, now)
        elif kind == "EnmityAggroList":
            self.last_enmity_at = now
            self._on_aggro_list(message, now)
        else:
            return None
        return self._maybe_finalize(now)

    def _on_target_data(self, message, now):
        target = message.get("Target") or {}
        enemy = _hex_id(target.get("ID"))
        if not enemy or not _is_npc(enemy):
            return None
        entries = [e for e in (message.get("Entries") or [])
                   if isinstance(e, dict) and (e.get("Enmity") or 0) > 0]
        self.engaged_target[enemy] = bool(entries)
        if not entries or enemy in self.baseline or not self._window_open(now):
            return None
        # Everyone with enmity on this (new) enemy engaged by now; pets are
        # credited to their owner. Times are "first seen", so a player who
        # was already recorded keeps their earlier time.
        for entry in entries:
            actor = _hex_id(entry.get("ID"))
            name = entry.get("Name")
            job = JOB_ID_TO_ABBR.get(entry.get("Job") or 0)
            owner_id = _hex_id(entry.get("OwnerID"))
            if owner_id and owner_id != "00000000":
                owner = next((e for e in entries if _hex_id(e.get("ID")) == owner_id), None)
                actor = owner_id
                name = (owner or {}).get("Name") or self.names.get(owner_id)
                job = JOB_ID_TO_ABBR.get((owner or {}).get("Job") or 0) or self.jobs.get(owner_id)
            if actor and _is_player(actor):
                self._add_evidence(now, "enmity", actor, name, job or self.jobs.get(actor))
        return None

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
        self._add_evidence(now, "enmity", who_id, name, job)
        return None


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

    def append_history(self, record):
        """One JSON line per pull decision (clean pulls too) in
        prepull_history.jsonl next to the stats -- ~1 KB per pull, meant for
        comparing what different players' overlays saw for the same pull."""
        path = os.path.join(os.path.dirname(self.path), "prepull_history.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"Could not write pre-pull history: {e}")

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

    def show_pull(self, offenders, counts=None, colors=None, anchor=None):
        """Show one or more pre-pullers from the same pull."""
        if len(offenders) == 1:
            return self.show_event(offenders[0], count=(counts or [None])[0], colors=colors, anchor=anchor)
        colors = colors or {}
        default = colors.get("default", "#ffffff")
        self.icon.hide()
        self.name.setTextFormat(Qt.TextFormat.RichText)
        self.name.setText("  +  ".join(
            f'<span style="color:{colors.get(o.get("job", "default"), default)}">{o["name"]}</span>'
            for o in offenders))
        self.name.setStyleSheet("font-size: 18px; font-weight: bold; color: #dddddd;")
        self.detail.setText("  ·  ".join(f"{o['name'].split()[0]} {o['early_by']:.1f}s early" for o in offenders)
                            + "  ·  jumped together")
        self._place_and_show(anchor)

    def show_event(self, event, count=None, colors=None, anchor=None):
        colors = colors or {}
        self.name.setTextFormat(Qt.TextFormat.PlainText)
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

        self._place_and_show(anchor)

    def _place_and_show(self, anchor):
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
