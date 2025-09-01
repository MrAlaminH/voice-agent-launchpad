import asyncio
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    NOT_GIVEN,
    Agent,
    AgentFalseInterruptionEvent,
    AgentSession,
    ConversationItemAddedEvent,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    RoomOutputOptions,
    UserInputTranscribedEvent,
    WorkerOptions,
    cli,
    metrics,
)
from livekit.plugins import deepgram, google, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# Local imports
try:
    from .end_call_report import build_end_call_payload, send_end_call_report
    from .function_callings.tools_appointments import AppointmentTools
    from .function_callings.tools_telephony import TelephonyTools
    from .main.egress_manager import EgressManager
    from .main.telephony_manager import TelephonyManager
    from .system_prompt import SYSTEM_PROMPT
except ImportError:
    from end_call_report import build_end_call_payload, send_end_call_report
    from src.function_callings.tools_appointments import AppointmentTools
    from src.function_callings.tools_telephony import TelephonyTools
    from src.main.egress_manager import EgressManager
    from src.main.telephony_manager import TelephonyManager
    from system_prompt import SYSTEM_PROMPT

# Logging setup
logger = logging.getLogger("agent")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
load_dotenv(".env.local")


# --- Assistant stripped of transcript logic ---
class Assistant(Agent, AppointmentTools, TelephonyTools):
    def __init__(self, telephony_manager=None) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)
        self.telephony_manager = telephony_manager


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata.setdefault("background_tasks", set())


async def _build_end_call_payload(
    ctx: JobContext, session: AgentSession, usage_collector: metrics.UsageCollector
) -> dict:
    return await build_end_call_payload(ctx, session, usage_collector)


