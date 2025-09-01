import logging
import os
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass

import aiohttp
from livekit import api

logger = logging.getLogger("webhook_handler")


@dataclass
class InboundCallRequest:
    """Data structure for inbound call webhook requests."""
    phone_number: str
    caller_id: Optional[str] = None
    call_id: Optional[str] = None
    room_name: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class WebhookHandler:
    """
    Handles webhook requests for inbound calls and agent management.
    
    This class processes webhook notifications from Twilio and other services
    to route inbound calls to the appropriate LiveKit agent rooms.
    
    Key Features:
    - Inbound call routing to LiveKit rooms
    - Agent session management
    - Call metadata tracking
    - Integration with TelephonyManager
    """
    
    def __init__(self, lkapi: api.LiveKitAPI):
        """
        Initialize the webhook handler.
        
        Args:
            lkapi: LiveKit API client instance
        """
        self.lkapi = lkapi
        self.agent_room_prefix = os.getenv("AGENT_ROOM_PREFIX", "agent_call")
        self.default_agent_instructions = os.getenv("DEFAULT_AGENT_INSTRUCTIONS", "")
    
    async def handle_inbound_call_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle inbound call webhook from Twilio or other SIP providers.
        
        Args:
            webhook_data: Webhook payload containing call information
            
        Returns:
            Dict containing processing results
        """
        try:
            # Parse webhook data
            call_request = self._parse_inbound_call_webhook(webhook_data)
            
            logger.info(
                "Processing inbound call webhook",
                extra={
                    "phone_number": call_request.phone_number,
                    "caller_id": call_request.caller_id,
                    "call_id": call_request.call_id
                }
            )
            
            # Generate room name if not provided
            if not call_request.room_name:
                call_request.room_name = self._generate_room_name(call_request.phone_number)
            
            # Create the room if it doesn't exist
            await self._ensure_room_exists(call_request.room_name)
            
            # Start agent session in the room
            agent_session_result = await self._start_agent_session(call_request)
            
            logger.info(
                "Inbound call webhook processed successfully",
                extra={
                    "call_id": call_request.call_id,
                    "room_name": call_request.room_name,
                    "phone_number": call_request.phone_number
                }
            )
            
            return {
                "status": "success",
                "call_id": call_request.call_id,
                "room_name": call_request.room_name,
                "phone_number": call_request.phone_number,
                "agent_session_started": agent_session_result.get("session_started", False)
            }
            
        except Exception as exc:
            logger.exception(
                "Failed to process inbound call webhook",
                extra={
                    "webhook_data": webhook_data,
                    "error": str(exc)
                }
            )
            
            return {
                "status": "error",
                "error": str(exc),
                "webhook_data": webhook_data
            }
    
    def _parse_inbound_call_webhook(self, webhook_data: Dict[str, Any]) -> InboundCallRequest:
        """
        Parse webhook data into structured format.
        
        Args:
            webhook_data: Raw webhook payload
            
        Returns:
            InboundCallRequest: Parsed call request
        """
        # Handle different webhook formats
        if "From" in webhook_data:  # Twilio format
            phone_number = webhook_data.get("From", "")
            caller_id = webhook_data.get("CallerName", "")
            call_id = webhook_data.get("CallSid", "")
        elif "phone_number" in webhook_data:  # Generic format
            phone_number = webhook_data.get("phone_number", "")
            caller_id = webhook_data.get("caller_id", "")
            call_id = webhook_data.get("call_id", "")
        else:
            raise ValueError("Unsupported webhook format")
        
        # Generate call ID if not provided
        if not call_id:
            call_id = f"inbound_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{phone_number}"
        
        return InboundCallRequest(
            phone_number=phone_number,
            caller_id=caller_id,
            call_id=call_id,
            room_name=webhook_data.get("room_name"),
            metadata=webhook_data
        )
    
    def _generate_room_name(self, phone_number: str) -> str:
        """
        Generate a unique room name for the call.
        
        Args:
            phone_number: Phone number that called in
            
        Returns:
            str: Generated room name
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        clean_number = phone_number.replace('+', '').replace('-', '').replace(' ', '')
        return f"{self.agent_room_prefix}_{timestamp}_{clean_number}"
    
    async def _ensure_room_exists(self, room_name: str) -> None:
        """
        Ensure the LiveKit room exists.
        
        Args:
            room_name: Name of the room to create/verify
        """
        try:
            # Try to get room info to check if it exists
            await self.lkapi.room.get_room(api.GetRoomRequest(room=room_name))
            logger.debug(f"Room {room_name} already exists")
        except Exception:
            # Room doesn't exist, create it
            await self.lkapi.room.create_room(
                api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=300,  # 5 minutes
                    max_participants=10
                )
            )
            logger.info(f"Created new room: {room_name}")
    
    async def _start_agent_session(self, call_request: InboundCallRequest) -> Dict[str, Any]:
        """
        Start an agent session in the specified room.
        
        Args:
            call_request: Inbound call request details
            
        Returns:
            Dict containing session start results
        """
        try:
            # This would typically start the agent worker process
            # For now, we'll just log that the session should be started
            logger.info(
                "Agent session should be started",
                extra={
                    "room_name": call_request.room_name,
                    "call_id": call_request.call_id,
                    "phone_number": call_request.phone_number
                }
            )
            
            # In a production environment, you might:
            # 1. Start a new agent worker process
            # 2. Send a message to an existing agent pool
            # 3. Use a job queue system
            
            return {
                "session_started": True,
                "room_name": call_request.room_name,
                "call_id": call_request.call_id
            }
            
        except Exception as exc:
            logger.exception(
                "Failed to start agent session",
                extra={
                    "room_name": call_request.room_name,
                    "call_id": call_request.call_id,
                    "error": str(exc)
                }
            )
            
            return {
                "session_started": False,
                "error": str(exc)
            }
    
    async def handle_agent_status_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle agent status update webhooks.
        
        Args:
            webhook_data: Webhook payload containing agent status
            
        Returns:
            Dict containing processing results
        """
        try:
            logger.info(
                "Processing agent status webhook",
                extra={"webhook_data": webhook_data}
            )
            
            # Process agent status updates
            # This could include:
            # - Agent availability updates
            # - Call completion notifications
            # - Performance metrics
            
            return {
                "status": "success",
                "processed": True
            }
            
        except Exception as exc:
            logger.exception(
                "Failed to process agent status webhook",
                extra={
                    "webhook_data": webhook_data,
                    "error": str(exc)
                }
            )
            
            return {
                "status": "error",
                "error": str(exc)
            }
    
    async def handle_call_completion_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle call completion webhooks.
        
        Args:
            webhook_data: Webhook payload containing call completion data
            
        Returns:
            Dict containing processing results
        """
        try:
            logger.info(
                "Processing call completion webhook",
                extra={"webhook_data": webhook_data}
            )
            
            # Process call completion
            # This could include:
            # - Updating call records
            # - Triggering follow-up actions
            # - Sending notifications
            
            return {
                "status": "success",
                "processed": True
            }
            
        except Exception as exc:
            logger.exception(
                "Failed to process call completion webhook",
                extra={
                    "webhook_data": webhook_data,
                    "error": str(exc)
                }
            )
            
            return {
                "status": "error",
                "error": str(exc)
            }
