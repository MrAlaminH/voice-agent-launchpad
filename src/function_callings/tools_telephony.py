import os
import re
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from livekit.agents.llm import function_tool
from livekit.agents import RunContext

logger = logging.getLogger("agent.telephony")


def _validate_phone_number(phone_number: str) -> bool:
    """
    Validate phone number format.
    
    Args:
        phone_number: Phone number to validate
        
    Returns:
        bool: True if valid phone number format
    """
    # Remove all non-digit characters
    digits_only = re.sub(r'\D', '', phone_number)
    
    # Check if it's a valid length (7-15 digits)
    if len(digits_only) < 7 or len(digits_only) > 15:
        return False
    
    # Check if it starts with a valid country code or area code
    if len(digits_only) == 10:  # US/Canada format
        return True
    elif len(digits_only) == 11 and digits_only.startswith('1'):  # US/Canada with country code
        return True
    elif len(digits_only) >= 10:  # International format
        return True
    
    return False


def _normalize_phone_number(phone_number: str) -> str:
    """
    Normalize phone number to E.164 format.
    
    Args:
        phone_number: Phone number to normalize
        
    Returns:
        str: Normalized phone number
    """
    # Remove all non-digit characters
    digits_only = re.sub(r'\D', '', phone_number)
    
    # Handle US/Canada numbers
    if len(digits_only) == 10:
        return f"+1{digits_only}"
    elif len(digits_only) == 11 and digits_only.startswith('1'):
        return f"+{digits_only}"
    
    # For international numbers, assume they're already in correct format
    # but add + if missing
    if not digits_only.startswith('+'):
        return f"+{digits_only}"
    
    return digits_only


