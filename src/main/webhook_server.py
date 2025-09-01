import logging
import os
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from livekit import api
from pydantic import BaseModel

from .webhook_handler import WebhookHandler

logger = logging.getLogger("webhook_server")

# Initialize FastAPI app
app = FastAPI(
    title="LiveKit Agent Webhook Server",
    description="Webhook server for handling telephony and agent events",
    version="1.0.0",
)

# Global webhook handler
webhook_handler: WebhookHandler = None


class TwilioWebhookData(BaseModel):
    """Twilio webhook data model."""

    CallSid: str
    From: str
    To: str
    CallStatus: str
    CallerName: str = ""
    CallDuration: str = "0"
    RecordingUrl: str = ""
    RecordingSid: str = ""


class GenericWebhookData(BaseModel):
    """Generic webhook data model."""

    phone_number: str
    caller_id: str = ""
    call_id: str = ""
    room_name: str = ""
    event_type: str = "inbound_call"
    metadata: dict[str, Any] = {}


@app.on_event("startup")
async def startup_event():
    """Initialize webhook handler on startup."""
    global webhook_handler

    try:
        lkapi = api.LiveKitAPI()
        webhook_handler = WebhookHandler(lkapi)
        logger.info("Webhook server started successfully")
    except Exception as exc:
        logger.error(f"Failed to initialize webhook server: {exc}")
        raise RuntimeError("Failed to initialize webhook server") from exc


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Webhook server shutting down")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "livekit-agent-webhook-server",
    }


@app.post("/webhook/twilio/inbound")
async def handle_twilio_inbound_webhook(
    request: Request, background_tasks: BackgroundTasks
):
    """
    Handle inbound call webhooks from Twilio.
    """
    try:
        form_data = await request.form()
        webhook_data = dict(form_data)

        logger.info(
            "Received Twilio inbound webhook",
            extra={
                "call_sid": webhook_data.get("CallSid"),
                "from_number": webhook_data.get("From"),
                "call_status": webhook_data.get("CallStatus"),
            },
        )

        background_tasks.add_task(process_inbound_call_webhook, webhook_data)

        return JSONResponse(
            content={"status": "accepted", "message": "Call routing initiated"},
            status_code=200,
        )

    except Exception as exc:
        logger.exception("Failed to handle Twilio inbound webhook")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/webhook/generic/inbound")
async def handle_generic_inbound_webhook(
    webhook_data: GenericWebhookData, background_tasks: BackgroundTasks
):
    """
    Handle generic inbound call webhooks.
    """
    try:
        logger.info(
            "Received generic inbound webhook",
            extra={
                "phone_number": webhook_data.phone_number,
                "call_id": webhook_data.call_id,
                "event_type": webhook_data.event_type,
            },
        )

        background_tasks.add_task(process_inbound_call_webhook, webhook_data.dict())

        return {
            "status": "accepted",
            "message": "Call routing initiated",
            "call_id": webhook_data.call_id,
        }

    except Exception as exc:
        logger.exception("Failed to handle generic inbound webhook")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/webhook/call/completion")
async def handle_call_completion_webhook(
    request: Request, background_tasks: BackgroundTasks
):
    """
    Handle call completion webhooks.
    """
    try:
        webhook_data = await request.json()

        logger.info(
            "Received call completion webhook", extra={"webhook_data": webhook_data}
        )

        background_tasks.add_task(process_call_completion_webhook, webhook_data)

        return {"status": "accepted", "message": "Call completion processed"}

    except Exception as exc:
        logger.exception("Failed to handle call completion webhook")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/webhook/agent/status")
async def handle_agent_status_webhook(
    request: Request, background_tasks: BackgroundTasks
):
    """
    Handle agent status update webhooks.
    """
    try:
        webhook_data = await request.json()

        logger.info(
            "Received agent status webhook", extra={"webhook_data": webhook_data}
        )

        background_tasks.add_task(process_agent_status_webhook, webhook_data)

        return {"status": "accepted", "message": "Agent status update processed"}

    except Exception as exc:
        logger.exception("Failed to handle agent status webhook")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def process_inbound_call_webhook(webhook_data: dict[str, Any]):
    """Process inbound call webhook in background."""
    try:
        if not webhook_handler:
            logger.error("Webhook handler not initialized")
            return

        result = await webhook_handler.handle_inbound_call_webhook(webhook_data)

        if result.get("status") == "success":
            logger.info(
                "Inbound call webhook processed successfully",
                extra={
                    "call_id": result.get("call_id"),
                    "room_name": result.get("room_name"),
                },
            )
        else:
            logger.error(
                "Failed to process inbound call webhook", extra={"result": result}
            )

    except Exception:
        logger.exception("Error processing inbound call webhook")


async def process_call_completion_webhook(webhook_data: dict[str, Any]):
    """Process call completion webhook in background."""
    try:
        if not webhook_handler:
            logger.error("Webhook handler not initialized")
            return

        result = await webhook_handler.handle_call_completion_webhook(webhook_data)

        if result.get("status") == "success":
            logger.info("Call completion webhook processed successfully")
        else:
            logger.error(
                "Failed to process call completion webhook", extra={"result": result}
            )

    except Exception:
        logger.exception("Error processing call completion webhook")


async def process_agent_status_webhook(webhook_data: dict[str, Any]):
    """Process agent status webhook in background."""
    try:
        if not webhook_handler:
            logger.error("Webhook handler not initialized")
            return

        result = await webhook_handler.handle_agent_status_webhook(webhook_data)

        if result.get("status") == "success":
            logger.info("Agent status webhook processed successfully")
        else:
            logger.error(
                "Failed to process agent status webhook", extra={"result": result}
            )

    except Exception:
        logger.exception("Error processing agent status webhook")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("WEBHOOK_SERVER_PORT", "8000"))
    host = os.getenv("WEBHOOK_SERVER_HOST", "0.0.0.0")

    uvicorn.run(
        "webhook_server:app", host=host, port=port, reload=True, log_level="info"
    )
