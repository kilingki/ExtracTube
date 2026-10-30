from __future__ import annotations
import math
from typing import Any

POLICY_ID = "fa_sentences_a4f1c2e_hyphen_v1"
PUNCT = {"-"} | set(".,?!。！？…~、，,:：;；\"'“”‘’()[]{}%&+")
CLOSERS = set("\"'”’)]}")
SENT_END = set(".?!。！？…")


class SentenceError(ValueError):
    """Text and items cannot be aligned. The FA file stays in place."""


def core(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace() and ch not in PUNCT)


def align_chunk(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    text = chunk["text"]
    items = chunk["items"]
    expected = "".join(core(item["text"]) for item in items)
    if core(text) != expected:
        raise SentenceError(f"chunk {chunk['index']}: text and items diverged")

    pos = 0
    core_ends: list[int] = []
    for item in items:
        want = core(item["text"])
        if want == "":
            raise SentenceError(f"chunk {chunk['index']}: empty aligned item")
        got = ""
        while pos < len(text) and got != want:
            character = text[pos]
            pos += 1
            if not character.isspace() and character not in PUNCT:
                got += character
        if got != want:
            raise SentenceError(f"chunk {chunk['index']}: could not align {item['text']!r}")
        core_ends.append(pos)

    tokens: list[dict[str, Any]] = []
    prev = 0
    for item, end in zip(items, core_ends):
        trail = end
        while trail < len(text) and (text[trail].isspace() or text[trail] in PUNCT):
            trail += 1
        tokens.append(
            {
                "item": {
                    "text": item["text"],
                    "start_time": item["start_time"],
                    "end_time": item["end_time"],
                },
                "slice": text[prev:trail],
                "language": chunk["language"],
            }
        )
        prev = trail
    return tokens


def _bounds(parts: list[str]) -> list[int]:
    bounds = [0]
    for part in parts:
        bounds.append(bounds[-1] + len(part))
    return bounds


def drop_overlapped_tail(
    prev: list[dict[str, Any]],
    nxt: list[dict[str, Any]],
    max_chars: int,
    boundary_sec: float,
) -> list[dict[str, Any]]:
    if not prev or not nxt or max_chars <= 0:
        return prev

    prev_cores = [core(token["item"]["text"]) for token in prev]
    next_cores = [core(token["item"]["text"]) for token in nxt]
    prev_bounds = _bounds(prev_cores)
    next_bounds = _bounds(next_cores)
    prev_text = "".join(prev_cores)
    next_text = "".join(next_cores)
    limit = min(len(prev_text), len(next_text), max_chars)
    best = 0
    for size in range(1, limit + 1):
        if prev_text[-size:] == next_text[:size]:
            best = size
    if best == 0:
        return _drop_by_time(prev, boundary_sec)

    drop_from = next(
        (index for index in range(len(prev_cores)) if prev_bounds[-1] - prev_bounds[index] == best),
        None,
    )
    next_take = next(
        (index for index in range(len(next_cores) + 1) if next_bounds[index] == best),
        None,
    )
    if drop_from is None or next_take is None:
        return _drop_by_time(prev, boundary_sec)

    dropped = prev[drop_from:]
    if any(token["item"]["start_time"] < boundary_sec - 0.5 for token in dropped):
        return _drop_by_time(prev, boundary_sec)
    return prev[:drop_from]


def _drop_by_time(prev: list[dict[str, Any]], boundary_sec: float) -> list[dict[str, Any]]:
    keep = 0
    for token in prev:
        if token["item"]["start_time"] >= boundary_sec - 1e-3:
            break
        keep += 1
    return prev[:keep]


def sentence_end(token: dict[str, Any], nxt: dict[str, Any] | None) -> bool:
    text = token["slice"].rstrip()
    if not text:
        return False
    index = len(text) - 1
    while index >= 0 and text[index] in CLOSERS:
        index -= 1
    if index < 0 or text[index] not in SENT_END:
        return False
    if text[index] == "…" or (index >= 2 and text[index - 2 : index + 1] == "..."):
        return False
    if text[index] != ".":
        return True

    after = token["slice"][index + 1 :] + ("" if nxt is None else nxt["slice"])
    cursor = 0
    saw_space = False
    while cursor < len(after) and (after[cursor].isspace() or after[cursor] in CLOSERS):
        if after[cursor].isspace():
            saw_space = True
        cursor += 1
    if cursor < len(after) and after[cursor].isdigit() and not saw_space:
        return False
    return True


def _omitted(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": chunk.get("index"),
        "status": chunk.get("status"),
        "start_sample": chunk.get("start_sample"),
        "end_sample": chunk.get("end_sample"),
    }


def convert(doc: dict[str, Any]) -> dict[str, Any]:
    audio = doc["audio"]
    sample_rate = audio["sample_rate"]
    num_samples = audio["num_samples"]
    source: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    for chunk in doc["chunks"]:
        if chunk.get("status") == "aligned" and chunk.get("items"):
            source.append(chunk)
        else:
            omitted.append(_omitted(chunk))
    source.sort(key=lambda chunk: (chunk["start_sample"], chunk["index"]))

    stream: list[dict[str, Any]] = []
    for position, chunk in enumerate(source):
        tokens = align_chunk(chunk)
        if position > 0 and chunk["start_sample"] < source[position - 1]["end_sample"]:
            overlap_sec = (source[position - 1]["end_sample"] - chunk["start_sample"]) / sample_rate
            stream = drop_overlapped_tail(
                stream,
                tokens,
                max(16, int(overlap_sec * 25)),
                chunk["start_sample"] / sample_rate,
            )
        stream.extend(tokens)

    sentences: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []

    def flush() -> None:
        if not buffer:
            return
        items = [token["item"] for token in buffer]
        start = max(0, math.floor(items[0]["start_time"] * sample_rate + 1e-6))
        end = min(num_samples, math.ceil(items[-1]["end_time"] * sample_rate - 1e-6))
        sentences.append(
            {
                "index": len(sentences),
                "start_sample": start,
                "end_sample": max(start + 1, end),
                "text": "".join(token["slice"] for token in buffer).strip(),
                "language": buffer[0]["language"],
                "status": "aligned",
                "items": items,
            }
        )
        buffer.clear()

    for index, token in enumerate(stream):
        buffer.append(token)
        nxt = stream[index + 1] if index + 1 < len(stream) else None
        if sentence_end(token, nxt):
            flush()
    flush()

    return {
        "audio": audio,
        "time_reference": "audio_start",
        "overlap_deduplicated": True,
        "chunks": sentences,
        "omitted_chunks": omitted,
        "sentence_policy_id": POLICY_ID,
    }
