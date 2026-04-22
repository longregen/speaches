# Realtime Inspector

The realtime inspector is a browser UI for observing the internal state of a
speaches realtime session as it runs. It renders every emitted inspector event
on a timeline, gives you a live mic+TTS playback client, and lets you click into
individual events to see their full payload — including the messages sent to the
LLM, the tokens it streamed back, the phrases that were sent to TTS, and the
chunks TTS emitted.

Use it to answer questions like:

- What did the LLM actually receive (system prompt, history)?
- Which tokens did it generate, in what order?
- Did a VAD confirmed_start line up with the STT final?
- Why did TTS fall behind?
- Which turn does this error belong to?

!!! warning

    The inspector is a developer tool. It streams raw internal events and is
    not intended for end-user exposure.

## Opening the inspector

The inspector is served from the same process as the realtime server under
`/inspect` (static files in `src/speaches/inspect/static/`). Open it in a
browser, then either:

- Click **New session** in the top bar to start a realtime session directly
  from the inspector page. This grabs the mic, opens a WebSocket to
  `/v1/realtime`, and auto-opens the new session in the timeline. The built-in
  client sends a short `instructions` prompt so the model keeps responses
  terse.
- Click the **session pill** (below the "speaches / Inspector" title) to pick
  an existing live or historical session from the dropdown.

When a new live session starts elsewhere, the inspector auto-opens it via a
`window` event (`inspector:openSession`).

## Lanes

Each lane is a horizontal row. The gutter on the left labels them; the counter
on the right shows how many events have landed on that lane.

| Lane          | What it carries                                                    |
|---------------|--------------------------------------------------------------------|
| `error`       | Mirrored error/dropped/raised events from any lane                 |
| `audio_level` | PCM RMS samples (mic in and TTS out), rendered as a sparkline       |
| `vad`         | Silero voice-activity boundaries (`confirmed_start`, `stopped`, …) |
| `stt`         | Whisper partials and finals                                        |
| `turn`        | Conversational turn start/end and `user_committed` markers         |
| `bargein`     | Pending / fired / cancelled / missed barge-in attempts             |
| `llm`         | Chat-completion lifecycle: `request`, `first_token`, `chunk`, `done` |
| `response`    | Response plan: `plan_start`, `phrase_boundary`, `done`             |
| `tts_req`     | TTS phrase requests (`phrase_sent`, `phrase_done`, `error`)        |
| `tts_chunk`   | 24 kHz PCM chunks streamed back from the TTS executor              |
| `wire`        | Protocol control (non-data events)                                 |

The lane palette is configurable (`warm`, `semantic`, `mono`) via the tweak
panel. Toggle lane visibility with a single click in the gutter;
double-click to un-hide and jump to the next event on that lane.

## Timeline layout

- **Ruler** (top): time ticks, sub-ticks, turn overlays, the dashed hover
  cursor, and the selection line.
- **Lanes** (middle): bands (time ranges) and ticks (point events). Grid
  lines match the ruler.
- **Minimap** (bottom): full-session overview with a viewport rectangle you
  can drag to pan.

Time labels use `ms` for values below 1 second, `s` (3 decimals) up to 10 s,
and `s` (2 decimals) beyond. Deep zoom shows sub-millisecond ticks (`µs`).

### Bands vs ticks

A **band** is a time range with an opening and closing event. For example, an
STT utterance band is bracketed by the VAD `confirmed_start` → `stopped` pair;
an LLM response band by `llm.request` → `llm.done`.

A **tick** is a point event that isn't a band endpoint — e.g. an LLM `chunk`,
an STT `partial`, a `phrase_boundary`, or an error marker.

Bands are drawn as translucent rectangles with opaque left/right edges; ticks
are drawn as short vertical glyphs in one of three treatments (`blocks`,
`ticks`, `dots`).

### Band labels

Each band gets a short summary label when it's wide enough on screen:

| Band                          | Label                                              |
|-------------------------------|----------------------------------------------------|
| `vad` speech                  | `speech · <duration>ms`                            |
| `stt` utterance               | `"<transcript>"`                                   |
| `llm` response                | `llm <tokens> tok · <elapsed>ms`                   |
| `response` assembly           | `assembly · N/M phrases` (with failed count)       |
| `tts_req` phrase              | the phrase text                                    |
| `tts_req` phrase_error        | `✕ error · worker closed`                          |
| `tts_chunk`                   | `<phrase text> · <ms_audio>ms`                     |

STT, LLM, and TTS chunk labels are **sticky**: when the band extends off the
left edge of the viewport, the label carries along at the viewport edge so it
stays readable. Other labels stay pinned to the band's left edge and scroll
off with it.

The LLM lane also renders each chunk's `delta` text as a small inline label
beside the chunk tick, overlap-suppressed by measured text width.

## Hover and click

### Hit-testing

The hit-test prioritizes **points over ranges** and treats STT / LLM / response
/ TTS chunk bands as *progressive* — hovering along them scrubs through their
internal state rather than returning the same broad endpoint. Specifically:

