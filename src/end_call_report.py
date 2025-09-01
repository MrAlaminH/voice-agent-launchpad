# end_call_report.py
import logging
import json
import os
import tempfile
from datetime import datetime
from typing import Any, List, Dict

from livekit.agents import AgentSession, metrics

logger = logging.getLogger("agent.end_call_report")


# -----------------------------
# Metrics serialization (unchanged)
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
def _ensure_list_from_maybe_items(maybe: Any) -> List[Any]:
    """Turn various transcript containers into a list of items."""
    if maybe is None:
        return []
    if isinstance(maybe, list):
        return maybe
    if isinstance(maybe, dict):
        # Look for common keys
        for key in ("items", "messages", "transcript", "entries"):
            if key in maybe and isinstance(maybe[key], list):
                return maybe[key]
        # If dict looks like a single item, return it as list
        return [maybe]
    # If it's some object that can be iterated, try to coerce to list safely
    try:
        return list(maybe)
    except Exception:
        return [maybe]


def _safe_get(obj: Any, keys: List[str]):
    """Get first existing attribute or dict key among keys. Returns None if none found."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                return obj[k]
        return None
    # object with attributes
    for k in keys:
        try:
            v = getattr(obj, k, None)
            if v is not None:
                return v
        except Exception:
            continue
    return None


def _item_to_plain_dict(item: Any) -> Dict[str, Any]:
    """
    Convert an item (SDK object or dict) into a plain dict with common fields:
    { role, content (list or str), text (str), created_at/ts/timestamp }.
    """
    if item is None:
        return {"role": "unknown", "content": [], "text": "", "created_at": datetime.now().isoformat()}

    # If it's already a dict, copy relevant keys
    if isinstance(item, dict):
        role = str(item.get("role") or item.get("sender") or item.get("type") or "unknown")
        # content may be a list
        content = item.get("content", item.get("content_list", None))
        text = item.get("text") or item.get("message") or None
        ts = item.get("created_at") or item.get("ts") or item.get("timestamp") or item.get("time")
        return {"role": role, "content": content, "text": text, "created_at": ts}
    # Otherwise try to access attributes
    role = _safe_get(item, ["role", "sender", "type", "sender_identity", "participant_identity"]) or "unknown"
    content = _safe_get(item, ["content", "content_list", "messages"])
    text = _safe_get(item, ["text", "message", "content_text"])
    ts = _safe_get(item, ["created_at", "ts", "timestamp", "time"])
    return {"role": str(role), "content": content, "text": text, "created_at": ts}


def _normalize_items(items: List[Any]) -> List[Dict[str, str]]:
    """Normalize raw items into list of {role, text, ts} with text always a string."""
    out = []
    for it in _ensure_list_from_maybe_items(items):
        try:
            plain = _item_to_plain_dict(it)
            # text priority: explicit 'text' > 'content' list > join of all fallback fields
            text = ""
            if plain.get("text"):
                text = str(plain["text"]).strip()
            elif plain.get("content"):
                c = plain["content"]
                if isinstance(c, list):
                    text = " ".join(str(x).strip() for x in c if x is not None).strip()
                else:
                    text = str(c).strip()
            # final fallback, str(item)
            if not text:
                try:
                    text = str(it).strip()
                except Exception:
                    text = ""
            if not text:
                continue
            ts = plain.get("created_at") or datetime.now().isoformat()
            out.append({"role": str(plain.get("role", "unknown")), "text": text, "ts": str(ts)})
        except Exception as exc:
            logger.debug("Failed to normalize an item, skipping", exc_info=exc)
            continue
    return out


def _merge_transcript(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Merge adjacent entries with the same role and normalize whitespace."""
    merged: List[Dict[str, str]] = []
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


def _as_text_block(items: List[Dict[str, str]]) -> str:
    """Render transcript as dialogue block (Agent/User lines)."""
    lines: List[str] = []
    for it in items or []:
        role = it.get("role", "unknown")
        role_l = str(role).lower()
        if role_l == "user":
            pretty_role = "User"
        elif role_l in ("assistant", "agent"):
            pretty_role = "Agent"
        else:
            pretty_role = str(role).capitalize()
        text = str(it.get("text", "")).strip()
        if text:
            lines.append(f"{pretty_role}: {text}")
    return "\n".join(lines)