class TelephonyTools:
    """
    Telephony tools for LiveKit agents to make and manage phone calls.
    
    This class provides tools for:
    - Making outbound calls to phone numbers
    - Managing active calls
    - Getting call status and information
    - Ending calls programmatically
    
    The tools integrate with the TelephonyManager to provide
    seamless phone call capabilities to the AI agent.
    """
    
    def __init__(self, telephony_manager=None):
        """
        Initialize telephony tools.
        
        Args:
            telephony_manager: TelephonyManager instance for call operations
        """
        self.telephony_manager = telephony_manager
    
    @function_tool(
        name="make_outbound_call",
        description="Make an outbound phone call to a specified number. Use this when the user wants to call someone or when you need to initiate a call on their behalf."
    )
    async def make_outbound_call(
        self,
        context: RunContext,
        phone_number: str,
        purpose: str,
        agent_instructions: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Make an outbound call to a phone number.
        
        Args:
            phone_number: The phone number to call (can be in any format)
            purpose: The purpose of the call (e.g., "appointment reminder", "follow-up call")
            agent_instructions: Optional specific instructions for the agent during the call
            
        Returns:
            Dict containing call status and information
        """
        if not self.telephony_manager:
            return {
                "status": "error",
                "message": "Telephony manager not configured. Outbound calls are not available.",
                "call_id": None
            }
        
        # Validate phone number
        if not _validate_phone_number(phone_number):
            return {
                "status": "error",
                "message": f"The phone number '{phone_number}' appears to be invalid. Please provide a valid phone number.",
                "call_id": None
            }
        
        # Normalize phone number
        normalized_number = _normalize_phone_number(phone_number)
        
        try:
            # Generate room name for the call
            room_name = f"outbound_call_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{normalized_number.replace('+', '')}"
            
            # Make the outbound call
            call_metadata = await self.telephony_manager.make_outbound_call(
                phone_number=normalized_number,
                room_name=room_name,
                agent_instructions=agent_instructions
            )
            
            logger.info(
                "Outbound call initiated via agent tool",
                extra={
                    "call_id": call_metadata.call_id,
                    "phone_number": normalized_number,
                    "purpose": purpose,
                    "room_name": room_name
                }
            )
            
            return {
                "status": "success",
                "message": f"Call initiated to {normalized_number} for {purpose}. The call is now ringing.",
                "call_id": call_metadata.call_id,
                "phone_number": normalized_number,
                "room_name": room_name,
                "call_status": call_metadata.status.value
            }
            
        except Exception as exc:
            logger.exception(
                "Failed to make outbound call via agent tool",
                extra={
                    "phone_number": normalized_number,
                    "purpose": purpose,
                    "error": str(exc)
                }
            )
            
            return {
                "status": "error",
                "message": f"Failed to initiate call to {normalized_number}. Please try again later.",
                "call_id": None,
                "error": str(exc)
            }
    
    @function_tool(
        name="get_call_status",
        description="Get the current status and information about an active call. Use this to check if a call is connected, ringing, or completed."
    )
    async def get_call_status(
        self,
        context: RunContext,
        call_id: str,
    ) -> Dict[str, Any]:
        """
        Get status and information about an active call.
        
        Args:
            call_id: The ID of the call to check
            
        Returns:
            Dict containing call status and information
        """
        if not self.telephony_manager:
            return {
                "status": "error",
                "message": "Telephony manager not configured.",
                "call_id": call_id
            }
        
        call_metadata = self.telephony_manager.get_call_metadata(call_id)
        
        if not call_metadata:
            return {
                "status": "error",
                "message": f"Call {call_id} not found or no longer active.",
                "call_id": call_id
            }
        
        return {
            "status": "success",
            "call_id": call_metadata.call_id,
            "phone_number": call_metadata.phone_number,
            "call_status": call_metadata.status.value,
            "direction": call_metadata.direction.value,
            "start_time": call_metadata.start_time.isoformat() if call_metadata.start_time else None,
            "duration_seconds": call_metadata.duration_seconds,
            "room_name": call_metadata.room_name,
            "transcript_entries": len(call_metadata.transcript)
        }
    
    @function_tool(
        name="end_call",
        description="End an active phone call. Use this when the user wants to hang up or when the call purpose has been completed."
    )
    async def end_call(
        self,
        context: RunContext,
        call_id: str,
    ) -> Dict[str, Any]:
        """
        End an active call.
        
        Args:
            call_id: The ID of the call to end
            
        Returns:
            Dict containing end call status
        """
        if not self.telephony_manager:
            return {
                "status": "error",
                "message": "Telephony manager not configured.",
                "call_id": call_id
            }
        
        try:
            success = await self.telephony_manager.end_call(call_id)
            
            if success:
                logger.info(
                    "Call ended via agent tool",
                    extra={"call_id": call_id}
                )
                
                return {
                    "status": "success",
                    "message": f"Call {call_id} has been ended successfully.",
                    "call_id": call_id
                }
            else:
                return {
                    "status": "error",
                    "message": f"Failed to end call {call_id}. Call may not be active.",
                    "call_id": call_id
                }
                
        except Exception as exc:
            logger.exception(
                "Failed to end call via agent tool",
                extra={
                    "call_id": call_id,
                    "error": str(exc)
                }
            )
            
            return {
                "status": "error",
                "message": f"Error ending call {call_id}: {str(exc)}",
                "call_id": call_id
            }
    
    @function_tool(
        name="list_active_calls",
        description="Get a list of all currently active calls. Use this to see what calls are in progress."
    )
    async def list_active_calls(
        self,
        context: RunContext,
    ) -> Dict[str, Any]:
        """
        Get a list of all active calls.
        
        Returns:
            Dict containing list of active calls
        """
        if not self.telephony_manager:
            return {
                "status": "error",
                "message": "Telephony manager not configured.",
                "active_calls": []
            }
        
        active_calls = self.telephony_manager.get_active_calls()
        
        calls_info = []
        for call in active_calls:
            calls_info.append({
                "call_id": call.call_id,
                "phone_number": call.phone_number,
                "status": call.status.value,
                "direction": call.direction.value,
                "start_time": call.start_time.isoformat() if call.start_time else None,
                "duration_seconds": call.duration_seconds,
                "room_name": call.room_name
            })
        
        return {
            "status": "success",
            "active_calls": calls_info,
            "total_calls": len(calls_info)
        }
    
    @function_tool(
        name="validate_phone_number",
        description="Validate and normalize a phone number format. Use this to check if a phone number is valid before making a call."
    )
    async def validate_phone_number(
        self,
        context: RunContext,
        phone_number: str,
    ) -> Dict[str, Any]:
        """
        Validate and normalize a phone number.
        
        Args:
            phone_number: The phone number to validate
            
        Returns:
            Dict containing validation results
        """
        is_valid = _validate_phone_number(phone_number)
        
        if is_valid:
            normalized = _normalize_phone_number(phone_number)
            return {
                "status": "success",
                "is_valid": True,
                "original_number": phone_number,
                "normalized_number": normalized,
                "message": f"Phone number '{phone_number}' is valid and normalized to '{normalized}'."
            }
        else:
            return {
                "status": "error",
                "is_valid": False,
                "original_number": phone_number,
                "message": f"Phone number '{phone_number}' appears to be invalid. Please provide a valid phone number."
            }