1. Find which lane + (for `tts_chunk`) which sub-row the cursor sits on.
2. Find the band containing (or closest to) the cursor.
3. If the band is progressive, pick the nearest-in-time candidate among:
    - the latest "stream event" inside the band whose `t ≤ cursor`
      (`chunk` for `llm`, `partial`/`final` for `stt`, `phrase_boundary` for
      `response`),
    - `band.open`,
    - `band.close`.
4. Otherwise, prefer a nearby tick within a 6-pixel tolerance; else fall back
   to the band's open/close endpoint nearer the cursor.

Consequences:

- Hovering the middle of an LLM response scrubs through generated tokens.
- Hovering near the start surfaces `llm.request`.
- Hovering near the end surfaces `llm.done`.
- Hovering an STT utterance scrubs through partials, then the `final`.
- Hovering a TTS chunk always lands on that specific chunk's event.

### Tooltip

The hover tooltip shows the event's lane chip, kind, timestamp, and a curated
set of payload fields: `text`, `delta`, `model`, `bytes`, `event_type`,
`elapsed_ms`, `ttft_ms`, `tok_out`, `prob`, `rms`, `ms_audio`, `reason`,
`error`. Long text wraps across multiple lines (capped at ~600 chars).

Extras on specific events:

- **LLM `chunk`** also gets a `cumm` row: the concatenation of every chunk's
  `delta` for the same `response_id` up through this one — the full
  generated text at this point in time.
- **`response.done` / `response.cancelled`** gets a `phrases` row listing the
  TTS phrases the response produced, numbered `1. 2. 3.` in emission order.

### Selection + inspector panel

Click an event to select it. The selection is marked by a warm ring on the
canvas and loads the right-side inspector panel.

The panel has three tabs:

- **Pretty** — structured sections:
    - **Event**: lane, kind, seq, `t (mono)`, wall-clock, span_id
    - **Correlation**: every present corr id, shown as a friendly alias
      (`turn 1`, `item 3`, …) instead of the raw hash. Each is a clickable
      row: clicking jumps the timeline to (and centers on) the first event
      carrying that correlation id. STT events get an extra `vad · speech ·
      <duration>ms` row that jumps to the matching VAD `confirmed_start`.
    - **Payload**: every payload key/value. Objects render as pretty JSON;
      the `messages` array on an `llm.request` renders one `role: content`
      line per message instead of raw JSON.
    - **Cross-reference**: session id and (when present) an OTEL span link.
- **Raw** — the full event as formatted JSON.
- **Related** — all other events that share any corr id with the selection,
  each clickable to jump there.

### Correlation aliases

Raw corr ids (`ed8e7f69…`) are replaced with short sequential aliases: the
first turn observed becomes `turn 1`, the next `turn 2`, etc. Aliases are
computed per-kind (`turn`, `item`, `response`, `phrase`) in the order events
arrive, reset on session switch, and used everywhere — correlation rows,
related list, hover tooltip. The original ids are still visible in the Raw
tab.

## Multi-row TTS chunks

When a session produces concurrent TTS playback (e.g. overlapping responses),
the `tts_chunk` lane grows to stack overlapping bands on sub-rows. Each chunk
is greedy-packed into the lowest sub-row whose previous chunk already ended.
Chunks within a single response are contiguous in playback time, so they stay
on the same row; only chunks from parallel responses trigger a second row.

The lane height in the gutter and canvas adapts automatically. When there is
no overlap the lane renders exactly like any other lane.

## Turn band overlay

User turns (bracketed by `turn.turn_start role=user` … `turn.turn_end`) draw
an amber backdrop across the whole canvas, plus a backdrop strip in the
ruler. This makes it easy to see which turn a given event belongs to even
when the turn lane is hidden.

Jump between turns with `Ctrl+,` / `Ctrl+.`.

## Replay

Hold `Shift` + drag on the timeline to define a replay window; press `Space`
to replay audio within that window. The playhead is drawn as a white
vertical marker with an arrow at top. `R` starts a live replay from the
current cursor; `Esc` (or `Stop`) cancels.

The built-in client can also independently **mute mic** or **mute TTS**
playback via the channel buttons in the top bar.

## Modes & playback

The inspector has two modes:

- **Live** — follow-tail is on. The viewport tracks the newest events and no
  audio is played back.
- **Replay** — follow-tail is off. The viewport stays put; you can click to
  select, hover to inspect, and play back the captured audio (mic + TTS).

Transitions are driven by `f` (Live toggle) and `Space` (Replay playback):

| In Live                                  | In Replay (idle)                                           | In Replay (playing)         |
|------------------------------------------|------------------------------------------------------------|-----------------------------|
| `Space` → exit Live, stop audio, Replay  | `Space` → play from the anchor (selected event if any, else cursor line) to end | `Space` → stop playback (stay in Replay) |
| `f` → no-op (already Live)               | `f` → back to Live, viewport jumps to tail                 | `f` → back to Live, stop playback |

The "anchor" is the solid selection line if an event is selected, otherwise
the dotted hover cursor line.

## Keyboard shortcuts

