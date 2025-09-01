import logging
import os
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

import aiohttp
from livekit import api

logger = logging.getLogger("telephony_manager")


class CallDirection(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallStatus(Enum):
    INITIATED = "initiated"
    RINGING = "ringing"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    NO_ANSWER = "no_answer"


@dataclass
class CallMetadata:
    """Metadata for tracking call information."""
    call_id: str
    direction: CallDirection
    phone_number: str
    room_name: str
    sip_participant_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: CallStatus = CallStatus.INITIATED
    duration_seconds: Optional[int] = None
    recording_url: Optional[str] = None
    transcript: List[Dict[str, Any]] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.transcript is None:
            self.transcript = []
        if self.metadata is None:
            self.metadata = {}


class TelephonyManager:
    """
    Manages telephony integration for LiveKit agents using Twilio SIP.
    
    This class handles:
    - Inbound call routing and management
    - Outbound call initiation and tracking
    - SIP participant creation and management
    - Call metadata tracking and webhook integration
    - Integration with existing agent infrastructure
    
    Key Features:
    - Seamless integration with existing agent session
    - Comprehensive call tracking and analytics
    - Support for both inbound and outbound calls
    - Integration with existing egress and webhook systems
    - Professional-grade error handling and logging
    """
    
    def __init__(self, lkapi: api.LiveKitAPI):
        """
        Initialize the TelephonyManager.
        
        Args:
            lkapi: LiveKit API client instance
        """
        self.lkapi = lkapi
        self.active_calls: Dict[str, CallMetadata] = {}
        self.sip_trunk_id = os.getenv("TWILIO_SIP_TRUNK_ID")
        self.outbound_trunk_id = os.getenv("TWILIO_OUTBOUND_TRUNK_ID")
        self.webhook_url = os.getenv("CALL_WEBHOOK_URL")
        
        # Validate required configuration
        if not self.sip_trunk_id:
            logger.warning("TWILIO_SIP_TRUNK_ID not set - inbound calls disabled")
        if not self.outbound_trunk_id:
            logger.warning("TWILIO_OUTBOUND_TRUNK_ID not set - outbound calls disabled")
    
    async def handle_inbound_call(
        self, 
        phone_number: str, 
        room_name: str,
        caller_id: Optional[str] = None,
        call_id: Optional[str] = None
    ) -> CallMetadata:
        """
        Handle an inbound call from a phone number.
        
        Args:
            phone_number: The phone number that called in
            room_name: LiveKit room name for the call
            caller_id: Optional caller ID information
            call_id: Optional custom call ID
            
        Returns:
            CallMetadata: Call tracking information
        """
        if not self.sip_trunk_id:
            raise ValueError("SIP trunk not configured for inbound calls")
        
        call_id = call_id or f"inbound_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{phone_number}"
        
        call_metadata = CallMetadata(
            call_id=call_id,
            direction=CallDirection.INBOUND,
            phone_number=phone_number,
            room_name=room_name,
            start_time=datetime.now(),
            metadata={
                "caller_id": caller_id,
                "source": "twilio_sip"
            }
        )
        
        try:
            logger.info(
                "Handling inbound call",
                extra={
                    "call_id": call_id,
                    "phone_number": phone_number,
                    "room_name": room_name,
                    "caller_id": caller_id
                }
            )
            
            # Create SIP participant in the room
            sip_participant = await self._create_sip_participant(
                room_name=room_name,
                phone_number=phone_number,
                trunk_id=self.sip_trunk_id
            )
            
            call_metadata.sip_participant_id = sip_participant.participant_id
            call_metadata.status = CallStatus.CONNECTED
            
            # Store call metadata
            self.active_calls[call_id] = call_metadata
            
            # Send webhook notification
            await self._send_call_webhook(call_metadata, "call_started")
            
            logger.info(
                "Inbound call connected successfully",
                extra={
                    "call_id": call_id,
                    "sip_participant_id": sip_participant.participant_id,
                    "room_name": room_name
                }
            )
            
            return call_metadata
            
        except Exception as exc:
            call_metadata.status = CallStatus.FAILED
            call_metadata.end_time = datetime.now()
            logger.exception(
                "Failed to handle inbound call",
                extra={
                    "call_id": call_id,
                    "phone_number": phone_number,
                    "error": str(exc)
                }
            )
            raise
    
    async def make_outbound_call(
        self,
        phone_number: str,
        room_name: str,
        agent_instructions: Optional[str] = None,
        call_id: Optional[str] = None
    ) -> CallMetadata:
        """
        Make an outbound call to a phone number.
        
        Args:
            phone_number: The phone number to call
            room_name: LiveKit room name for the call
            agent_instructions: Optional instructions for the agent
            call_id: Optional custom call ID
            
        Returns:
            CallMetadata: Call tracking information
        """
        if not self.outbound_trunk_id:
            raise ValueError("Outbound trunk not configured for outbound calls")
        
        call_id = call_id or f"outbound_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{phone_number}"
        
        call_metadata = CallMetadata(
            call_id=call_id,
            direction=CallDirection.OUTBOUND,
            phone_number=phone_number,
            room_name=room_name,
            start_time=datetime.now(),
            metadata={
                "agent_instructions": agent_instructions,
                "source": "twilio_sip"
            }
        )
        
        try:
            logger.info(
                "Initiating outbound call",
                extra={
                    "call_id": call_id,
                    "phone_number": phone_number,
                    "room_name": room_name
                }
            )
            
            # Create SIP participant for outbound call
            sip_participant = await self._create_sip_participant(
                room_name=room_name,
                phone_number=phone_number,
                trunk_id=self.outbound_trunk_id
            )
            
            call_metadata.sip_participant_id = sip_participant.participant_id
            call_metadata.status = CallStatus.RINGING
            
            # Store call metadata
            self.active_calls[call_id] = call_metadata
            
            # Send webhook notification
            await self._send_call_webhook(call_metadata, "call_initiated")
            
            logger.info(
                "Outbound call initiated successfully",
                extra={
                    "call_id": call_id,
                    "sip_participant_id": sip_participant.participant_id,
                    "room_name": room_name
                }
            )
            
            return call_metadata
            
        except Exception as exc:
            call_metadata.status = CallStatus.FAILED
            call_metadata.end_time = datetime.now()
            logger.exception(
                "Failed to initiate outbound call",
                extra={
                    "call_id": call_id,
                    "phone_number": phone_number,
                    "error": str(exc)
                }
            )
            raise
    
    async def end_call(self, call_id: str) -> bool:
        """
        End an active call.
        
        Args:
            call_id: The ID of the call to end
            
        Returns:
            bool: True if call was ended successfully
        """
        if call_id not in self.active_calls:
            logger.warning(f"Call {call_id} not found in active calls")
            return False
        
        call_metadata = self.active_calls[call_id]
        
        try:
            if call_metadata.sip_participant_id:
                # Remove SIP participant from room
                await self.lkapi.room.remove_participant(
                    api.RemoveParticipantRequest(
                        room=call_metadata.room_name,
                        participant=call_metadata.sip_participant_id
                    )
                )
                
                logger.info(
                    "SIP participant removed from room",
                    extra={
                        "call_id": call_id,
                        "sip_participant_id": call_metadata.sip_participant_id,
                        "room_name": call_metadata.room_name
                    }
                )
            
            # Update call metadata
            call_metadata.end_time = datetime.now()
            call_metadata.status = CallStatus.COMPLETED
            if call_metadata.start_time:
                call_metadata.duration_seconds = int(
                    (call_metadata.end_time - call_metadata.start_time).total_seconds()
                )
            
            # Send webhook notification
            await self._send_call_webhook(call_metadata, "call_ended")
            
            # Remove from active calls
            del self.active_calls[call_id]
            
            logger.info(
                "Call ended successfully",
                extra={
                    "call_id": call_id,
                    "duration_seconds": call_metadata.duration_seconds,
                    "phone_number": call_metadata.phone_number
                }
            )
            
            return True
            
        except Exception as exc:
            logger.exception(
                "Failed to end call",
                extra={
                    "call_id": call_id,
                    "error": str(exc)
                }
            )
            return False
    
    async def update_call_status(self, call_id: str, status: CallStatus, **kwargs) -> bool:
        """
        Update the status of an active call.
        
        Args:
            call_id: The ID of the call to update
            status: New call status
            **kwargs: Additional metadata to update
            
        Returns:
            bool: True if update was successful
        """
        if call_id not in self.active_calls:
            logger.warning(f"Call {call_id} not found in active calls")
            return False
        
        call_metadata = self.active_calls[call_id]
        call_metadata.status = status
        
        # Update additional metadata
        for key, value in kwargs.items():
            if hasattr(call_metadata, key):
                setattr(call_metadata, key, value)
            else:
                call_metadata.metadata[key] = value
        
        logger.info(
            "Call status updated",
            extra={
                "call_id": call_id,
                "status": status.value,
                "phone_number": call_metadata.phone_number
            }
        )
        
        return True
    
    async def add_call_transcript(self, call_id: str, transcript_entry: Dict[str, Any]) -> bool:
        """
        Add a transcript entry to a call.
        
        Args:
            call_id: The ID of the call
            transcript_entry: Transcript entry with role, text, timestamp
            
        Returns:
            bool: True if transcript was added successfully
        """
        if call_id not in self.active_calls:
            logger.warning(f"Call {call_id} not found in active calls")
            return False
        
        call_metadata = self.active_calls[call_id]
        call_metadata.transcript.append(transcript_entry)
        
        logger.debug(
            "Transcript entry added to call",
            extra={
                "call_id": call_id,
                "role": transcript_entry.get("role"),
                "text_length": len(transcript_entry.get("text", ""))
            }
        )
        
        return True
    
    def get_call_metadata(self, call_id: str) -> Optional[CallMetadata]:
        """Get metadata for a specific call."""
        return self.active_calls.get(call_id)
    
    def get_active_calls(self) -> List[CallMetadata]:
        """Get all active calls."""
        return list(self.active_calls.values())
    
    async def _create_sip_participant(
        self, 
        room_name: str, 
        phone_number: str, 
        trunk_id: str
    ):
        """
        Create a SIP participant in a LiveKit room.
        
        Args:
            room_name: LiveKit room name
            phone_number: Phone number to connect
            trunk_id: SIP trunk ID to use
            
        Returns:
            Created SIP participant response
        """
        try:
            response = await self.lkapi.room.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room=room_name,
                    sip_trunk_id=trunk_id,
                    phone_number=phone_number
                )
            )
            
            return response
            
        except Exception as exc:
            logger.exception(
                "Failed to create SIP participant",
                extra={
                    "room_name": room_name,
                    "phone_number": phone_number,
                    "trunk_id": trunk_id,
                    "error": str(exc)
                }
            )
            raise
    
    async def _send_call_webhook(self, call_metadata: CallMetadata, event_type: str) -> None:
        """
        Send webhook notification for call events.
        
        Args:
            call_metadata: Call metadata
            event_type: Type of event (call_started, call_ended, etc.)
        """
        if not self.webhook_url:
            return
        
        try:
            payload = {
                "event_type": event_type,
                "call_id": call_metadata.call_id,
                "direction": call_metadata.direction.value,
                "phone_number": call_metadata.phone_number,
                "room_name": call_metadata.room_name,
                "status": call_metadata.status.value,
                "start_time": call_metadata.start_time.isoformat() if call_metadata.start_time else None,
                "end_time": call_metadata.end_time.isoformat() if call_metadata.end_time else None,
                "duration_seconds": call_metadata.duration_seconds,
                "sip_participant_id": call_metadata.sip_participant_id,
                "transcript": call_metadata.transcript,
                "metadata": call_metadata.metadata,
                "timestamp": datetime.now().isoformat()
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                ) as response:
                    if response.status >= 400:
                        logger.warning(
                            "Webhook notification failed",
                            extra={
                                "status": response.status,
                                "call_id": call_metadata.call_id,
                                "event_type": event_type
                            }
                        )
                    else:
                        logger.debug(
                            "Webhook notification sent successfully",
                            extra={
                                "call_id": call_metadata.call_id,
                                "event_type": event_type
                            }
                        )
                        
        except Exception as exc:
            logger.exception(
                "Failed to send webhook notification",
                extra={
                    "call_id": call_metadata.call_id,
                    "event_type": event_type,
                    "error": str(exc)
                }
            )
    
    async def cleanup(self):
        """Clean up resources and end all active calls."""
        logger.info(f"Cleaning up {len(self.active_calls)} active calls")
        
        for call_id in list(self.active_calls.keys()):
            await self.end_call(call_id)
        
        logger.info("Telephony manager cleanup completed")