# -----------------------------
# Try to collect transcript items from multiple reliable sources
# -----------------------------
def _collect_raw_transcript_items(ctx, session: AgentSession) -> List[Any]:
    """
    Return a list of raw transcript items from the most reliable available source.
    Order of preference:
      1) ctx.proc.userdata['transcript'] (can be list or dict with 'items')
      2) session.history.to_dict() (keys 'items' or 'messages')
      3) session.messages / session.conversation
      4) session.agent.transcript (if present)
      5) fallback: attempt to read file saved in ctx.proc.userdata['transcript_file']
    """
    # 1) ctx.proc.userdata
    try:
        if hasattr(ctx, "proc") and ctx.proc and isinstance(ctx.proc.userdata, dict):
            td = ctx.proc.userdata.get("transcript")
            if td:
                items = _ensure_list_from_maybe_items(td)
                # if td is dict and contains 'items', _ensure_list returns those already
                if items:
                    return items
    except Exception:
        logger.debug("ctx.proc.userdata transcript not readable", exc_info=True)

    # 2) session.history
    try:
        if hasattr(session, "history") and session.history:
            hist_dict = None
            try:
                hist_dict = session.history.to_dict()
            except Exception:
                # Some SDK versions may raise; ignore and try attributes directly
                hist_dict = None
            if isinstance(hist_dict, dict):
                for key in ("items", "messages"):
                    if key in hist_dict and isinstance(hist_dict[key], list) and hist_dict[key]:
                        return hist_dict[key]
    except Exception:
        logger.debug("session.history not usable", exc_info=True)

    # 3) session.conversation / session.messages
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

    # 4) session.agent.transcript
    try:
        ag = getattr(session, "agent", None)
        if ag and hasattr(ag, "transcript"):
            t = getattr(ag, "transcript")
            if t:
                return _ensure_list_from_maybe_items(t)
    except Exception:
        logger.debug("session.agent.transcript not usable", exc_info=True)

    # 5) fallback: check saved transcript file path in ctx.proc.userdata
    try:
        if hasattr(ctx, "proc") and ctx.proc and isinstance(ctx.proc.userdata, dict):
            tf = ctx.proc.userdata.get("transcript_file")
            if tf and os.path.exists(tf):
                with open(tf, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    # The saved file used "transcript" field earlier in agent — try to extract
                    if isinstance(data, dict):
                        for key in ("transcript", "items", "messages"):
                            if key in data and isinstance(data[key], list) and data[key]:
                                return data[key]
                    # else if the saved file directly contains items as list
                    if isinstance(data, list) and data:
                        return data
    except Exception:
        logger.debug("failed reading transcript_file fallback", exc_info=True)

    # nothing found
    return []


# -----------------------------
# Network helper for webhook (unchanged)
# -----------------------------
async def _post_json_with_redirects(session, url: str, payload: dict, headers: dict, max_redirects: int = 3):
    current_url = url
    for _ in range(max_redirects + 1):
        resp = await session.post(current_url, json=payload, headers=headers, allow_redirects=False)
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
            resp = await _post_json_with_redirects(client, webhook_url, session_data, headers)
            async with resp:
                if 200 <= resp.status < 300:
                    logger.info("End call report sent successfully", extra={"status": resp.status})
                else:
                    body = await resp.text()
                    logger.warning("Failed to send end call report", extra={"status": resp.status, "body": body[:500]})
    except Exception as exc:
        logger.exception("Error sending end call report", exc_info=exc)


# -----------------------------
# Payload builder (main)
# -----------------------------
async def build_end_call_payload(ctx, session: AgentSession, usage_collector: metrics.UsageCollector) -> dict:
    """
    Build a JSON-serializable payload for the end-of-call webhook.
    This function collects transcripts from multiple sources (ctx, session.history, agent, file)
    normalizes them, merges adjacent same-role messages, and creates transcript_text
    as a dialogue-style block.
    """
    run_meta = ctx.proc.userdata.get("run_meta", {}) if hasattr(ctx, "proc") else {}
    tool_calls = ctx.proc.userdata.get("tool_calls", []) if hasattr(ctx, "proc") else []

    # Recording metadata fallback unchanged
    recording_data = ctx.proc.userdata.get("recording", {})
    if not recording_data or not recording_data.get("recording_url"):
        egress_manager = ctx.proc.userdata.get("egress_manager")
        if egress_manager:
            fallback_recording_data = egress_manager.get_recording_metadata()
            if fallback_recording_data and fallback_recording_data.get("recording_url"):
                recording_data = fallback_recording_data
                logger.info("Using fallback recording data from egress manager")

    # Room SID resolution unchanged
    room_sid_value = getattr(ctx.room, "sid", None)
    try:
        if callable(room_sid_value):
            maybe_sid = room_sid_value()
        else:
            maybe_sid = room_sid_value
        if hasattr(maybe_sid, "__await__"):
            room_sid = await maybe_sid
        else:
            room_sid = maybe_sid
    except Exception:
        room_sid = None

    # === COLLECT raw items from best available source ===
    raw_items = _collect_raw_transcript_items(ctx, session) or []

    # If raw_items is a dict that contains 'items' (Edge case), expand it
    if isinstance(raw_items, dict):
        raw_items = _ensure_list_from_maybe_items(raw_items)

    # Normalize into list of {role, text, ts}
    normalized = _normalize_items(raw_items)

    # Merge adjacent by same role (cleaner)
    merged = _merge_transcript(normalized)

    # Build the readable multi-line dialogue block
    transcript_text = _as_text_block(merged)

    # For payload: keep structured items as LiveKit-like shape (items)
    structured_transcript = {"items": normalized}

    # Logging detail for debugging
    agent_entries = [m for m in merged if m.get("role", "").lower() in ("assistant", "agent")]
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
        "transcript": structured_transcript,  # full structured transcript (items)
        "transcript_text": transcript_text,   # dialogue-style block (Agent/User lines)
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