| Key                 | Action                                     |
|---------------------|--------------------------------------------|
| `Space`             | See **Modes & playback** above             |
| `f`                 | Toggle Live (follow-tail)                  |
| `,` / `.`           | Pan left / right                           |
| `Ctrl+,` / `Ctrl+.` | Prev / next turn                           |
| `Esc`               | Stop replay / blur input                   |

## Architecture

The inspector is entirely client-side static files plus a small
backend-relay:

```
src/speaches/inspect/
├── emit.py             # inspect_emit.emit(lane, kind, **payload)
├── registry.py         # per-session relay (fanout of events to subscribers)
├── relay.py            # WebSocket relay for /v1/inspect/<sid>/stream
├── audio_store.py      # raw mic/tts capture for replay + WAV export
├── retention.py        # on-disk ndjson rotation
└── static/
    ├── index.html      # shell — topbar, lanes, inspector panel, minimap
    └── inspector/
        ├── app.js      # UI wiring: status, inspector panel, keybinds,
        │               # replay, session list, tooltip content, aliases
        ├── client.js   # optional "New session" flow (mic capture + TTS playback)
        └── timeline.js # canvas renderer — lanes, bands, ticks, hit-test
```

### Event flow

1. Speaches runtime code calls `inspect_emit.emit(lane, kind, **payload)`.
   The call is a no-op when no inspector session is active for the current
   task.
2. The relay assigns a monotonically increasing `seq`, stamps `ts_mono_ns`
   and `ts_wall`, attaches current corr ids (`turn_id`, `item_id`,
   `response_id`, `phrase_id`) and OTEL span id, and publishes the event to
   subscribers.
3. `/v1/inspect/<sid>/stream` streams events as ndjson over WebSocket.
4. `app.js` `normalizeEvent` anchors `t = 0` at the first event's `ts_wall`,
   registers correlation aliases, and hands the event to the `Timeline`.
5. `Timeline.appendEvent` pushes into `this.events`, rebuilds derived state
   (`this.bands`, `this.turns`, `this.ttsChunkRows`), and schedules a
   redraw.

### `timeline.js` layout

The renderer is one IIFE organized in numbered sections (see the banners in
the file):

1. **Config** — lane metadata + palettes, shared with `app.js` via
   `INSPECTOR_DATA`.
2. **Domain types** — `makeBand(lane, kind, t0, t1, {open, close, corr,
   ongoing})`, `makeTurn(turnId, t0, t1)`.
3. **Constructor** — state fields, resize wiring.
4. **Ingest** — `rebuildBands` / `rebuildTurns`. Bands are produced by
   lane-specific handlers (`ingestVad`, `ingestSttFinal`, `ingestLlm`,
   `ingestResponse`, `ingestTtsReq`, `ingestTtsChunk`). Open events are
   tracked per correlation id and paired with their close counterpart.
   `closeOngoingBands` extends still-open bands to the last-observed
   timestamp. `assignTtsChunkRows` greedy-packs TTS chunks into sub-rows.
5. **Coordinates** — `msToPx`, `_pxToMs`, per-lane `laneHeight(laneId)`,
   `laneYOffset(idx)`, `bandRowMetrics(band, laneY, laneH)`, `colorFor`.
6. **Render** — `drawRuler`, `drawLaneRows`, `drawMinimap`, `renderGutter`,
   `refreshGutterState`, `drawAudioSparkline`, `drawLlmTokenLabels`.
7. **Band drawing** — `drawBand`, `drawBandLabel`, `bandLabel`.
8. **Tick drawing** — `drawTick`.
9. **Minimap + gutter**.
10. **Hit-test** — `hitTest`, `findBandAt`, `isProgressiveBand`,
    `progressiveHit`, `pickBandEndpoint`.
11. **View commands** — `zoomAtPx`, `panBy`, `fit`, `toEnd`, setters.
12. **Formatters** — `formatMs`, `niceStep`, `isBandEndpoint`.

### Adding a new lane or event

1. Add an entry in the `LANES` list (both fallback in `timeline.js` and the
   default in `app.js`) and a color per palette in `PALETTES`.
2. If the lane needs bands, add an ingest handler in `rebuildBands` and a
   label branch in `bandLabel`.
3. If the lane has point events, decide whether `isBandEndpoint` should
   filter them out of tick drawing.
4. Server-side: call `inspect_emit.emit("<lane>", "<kind>", corr=…,
   **payload)` from the runtime code. Use the mutators
   (`set_turn_id`/`set_item_id`/`set_response_id`/`set_phrase_id`) or an
   explicit `corr=` arg to stamp the right correlation ids.
5. If the event carries long text (tokens, transcripts, etc.) and you want
   it in the tooltip, add a row to `showTooltip` in `app.js`.

## Related

- [Realtime API](./usage/realtime-api.md) — the transport and protocol the
  inspector is observing.
- [Voice chat](./usage/voice-chat.md) — end-to-end setup prerequisites.
- [VAD](./usage/vad.md) — Silero configuration relevant to the `vad` lane.
