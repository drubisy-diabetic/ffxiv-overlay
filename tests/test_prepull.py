from prepull import PrePullDetector, PrePullStore, extract_log_line

T = "2026-09-23T21:00:{:07.4f}00+02:00"   # seconds -> ACT-style timestamp


def ts(sec):
    return T.format(sec)


def countdown(sec, n=10, who="Tank Guy"):
    return f"268|{ts(sec)}|10000001|50|{n}|00|{who}|x"


def ability(sec, src, src_name, tgt, tgt_name, flags="720003", name="Fast Blade"):
    return f"21|{ts(sec)}|{src}|{src_name}|9|{name}|{tgt}|{tgt_name}|{flags}|0|x"


BOSS = ("40000100", "Boss")
DPS = ("10000002", "Eager Dps")


def run(lines, **kw):
    d = PrePullDetector(**kw)
    d.feed_line(f"03|{ts(0)}|10000002|Eager Dps|1F|64|0000|50|W|0|0|1|1|1|1")
    return [e for e in (d.feed_line(l) for l in lines) if e]


def test_early_attack_is_prepull():
    ev = run([countdown(0), ability(7.0, *DPS, *BOSS)])
    assert len(ev) == 1
    assert ev[0]["name"] == "Eager Dps" and ev[0]["job"] == "mch"
    assert ev[0]["early_by"] == 3.0 and ev[0]["countdown_by"] == "Tank Guy"


def test_on_time_and_caster_landing_are_clean():
    assert run([countdown(0), ability(10.2, *DPS, *BOSS)]) == []
    assert run([countdown(0), ability(9.4, *DPS, *BOSS)]) == []   # within 1s tolerance


def test_only_first_engagement_counts():
    ev = run([countdown(0), ability(5, *DPS, *BOSS), ability(6, "10000003", "Other", *BOSS)])
    assert [e["name"] for e in ev] == ["Eager Dps"]


def test_cancelled_countdown_and_no_countdown():
    assert run([countdown(0), f"269|{ts(2)}|10000001|50|Tank Guy|x", ability(5, *DPS, *BOSS)]) == []
    assert run([ability(5, *DPS, *BOSS)]) == []


def test_buffs_heals_and_pets_ignored():
    lines = [
        countdown(0),
        f"03|{ts(0.5)}|40000200|Eos|00|64|10000002|0|0|0|0|1|1|1|1",
        ability(2, *DPS, *DPS, flags="E", name="Reassemble"),              # self buff
        ability(3, "40000200", "Eos", *DPS, flags="200004", name="Embrace"),  # pet heal
        ability(4, *DPS, "E0000000", "", flags="0"),                         # untargeted
        ability(5, "40000200", "Eos", *BOSS),                               # pet hits boss? not player
    ]
    assert run(lines) == []


def test_body_pull_blames_target():
    ev = run([countdown(0), ability(4, *BOSS, *DPS, flags="710003", name="attack")])
    assert ev and ev[0]["name"] == "Eager Dps"


def test_countdown_ignored_in_combat_and_zone_change_resets():
    assert run([f"260|{ts(0)}|1|1|0|1|x", countdown(1), ability(3, *DPS, *BOSS)]) == []
    assert run([countdown(0), f"01|{ts(1)}|3D3|Somewhere|x", ability(3, *DPS, *BOSS)]) == []


def test_extract_log_line():
    assert extract_log_line({"msgtype": "Chat", "msg": "21|x"}) == "21|x"
    assert extract_log_line({"type": "LogLine", "rawLine": "22|y"}) == "22|y"
    assert extract_log_line({"msgtype": "CombatData", "msg": {}}) is None


def test_store_counts(tmp_path):
    s = PrePullStore(str(tmp_path / "s.json"))
    ev = {"name": "A", "job": "war", "early_by": 2.0, "timestamp": "t"}
    s.add(ev); s.add(dict(ev, early_by=4.0)); s.add(dict(ev, name="B"))
    s2 = PrePullStore(str(tmp_path / "s.json"))
    assert s2.ranking()[0] == ("A", {"count": 2, "job": "war", "worst": 4.0, "last": "t"})
    assert s2.total() == 3


# ---------------------------------------------------------------------------
# Countdown + enmity
# ---------------------------------------------------------------------------
from prepull import parse_ts

BOSS_DEC, DUMMY_DEC = 0x40000100, 0x40000999
DPS_DEC, TANK_DEC, PET_DEC = 0x10000002, 0x10000001, 0x40000200


def at(sec):
    return parse_ts(ts(sec))


def target_data(enemy, entries):
    return {"type": "EnmityTargetData", "Target": {"ID": enemy, "Name": "Boss"},
            "Entries": [dict(ID=i, Name=n, Enmity=e, OwnerID=o, Job=j, HateRate=0, isMe=False)
                        for i, n, e, o, j in entries]}


