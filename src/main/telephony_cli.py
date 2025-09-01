#!/usr/bin/env python3
"""
Telephony CLI Tool for LiveKit Agent

This tool provides command-line interface for testing and managing
telephony functionality including making outbound calls and checking
call status.
"""

import asyncio
import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Optional

from livekit import api
from .telephony_manager import TelephonyManager, CallDirection, CallStatus

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("telephony_cli")


class TelephonyCLI:
    """Command-line interface for telephony operations."""
    
    def __init__(self):
        """Initialize the CLI with LiveKit API client."""
        try:
            self.lkapi = api.LiveKitAPI()
            self.telephony_manager = TelephonyManager(self.lkapi)
            logger.info("Telephony CLI initialized successfully")
        except Exception as exc:
            logger.error(f"Failed to initialize telephony CLI: {exc}")
            sys.exit(1)
    
    async def make_call(self, phone_number: str, purpose: str, instructions: Optional[str] = None):
        """Make an outbound call."""
        try:
            logger.info(f"Making outbound call to {phone_number}")
            
            # Generate room name
            room_name = f"cli_call_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{phone_number.replace('+', '')}"
            
            # Make the call
            call_metadata = await self.telephony_manager.make_outbound_call(
                phone_number=phone_number,
                room_name=room_name,
                agent_instructions=instructions
            )
            
            print(f"✅ Call initiated successfully!")
            print(f"   Call ID: {call_metadata.call_id}")
            print(f"   Phone Number: {call_metadata.phone_number}")
            print(f"   Room Name: {call_metadata.room_name}")
            print(f"   Status: {call_metadata.status.value}")
            print(f"   Purpose: {purpose}")
            
            if instructions:
                print(f"   Instructions: {instructions}")
            
            return call_metadata.call_id
            
        except Exception as exc:
            logger.error(f"Failed to make call: {exc}")
            print(f"❌ Failed to make call: {exc}")
            return None
    
    async def check_call_status(self, call_id: str):
        """Check the status of a call."""
        try:
            call_metadata = self.telephony_manager.get_call_metadata(call_id)
            
            if not call_metadata:
                print(f"❌ Call {call_id} not found")
                return
            
            print(f"📞 Call Status: {call_metadata.call_id}")
            print(f"   Phone Number: {call_metadata.phone_number}")
            print(f"   Direction: {call_metadata.direction.value}")
            print(f"   Status: {call_metadata.status.value}")
            print(f"   Room Name: {call_metadata.room_name}")
            
            if call_metadata.start_time:
                print(f"   Start Time: {call_metadata.start_time.isoformat()}")
            
            if call_metadata.duration_seconds:
                print(f"   Duration: {call_metadata.duration_seconds} seconds")
            
            if call_metadata.transcript:
                print(f"   Transcript Entries: {len(call_metadata.transcript)}")
            
            if call_metadata.metadata:
                print(f"   Metadata: {call_metadata.metadata}")
                
        except Exception as exc:
            logger.error(f"Failed to check call status: {exc}")
            print(f"❌ Failed to check call status: {exc}")
    
    async def list_calls(self):
        """List all active calls."""
        try:
            active_calls = self.telephony_manager.get_active_calls()
            
            if not active_calls:
                print("📞 No active calls")
                return
            
            print(f"📞 Active Calls ({len(active_calls)}):")
            print("-" * 80)
            
            for call in active_calls:
                print(f"Call ID: {call.call_id}")
                print(f"  Phone Number: {call.phone_number}")
                print(f"  Direction: {call.direction.value}")
                print(f"  Status: {call.status.value}")
                print(f"  Room: {call.room_name}")
                
                if call.start_time:
                    duration = ""
                    if call.duration_seconds:
                        duration = f" ({call.duration_seconds}s)"
                    print(f"  Started: {call.start_time.strftime('%H:%M:%S')}{duration}")
                
                print()
                
        except Exception as exc:
            logger.error(f"Failed to list calls: {exc}")
            print(f"❌ Failed to list calls: {exc}")
    
    async def end_call(self, call_id: str):
        """End a call."""
        try:
            logger.info(f"Ending call {call_id}")
            
            success = await self.telephony_manager.end_call(call_id)
            
            if success:
                print(f"✅ Call {call_id} ended successfully")
            else:
                print(f"❌ Failed to end call {call_id}")
                
        except Exception as exc:
            logger.error(f"Failed to end call: {exc}")
            print(f"❌ Failed to end call: {exc}")
    
    async def test_webhook(self, webhook_url: str, phone_number: str):
        """Test webhook endpoint with sample data."""
        try:
            import aiohttp
            
            # Sample webhook data
            webhook_data = {
                "CallSid": f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "From": phone_number,
                "To": "+1234567890",
                "CallStatus": "ringing",
                "CallerName": "Test Caller"
            }
            
            logger.info(f"Testing webhook endpoint: {webhook_url}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    data=webhook_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        print(f"✅ Webhook test successful!")
                        print(f"   Status: {response.status}")
                        print(f"   Response: {result}")
                    else:
                        print(f"❌ Webhook test failed: {response.status}")
                        print(f"   Response: {await response.text()}")
                        
        except Exception as exc:
            logger.error(f"Failed to test webhook: {exc}")
            print(f"❌ Failed to test webhook: {exc}")
    
    async def cleanup(self):
        """Cleanup resources."""
        try:
            await self.telephony_manager.cleanup()
            logger.info("Telephony CLI cleanup completed")
        except Exception as exc:
            logger.error(f"Failed to cleanup: {exc}")


async def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="LiveKit Agent Telephony CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Make an outbound call
  python telephony_cli.py call +1234567890 "appointment reminder" "Remind about tomorrow's appointment"
  
  # Check call status
  python telephony_cli.py status call_1234567890
  
  # List active calls
  python telephony_cli.py list
  
  # End a call
  python telephony_cli.py end call_1234567890
  
  # Test webhook
  python telephony_cli.py test-webhook https://your-domain.com/webhook/twilio/inbound +1234567890
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Call command
    call_parser = subparsers.add_parser("call", help="Make an outbound call")
    call_parser.add_argument("phone_number", help="Phone number to call")
    call_parser.add_argument("purpose", help="Purpose of the call")
    call_parser.add_argument("--instructions", help="Instructions for the agent")
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Check call status")
    status_parser.add_argument("call_id", help="Call ID to check")
    
    # List command
    subparsers.add_parser("list", help="List active calls")
    
    # End command
    end_parser = subparsers.add_parser("end", help="End a call")
    end_parser.add_argument("call_id", help="Call ID to end")
    
    # Test webhook command
    webhook_parser = subparsers.add_parser("test-webhook", help="Test webhook endpoint")
    webhook_parser.add_argument("webhook_url", help="Webhook URL to test")
    webhook_parser.add_argument("phone_number", help="Phone number for test")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Initialize CLI
    cli = TelephonyCLI()
    
    try:
        if args.command == "call":
            await cli.make_call(args.phone_number, args.purpose, args.instructions)
        elif args.command == "status":
            await cli.check_call_status(args.call_id)
        elif args.command == "list":
            await cli.list_calls()
        elif args.command == "end":
            await cli.end_call(args.call_id)
        elif args.command == "test-webhook":
            await cli.test_webhook(args.webhook_url, args.phone_number)
        else:
            parser.print_help()
            
    except KeyboardInterrupt:
        print("\n🛑 Operation cancelled by user")
    except Exception as exc:
        logger.error(f"CLI error: {exc}")
        print(f"❌ CLI error: {exc}")
    finally:
        await cli.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
