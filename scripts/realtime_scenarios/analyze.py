#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import sys

SCENARIOS = ["A_full_drain", "B_barge_during_streaming", "C_barge_during_drain", "D_cough_during_drain"]
INTERESTING = {"turn", "bargein", "response", "stt", "vad"}


def fmt_payload(
    p: dict,
    keys: tuple[str, ...] = (
        "role",
        "status",
        "delay_ms",
        "reason",
        "played_ms",
        "audio_duration_ms",
        "total_audio_ms",
        "deferred_ms",
        "phrases",
        "completed_phrases",
        "failed_phrases",
        "audio_start_ms",
        "audio_end_ms",
    ),
) -> str:
    p = p or {}
    bits: list[str] = []
    for k in keys:
        if k in p:
            v = p[k]
            if isinstance(v, str) and len(v) > 30:
                v = v[:27] + "..."
            bits.append(f"{k}={v}")
    if "heard" in p:
        s = p["heard"][:40].replace("\n", " ")
        bits.append(f'heard="{s}{"..." if len(p["heard"]) > 40 else ""}"')
    if "unheard" in p:
        s = p["unheard"][:40].replace("\n", " ")
        bits.append(f'unheard="{s}{"..." if len(p["unheard"]) > 40 else ""}"')
    if "text" in p and p.get("text"):
        s = str(p["text"])[:40].replace("\n", " ")
        bits.append(f'text="{s}"')
    return " ".join(bits)


def analyze(name: str, path: Path) -> None:
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not events:
        print(f"\n{name}: empty")
        return
    base = events[0]["ts_wall"]

    print(f"\n{'=' * 70}")
    print(f"{name}: {len(events)} events, span={events[-1]['ts_wall'] - base:.2f}s")
    print(f"{'=' * 70}")

    print("\nTimeline (turn / bargein / response / vad / stt only):")
    for e in events:
        if e.get("lane") not in INTERESTING:
            continue
        if e.get("lane") == "stt" and e.get("kind") == "partial":
            continue
        if e.get("lane") == "vad" and e.get("kind") == "pending_start":
            continue
        rel = e["ts_wall"] - base
        corr = e.get("corr") or {}
        cid = (corr.get("turn_id") or "-")[:6]
        rid = (corr.get("response_id") or "-")[:6]
        print(f"  +{rel:6.3f}s  {e['lane']:9s} {e['kind']:25s} t={cid} r={rid}  {fmt_payload(e.get('payload'))}")

    print("\nAnalysis:")

    response_dones = [(i, e) for i, e in enumerate(events) if e["lane"] == "response" and e["kind"] == "done"]
    response_cancels = [(i, e) for i, e in enumerate(events) if e["lane"] == "response" and e["kind"] == "cancelled"]
    turn_ends = [(i, e) for i, e in enumerate(events) if e["lane"] == "turn" and e["kind"] == "turn_end"]
    print(
        f"  response.done: {len(response_dones)}, response.cancelled: {len(response_cancels)}, turn_end: {len(turn_ends)}"
    )

    for i, e in response_dones:
        adm = (e.get("payload") or {}).get("total_audio_ms", 0)
        following = [(j, te) for j, te in turn_ends if j > i]
        if following:
            jt, te = following[0]
            gap_ms = (te["ts_wall"] - e["ts_wall"]) * 1000
            tep = te.get("payload") or {}
            ts = tep.get("status", "?")
            played = tep.get("played_ms", "-")
            audio_dur = tep.get("audio_duration_ms", "-")
            verdict = ""
            if ts == "completed" and adm > 0:
                if abs(gap_ms - adm) < 200:
                    verdict = f"OK deferred ~{adm}ms (gap={gap_ms:.0f}ms)"
                else:
                    verdict = f"WARN gap={gap_ms:.0f}ms vs expected ~{adm}ms"
            elif ts == "truncated":
                verdict = f"truncated; played_ms={played}/{audio_dur}, gap-from-done={gap_ms:.0f}ms"
            elif ts == "cancelled":
                verdict = f"cancelled; played_ms={played}, gap-from-done={gap_ms:.0f}ms"
            print(f"  response.done(total_audio_ms={adm}) -> turn_end(status={ts}): {verdict}")

    for i, e in response_cancels:
        ep = e.get("payload") or {}
        following = [(j, te) for j, te in turn_ends if j > i]
        if following:
            jt, te = following[0]
            gap_ms = (te["ts_wall"] - e["ts_wall"]) * 1000
            tep = te.get("payload") or {}
            print(
                f"  response.cancelled(played_ms={ep.get('played_ms', '-')}) -> turn_end(status={tep.get('status', '?')}, played_ms={tep.get('played_ms', '-')}, gap={gap_ms:.0f}ms)"
            )

    bargein_pending = [e for e in events if e["lane"] == "bargein" and e["kind"] == "bargein_pending"]
    bargein_fired = [e for e in events if e["lane"] == "bargein" and e["kind"] == "bargein_fired"]
    bargein_cancelled = [e for e in events if e["lane"] == "bargein" and e["kind"] == "bargein_cancelled"]
    bargein_context = [e for e in events if e["lane"] == "turn" and e["kind"] == "bargein_context"]
    print(
        f"  bargein: {len(bargein_pending)} pending, {len(bargein_fired)} fired, {len(bargein_cancelled)} cancelled (false starts)"
    )
    for bc in bargein_context:
        p = bc.get("payload") or {}
        print(f"    bargein_context: heard={p.get('heard', '')!r}; unheard={p.get('unheard', '')!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir",
        default=os.environ.get("OUT_DIR", "/tmp/realtime_scenarios"),
        help="Directory containing <scenario>.ndjson files",
    )
    args = ap.parse_args()
    base = Path(args.dir)
    for sc in SCENARIOS:
        p = base / f"{sc}.ndjson"
        if not p.exists():
            print(f"{sc}: missing ({p})", file=sys.stderr)
            continue
        analyze(sc, p)


if __name__ == "__main__":
    main()