def fresh():
    d = PrePullDetector()
    d.feed_line(f"03|{ts(0)}|10000002|Eager Dps|1F|64|0000|50|W|0|0|1|1|1|1")
    d.feed_enmity(target_data(BOSS_DEC, []), now=at(0))   # enmity is live, boss untouched
    return d


def test_enmity_alone_decides_after_confirm_window():
    d = fresh()
    d.feed_line(countdown(0))
    assert d.feed_enmity(target_data(BOSS_DEC, [(DPS_DEC, "Eager Dps", 500, 0, 31)]), now=at(7)) is None
    assert d.tick(now=at(7.3)) is None                      # still waiting for an action line
    ev = d.tick(now=at(7.8))
    assert ev["name"] == "Eager Dps" and ev["early_by"] == 3.0 and ev["evidence"] == "enmity"
    assert ev["job"] == "mch"


def test_enmity_plus_action_uses_earliest_time_and_ability():
    d = fresh()
    d.feed_line(countdown(0))
    d.feed_enmity(target_data(BOSS_DEC, []), now=at(6.7))   # enmity keeps streaming
    assert d.feed_line(ability(6.8, *DPS, *BOSS, name="Air Anchor")) is None   # waits for enmity
    ev = d.feed_enmity(target_data(BOSS_DEC, [(DPS_DEC, "Eager Dps", 900, 0, 31)]), now=at(7.1))
    assert ev["early_by"] == 3.2 and ev["ability"] == "Air Anchor" and ev["evidence"] == "action+enmity"


def test_enmity_top_overrides_action_culprit():
    d = fresh()
    d.feed_line(countdown(0))
    d.feed_enmity(target_data(BOSS_DEC, []), now=at(5.9))
    d.feed_line(ability(6.0, "40000100", "Boss", "10000001", "Tank Guy", flags="710003", name="attack"))
    ev = d.feed_enmity(target_data(BOSS_DEC, [(TANK_DEC, "Tank Guy", 10, 0, 19),
                                              (DPS_DEC, "Eager Dps", 800, 0, 31)]), now=at(6.2))
    assert ev["name"] == "Eager Dps" and ev["ability"] is None


def test_pet_enmity_goes_to_owner():
    d = fresh()
    d.feed_line(countdown(0))
    d.feed_enmity(target_data(BOSS_DEC, [(PET_DEC, "Automaton Queen", 700, DPS_DEC, 0),
                                         (DPS_DEC, "Eager Dps", 100, 0, 31)]), now=at(5))
    ev = d.tick(now=at(6))
    assert ev["name"] == "Eager Dps" and ev["id"] == "10000002"


def test_enemy_engaged_before_countdown_is_baseline():
    d = fresh()
    d.feed_enmity(target_data(DUMMY_DEC, [(DPS_DEC, "Eager Dps", 50, 0, 31)]), now=at(0))
    d.feed_line(countdown(1))
    d.feed_enmity(target_data(DUMMY_DEC, [(DPS_DEC, "Eager Dps", 90, 0, 31)]), now=at(4))
    d.feed_line(ability(4, *DPS, "40000999", "Striking Dummy"))
    assert d.tick(now=at(6)) is None and d.pending is None


def test_on_time_enmity_is_clean():
    d = fresh()
    d.feed_line(countdown(0))
    d.feed_enmity(target_data(BOSS_DEC, [(DPS_DEC, "Eager Dps", 500, 0, 31)]), now=at(9.6))
    assert d.tick(now=at(11)) is None and d.last_pull[0] == "Eager Dps"


def test_aggro_list_new_enemy_targets_puller():
    d = fresh()
    d.feed_line(countdown(0))
    msg = {"type": "EnmityAggroList", "AggroList": [
        {"ID": BOSS_DEC, "Name": "Boss", "HateRate": 100, "Target": {"ID": TANK_DEC, "Name": "Tank Guy"}}]}
    d.feed_enmity(msg, now=at(4))
    ev = d.tick(now=at(5))
    assert ev["name"] == "Tank Guy" and ev["early_by"] == 6.0


def test_no_countdown_enmity_does_nothing():
    d = fresh()
    assert d.feed_enmity(target_data(BOSS_DEC, [(DPS_DEC, "Eager Dps", 500, 0, 31)]), now=at(5)) is None
    assert d.tick(now=at(9)) is None


def test_action_decides_immediately_when_enmity_not_live():
    d = PrePullDetector()   # never received enmity
    d.feed_line(countdown(0))
    ev = d.feed_line(ability(6.0, *DPS, *BOSS))
    assert ev and ev["evidence"] == "action"