async def entrypoint(ctx: JobContext):
    """
    LiveKit-official transcript handling:
      • observe conversation commits via conversation_item_added (optional)
      • final transcript from session.history.to_dict()
      • send via end-call webhook
    """
    ctx.log_context_fields = {"room": ctx.room.name}

    ctx.proc.userdata["run_meta"] = {
        "start_time": datetime.now().isoformat(),
        "llm_model": os.getenv("LLM_MODEL", "gemini-2.0-flash-lite"),
        "stt_model": "deepgram:nova-3",
        "tts_voice": "deepgram:aura-2-amalthea-en",
    }

    telephony_manager = None
    if os.getenv("ENABLE_TELEPHONY", "0") == "1":
        try:
            lkapi = api.LiveKitAPI()
            telephony_manager = TelephonyManager(lkapi)
            ctx.proc.userdata["telephony_manager"] = telephony_manager
            logger.info("Telephony manager initialized")
        except Exception as exc:
            logger.warning("Failed to initialize telephony manager", exc_info=exc)

    session = AgentSession(
        llm=google.LLM(model=os.getenv("LLM_MODEL", "gemini-2.0-flash-lite")),
        stt=deepgram.STT(model="nova-3", language="multi"),
        tts=deepgram.TTS(model="aura-2-amalthea-en"),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
        use_tts_aligned_transcript=True,
    )
    usage_collector = metrics.UsageCollector()

    # === Events ===
    @session.on("agent_false_interruption")
    def _on_agent_false_interruption(ev: AgentFalseInterruptionEvent):
        logger.info("False positive interruption detected; resuming agent")
        session.generate_reply(instructions=ev.extra_instructions or NOT_GIVEN)

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        try:
            metrics.log_metrics(ev.metrics)
            usage_collector.collect(ev.metrics)
        except Exception:
            logger.warning("Failed processing metrics", exc_info=True)

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(ev: UserInputTranscribedEvent):
        logger.debug(f"User transcribed (final={ev.is_final}): {ev.transcript[:120]}")

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev: ConversationItemAddedEvent):
        try:
            logger.info(
                "History item committed",
                extra={
                    "role": str(ev.item.role),
                    "text_preview": (ev.item.text_content or "").strip()[:120],
                    "interrupted": getattr(ev.item, "interrupted", False),
                },
            )
        except Exception:
            logger.warning("conversation_item_added handler failed", exc_info=True)

    # === NEW: IMMEDIATE EGRESS STOP EVENT HANDLERS ===
    @session.on("close")
    def _on_session_close(ev):
        """
        Handle immediate session close to stop egress recording immediately.
        This prevents the 20-30 second delay in recording termination.
        """
        logger.info(
            "Session closed, stopping egress immediately",
            extra={
                "reason": getattr(ev, "reason", "unknown"),
                "error": getattr(ev, "error", None),
            },
        )

        # Stop egress immediately when session closes
        egress_manager = ctx.proc.userdata.get("egress_manager")
        if egress_manager:
            try:
                # Use asyncio.create_task to run async cleanup without blocking
                background_tasks = ctx.proc.userdata["background_tasks"]
                task = asyncio.create_task(egress_manager.stop_recording())
                background_tasks.add(task)
                task.add_done_callback(background_tasks.discard)
                logger.info("Egress stop initiated on session close")
            except Exception as exc:
                logger.warning("Failed to stop egress on session close", exc_info=exc)

    @ctx.room.on("participant_disconnected")
    def _on_participant_disconnected(participant):
        """
        Handle participant disconnect to trigger immediate egress cleanup.
        This provides additional safety for recording termination.
        """
        logger.info(
            "Participant disconnected, checking for session end",
            extra={"participant": participant.identity},
        )

        # Check if this was the last user participant (non-agent)
        user_participants = [
            p
            for p in ctx.room.remote_participants.values()
            if getattr(p, "kind", None) != "agent"
        ]

        if len(user_participants) == 0:
            logger.info("Last user participant left, stopping egress")
            egress_manager = ctx.proc.userdata.get("egress_manager")
            if egress_manager:
                try:
                    background_tasks = ctx.proc.userdata["background_tasks"]
                    task = asyncio.create_task(egress_manager.stop_recording())
                    background_tasks.add(task)
                    task.add_done_callback(background_tasks.discard)
                    logger.info("Egress stop initiated on last participant disconnect")
                except Exception as exc:
                    logger.warning(
                        "Failed to stop egress on participant disconnect", exc_info=exc
                    )

    async def _log_usage_summary():
        try:
            summary = usage_collector.get_summary()
            logger.info("Usage summary", extra={"usage_summary": summary})
        except Exception:
            logger.warning("Failed to collect usage summary", exc_info=True)

    async def _send_shutdown_report():
        """Build and send the end-call report with the final transcript."""
        end_call_webhook = os.getenv("END_CALL_WEBHOOK_URL")
        if not end_call_webhook:
            logger.info("END_CALL_WEBHOOK_URL not set; skipping end-call report")
            return

        try:
            start_iso = ctx.proc.userdata["run_meta"]["start_time"]
            start_dt = datetime.fromisoformat(start_iso)
            session_duration = (datetime.now() - start_dt).total_seconds()
            min_duration = float(os.getenv("MIN_SESSION_SECONDS_FOR_REPORT", "5"))
            duration_ok = session_duration >= min_duration

            history_dict = session.history.to_dict()
            messages = history_dict.get("messages", [])
            has_user_activity = any(
                m.get("role") == "user" and (m.get("text") or "").strip()
                for m in messages
            )

            logger.info(
                "End-call criteria",
                extra={
                    "session_duration": session_duration,
                    "min_required": min_duration,
                    "duration_ok": duration_ok,
                    "history_messages": len(messages),
                    "has_user_activity": has_user_activity,
                },
            )

            if not (duration_ok or has_user_activity):
                logger.info("Skipping end-call report due to low activity/duration")
                return

            session_data = await _build_end_call_payload(ctx, session, usage_collector)
            session_data["transcript"] = history_dict  # attach canonical transcript
            await send_end_call_report(end_call_webhook, session_data)
            logger.info("End-call report sent")
        except Exception:
            logger.exception("Failed to send end-call report", exc_info=True)

    ctx.add_shutdown_callback(_log_usage_summary)
    ctx.add_shutdown_callback(_send_shutdown_report)

    # --- Start egress BEFORE connect ---
    async def _maybe_start_egress():
        try:
            egress_manager = EgressManager(ctx.room.name)
            ctx.proc.userdata["egress_manager"] = egress_manager
            recording_metadata = await egress_manager.start_recording()
            if recording_metadata:
                ctx.proc.userdata["recording"] = recording_metadata
                logger.info(
                    "Egress started",
                    extra={
                        "egress_id": recording_metadata.get("egress_id"),
                        "mode": recording_metadata.get("mode"),
                        "bucket": recording_metadata.get("bucket"),
                        "recording_url": recording_metadata.get("recording_url"),
                    },
                )
            else:
                logger.warning("Egress not started: no metadata")
        except Exception:
            logger.exception("Failed to start egress", exc_info=True)

    await _maybe_start_egress()

    # Connect and start publishing
    await ctx.connect()

    await session.start(
        agent=Assistant(telephony_manager=telephony_manager),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC()
        ),
        room_output_options=RoomOutputOptions(sync_transcription=False),
    )

    greeting = "Hello, thanks for calling. How can I help you today?"
    logger.info("Starting agent session; sending greeting")
    await session.say(greeting, allow_interruptions=False, add_to_chat_ctx=True)

    # --- UPDATED: Shutdown cleanup with state checking ---
    async def _cleanup_resources_on_shutdown():
        # Egress cleanup with check for already stopped
        egress_manager = ctx.proc.userdata.get("egress_manager")
        if egress_manager:
            try:
                # Check if egress is already stopped to avoid duplicate calls
                if (
                    not hasattr(egress_manager, "_is_stopped")
                    or not egress_manager._is_stopped
                ):
                    logger.info("Stopping egress (shutdown cleanup)")
                    await egress_manager.stop_recording()
                else:
                    logger.info("Egress already stopped, skipping shutdown stop")
                await egress_manager.cleanup()
            except Exception:
                logger.exception("Egress cleanup failed", exc_info=True)

        # Telephony cleanup
        tm = ctx.proc.userdata.get("telephony_manager")
        if tm:
            try:
                logger.info("Cleaning up telephony resources")
                await tm.cleanup()
            except Exception:
                logger.exception("Telephony cleanup failed", exc_info=True)

    ctx.add_shutdown_callback(_cleanup_resources_on_shutdown)

    await asyncio.Event().wait()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            shutdown_process_timeout=60,
        )
    )
