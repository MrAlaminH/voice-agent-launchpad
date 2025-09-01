# end_call_report.py
import json
import logging
import os
from datetime import datetime
from typing import Any

from livekit.agents import AgentSession, metrics

logger = logging.getLogger("agent.end_call_report")


# -----------------------------
# Metrics serialization
# -----------------------------
def _serialize_metrics(usage_collector: metrics.UsageCollector) -> dict:
    try:
        summary = usage_collector.get_summary() if usage_collector else None
        if summary is None:
            return {}
        if hasattr(summary, "__dict__"):
            return {k: v for k, v in summary.__dict__.items() if isinstance(k, str)}
        return {"summary": str(summary)}
    except Exception:
        return {}


# -----------------------------
# Helpers to normalize transcript sources
# -----------------------------
def _ensure_list_from_maybe_items(maybe: Any) -> list:
    """Turn various transcript containers into a list of items."""
    if maybe is None:
        return []
    if isinstance(maybe, list):
        return maybe
    if isinstance(maybe, dict):
        for key in ("items", "messages", "transcript", "entries"):
            if key in maybe and isinstance(maybe[key], list):
                return maybe[key]
        return [maybe]
    try:
        return list(maybe)
    except Exception:
        return [maybe]


def _safe_get(obj: Any, keys: list):
    """Get first existing attribute or dict key among keys. Returns None if none found."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                return obj[k]
        return None
    for k in keys:
        try:
            v = getattr(obj, k, None)
            if v is not None:
                return v
        except Exception:
            continue
    return None


def _item_to_plain_dict(item: Any) -> dict:
    """Convert an item into a plain dict with role, content, text, created_at."""
    if item is None:
        return {
            "role": "unknown",
            "content": [],
            "text": "",
            "created_at": datetime.now().isoformat(),
        }

    if isinstance(item, dict):
        role = str(
            item.get("role") or item.get("sender") or item.get("type") or "unknown"
        )
        content = item.get("content", item.get("content_list", None))
        text = item.get("text") or item.get("message") or None
        ts = (
            item.get("created_at")
            or item.get("ts")
            or item.get("timestamp")
            or item.get("time")
        )
        return {"role": role, "content": content, "text": text, "created_at": ts}

    role = (
        _safe_get(
            item, ["role", "sender", "type", "sender_identity", "participant_identity"]
        )
        or "unknown"
    )
    content = _safe_get(item, ["content", "content_list", "messages"])
    text = _safe_get(item, ["text", "message", "content_text"])
    ts = _safe_get(item, ["created_at", "ts", "timestamp", "time"])
    return {"role": str(role), "content": content, "text": text, "created_at": ts}


def _normalize_items(items: list) -> list:
    """Normalize raw items into list of {role, text, ts} with text always a string."""
    out = []
    for it in _ensure_list_from_maybe_items(items):
        try:
            plain = _item_to_plain_dict(it)
            text = ""
            if plain.get("text"):
                text = str(plain["text"]).strip()
            elif plain.get("content"):
                c = plain["content"]
                if isinstance(c, list):
                    text = " ".join(str(x).strip() for x in c if x is not None).strip()
                else:
                    text = str(c).strip()
            if not text:
                try:
                    text = str(it).strip()
                except Exception:
                    text = ""
            if not text:
                continue
            ts = plain.get("created_at") or datetime.now().isoformat()
            out.append(
                {"role": str(plain.get("role", "unknown")), "text": text, "ts": str(ts)}
            )
        except Exception as exc:
            logger.debug("Failed to normalize an item, skipping", exc_info=exc)
            continue
    return out


def _merge_transcript(items: list) -> list:
    """Merge adjacent entries with the same role and normalize whitespace."""
    merged = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        role = str(it.get("role", "unknown"))
        text = str(it.get("text", "")).strip()
        ts = str(it.get("ts", datetime.now().isoformat()))
        if not text:
            continue
        if merged and merged[-1]["role"] == role:
            merged[-1]["text"] = (merged[-1]["text"] + " " + text).strip()
            merged[-1]["ts"] = ts
        else:
            merged.append({"role": role, "text": text, "ts": ts})
    return merged


def _as_text_block(items: list) -> str:
    """Render transcript as dialogue block (Agent/User lines)."""
    lines = []
    for it in items or []:
        role = it.get("role", "unknown")
        role_l = str(role).lower()
        pretty_role = (
            "User"
            if role_l == "user"
            else (
                "Agent" if role_l in ("assistant", "agent") else str(role).capitalize()
            )
        )
        text = str(it.get("text", "")).strip()
        if text:
            lines.append(f"{pretty_role}: {text}")
    return "\n".join(lines)


# -----------------------------
# Collect transcript items
# -----------------------------
def _collect_raw_transcript_items(ctx, session: AgentSession) -> list:
    """Return a list of raw transcript items from the most reliable available source."""
    try:
        if hasattr(ctx, "proc") and ctx.proc and isinstance(ctx.proc.userdata, dict):
            td = ctx.proc.userdata.get("transcript")
            if td:
                items = _ensure_list_from_maybe_items(td)
                if items:
                    return items
    except Exception:
        logger.debug("ctx.proc.userdata transcript not readable", exc_info=True)

    try:
        if hasattr(session, "history") and session.history:
            hist_dict = None
            try:
                hist_dict = session.history.to_dict()
            except Exception:
                hist_dict = None
            if isinstance(hist_dict, dict):
                for key in ("items", "messages"):
                    if (
                        key in hist_dict
                        and isinstance(hist_dict[key], list)
                        and hist_dict[key]
                    ):
                        return hist_dict[key]
    except Exception:
        logger.debug("session.history not usable", exc_info=True)

    try:
        conv = getattr(session, "conversation", None)
        if conv:
            try:
                return list(conv)
            except Exception:
                pass
        msgs = getattr(session, "messages", None)
        if msgs:
            try:
                return list(msgs)
            except Exception:
                pass
    except Exception:
        logger.debug("session conversation/messages not usable", exc_info=True)

    try:
        ag = getattr(session, "agent", None)
        if ag and hasattr(ag, "transcript"):
            t = ag.transcript
            if t:
                return _ensure_list_from_maybe_items(t)
    except Exception:
        logger.debug("session.agent.transcript not usable", exc_info=True)

    try:
        if hasattr(ctx, "proc") and ctx.proc and isinstance(ctx.proc.userdata, dict):
            tf = ctx.proc.userdata.get("transcript_file")
            if tf and os.path.exists(tf):
                with open(tf, encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        for key in ("transcript", "items", "messages"):
                            if (
                                key in data
                                and isinstance(data[key], list)
                                and data[key]
                            ):
                                return data[key]
                    if isinstance(data, list) and data:
                        return data
    except Exception:
        logger.debug("failed reading transcript_file fallback", exc_info=True)

    return []


# -----------------------------
# Network helper
# -----------------------------
async def _post_json_with_redirects(
    session, url: str, payload: dict, headers: dict, max_redirects: int = 3
):
    current_url = url
    for _ in range(max_redirects + 1):
        resp = await session.post(
            current_url, json=payload, headers=headers, allow_redirects=False
        )
        if resp.status in {301, 302, 303, 307, 308}:
            location = resp.headers.get("Location")
            if not location:
                return resp
            await resp.release()
            current_url = location
            continue
        return resp
    return resp


async def send_end_call_report(webhook_url: str, session_data: dict):
    """Send end-of-call report to the provided webhook."""
    import aiohttp

    headers = {"Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as client:
            resp = await _post_json_with_redirects(
                client, webhook_url, session_data, headers
            )
            async with resp:
                if 200 <= resp.status < 300:
                    logger.info(
                        "End call report sent successfully",
                        extra={"status": resp.status},
                    )
                else:
                    body = await resp.text()
                    logger.warning(
                        "Failed to send end call report",
                        extra={"status": resp.status, "body": body[:500]},
                    )
    except Exception as exc:
        logger.exception("Error sending end call report", exc_info=exc)


# -----------------------------
# Payload builder
# -----------------------------
async def build_end_call_payload(
    ctx, session: AgentSession, usage_collector: metrics.UsageCollector
) -> dict:
    run_meta = ctx.proc.userdata.get("run_meta", {}) if hasattr(ctx, "proc") else {}
    tool_calls = ctx.proc.userdata.get("tool_calls", []) if hasattr(ctx, "proc") else []

    recording_data = ctx.proc.userdata.get("recording", {})
    if not recording_data or not recording_data.get("recording_url"):
        egress_manager = ctx.proc.userdata.get("egress_manager")
        if egress_manager:
            fallback_recording_data = egress_manager.get_recording_metadata()
            if fallback_recording_data and fallback_recording_data.get("recording_url"):
                recording_data = fallback_recording_data
                logger.info("Using fallback recording data from egress manager")

    # ternary operator used here to simplify
    room_sid_value = getattr(ctx.room, "sid", None)
    try:
        maybe_sid = room_sid_value() if callable(room_sid_value) else room_sid_value
        room_sid = await maybe_sid if hasattr(maybe_sid, "__await__") else maybe_sid
    except Exception:
        room_sid = None

    raw_items = _collect_raw_transcript_items(ctx, session) or []
    if isinstance(raw_items, dict):
        raw_items = _ensure_list_from_maybe_items(raw_items)

    normalized = _normalize_items(raw_items)
    merged = _merge_transcript(normalized)
    transcript_text = _as_text_block(merged)
    structured_transcript = {"items": normalized}

    agent_entries = [
        m for m in merged if m.get("role", "").lower() in ("assistant", "agent")
    ]
    user_entries = [m for m in merged if m.get("role", "").lower() == "user"]

    logger.info(
        "Building end-call payload",
        extra={
            "total_raw_items": len(raw_items),
            "normalized": len(normalized),
            "merged": len(merged),
            "agent_entries": len(agent_entries),
            "user_entries": len(user_entries),
            "transcript_text_len": len(transcript_text or ""),
        },
    )

    return {
        "room_name": ctx.room.name,
        "room_sid": room_sid,
        "session_id": str(getattr(session, "session_id", "unknown")),
        "start_time": run_meta.get("start_time"),
        "end_time": datetime.now().isoformat(),
        "transcript": structured_transcript,
        "transcript_text": transcript_text,
        "recording_url": recording_data.get("recording_url"),
        "recording": recording_data,
        "metrics": _serialize_metrics(usage_collector),
        "models": {
            "llm": run_meta.get("llm_model"),
            "stt": run_meta.get("stt_model"),
            "tts_voice": run_meta.get("tts_voice"),
            "turn_detection": "MultilingualModel",
        },
        "tool_calls": tool_calls,
        "source": "livekit-agent",
        "requestType": "end-of-call-report",
    }
