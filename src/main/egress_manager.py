import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any
from livekit import api

logger = logging.getLogger("egress_manager")


class EgressManager:
    """
    Manages audio recording egress to S3/R2 storage with support for both MP4 and HLS formats.
    
    This class handles:
    - Starting and stopping LiveKit egress jobs
    - Building consistent filenames with timestamps
    - Constructing recording URLs for webhooks
    - Managing S3/R2 upload configuration
    - Providing fallback mechanisms for metadata retrieval
    
    Key Features:
    - Timestamp consistency: Uses a single timestamp for all filename generation
    - Fallback URL construction: Builds URLs even when LiveKit doesn't return filenames
    - Environment-based configuration: Supports both MP4 and HLS recording modes
    - Comprehensive error handling and logging
    """
    
    def __init__(self, room_name: str):
        """
        Initialize the EgressManager for a specific room.
        
        Args:
            room_name: The LiveKit room name for this recording session
            
        Key initialization:
        - Captures timestamp at creation for consistent filename generation
        - Prevents timestamp drift between LiveKit and our URL construction
        """
        self.room_name = room_name
        self.lkapi = None  # LiveKit API client (initialized when needed)
        self.egress_id = None  # LiveKit egress job ID
        self.recording_metadata = {}  # Recording metadata for webhooks
        # Store timestamp at creation to ensure consistency across all operations
        self.timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        
    async def start_recording(self) -> Optional[Dict[str, Any]]:
        """
        Start audio recording egress to S3/R2 storage.
        
        This method:
        1. Validates egress is enabled and S3 configuration is complete
        2. Initializes LiveKit API client
        3. Builds egress request (MP4 or HLS based on configuration)
        4. Starts the LiveKit egress job
        5. Extracts filename from response (if available)
        6. Builds comprehensive recording metadata with URL
        7. Returns metadata for webhook use
        
        Returns:
            Optional[Dict]: Recording metadata with keys:
                - provider: "s3"
                - egress_id: LiveKit egress job ID
                - filepath: Actual filename from LiveKit (may be None)
                - bucket: S3/R2 bucket name
                - endpoint: S3/R2 endpoint URL
                - recording_url: Playable URL for the recording
                - mode: "mp4" or "hls"
                - started_at: ISO timestamp when recording started
                
            None if egress is disabled, configuration is incomplete, or an error occurs
        """
        try:
            if not self._is_egress_enabled():
                logger.info("Egress is disabled, skipping recording start")
                return None
                
            if not self._validate_s3_config():
                logger.warning("S3 configuration incomplete, skipping egress")
                return None
            
            # Initialize LiveKit API client
            self.lkapi = api.LiveKitAPI()
            
            # Build egress request based on configuration
            req = self._build_egress_request()
            
            # Start the egress job
            logger.info("Starting egress job", extra={
                "room_name": self.room_name,
                "mode": "hls" if self._should_use_hls() else "mp4"
            })
            
            res = await self.lkapi.egress.start_room_composite_egress(req)
            self.egress_id = getattr(res, "egress_id", None)
            
            # Extract actual filename from response
            actual_filename = self._extract_filename_from_response(res)
            
            logger.debug("Extracted filename from response", extra={
                "actual_filename": actual_filename,
                "response_type": type(res).__name__,
                "use_hls": self._should_use_hls()
            })
            
            # Build recording metadata
            self.recording_metadata = self._build_recording_metadata(actual_filename)
            
            logger.info(
                "Egress started successfully",
                extra={
                    "egress_id": self.egress_id,
                    "actual_filename": actual_filename,
                    "bucket": os.getenv("S3_BUCKET"),
                    "mode": self.recording_metadata.get("mode"),
                    "recording_url": self.recording_metadata.get("recording_url")
                }
            )
            
            # Log successful egress start with metadata
            logger.info(
                "Egress metadata prepared",
                extra={
                    "egress_id": self.egress_id,
                    "timestamp_used": self.timestamp,
                    "actual_filename": actual_filename,
                    "recording_url": self.recording_metadata.get("recording_url")
                }
            )
            
            return self.recording_metadata
            
        except Exception as exc:
            logger.exception("Failed to start egress", exc_info=exc)
            return None
    
    async def stop_recording(self) -> bool:
        """
        Stop the active egress job.
        
        Returns:
            bool: True if stopped successfully, False otherwise
        """
        try:
            if not self.lkapi or not self.egress_id:
                logger.info("No active egress to stop")
                return True
            
            logger.info("Stopping egress", extra={"egress_id": self.egress_id})
            await self.lkapi.egress.stop_egress(
                api.StopEgressRequest(egress_id=self.egress_id)
            )
            
            logger.info("Egress stopped successfully", extra={"egress_id": self.egress_id})
            return True
            
        except Exception as exc:
            # Check if it's the expected "already completed" error
            if "EGRESS_COMPLETE cannot be stopped" in str(exc):
                logger.info("Egress already completed, no need to stop", extra={"egress_id": self.egress_id})
                return True
            else:
                logger.exception("Failed to stop egress", exc_info=exc)
                return False
    
    async def cleanup(self):
        """Clean up resources and close API client."""
        try:
            if self.lkapi:
                await self.lkapi.aclose()
                logger.debug("LiveKit API client closed")
        except Exception as exc:
            logger.exception("Failed to close LiveKit API client", exc_info=exc)
    
    def get_recording_metadata(self) -> Dict[str, Any]:
        """Get current recording metadata."""
        return self.recording_metadata.copy()
    
    def get_timestamp(self) -> str:
        """Get the consistent timestamp used for this recording session."""
        return self.timestamp
    
    def _is_egress_enabled(self) -> bool:
        """Check if egress is enabled via environment variable."""
        return os.getenv("ENABLE_EGRESS", "0") == "1"
    
    def _should_use_hls(self) -> bool:
        """Check if HLS mode should be used."""
        return os.getenv("EGRESS_USE_HLS", "0") in {"1", "true", "True"}
    
    def _validate_s3_config(self) -> bool:
        """Validate that required S3 configuration is present."""
        required_vars = ["S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        
        if missing_vars:
            logger.warning(f"Missing S3 configuration: {missing_vars}")
            return False
        
        return True
    
    def _build_egress_request(self) -> api.RoomCompositeEgressRequest:
        """Build the appropriate egress request based on configuration."""
        s3_kwargs = self._build_s3_kwargs()
        
        if not self._should_use_hls():
            # MP4 single file output
            filename = self._build_filename()
            return api.RoomCompositeEgressRequest(
                room_name=self.room_name,
                audio_only=True,
                file_outputs=[api.EncodedFileOutput(
                    file_type=api.EncodedFileType.MP4,
                    filepath=filename,
                    s3=api.S3Upload(**s3_kwargs),
                )],
            )
        else:
            # HLS segmented output
            filename_prefix = self._build_filename().rstrip(".mp4")
            # EGRESS_SEGMENT_DURATION: Controls segment length in seconds for HLS mode
            # - Shorter segments (2-4s): Faster start, more responsive streaming
            # - Longer segments (6-10s): Better compression, fewer files
            # - Only applies when EGRESS_USE_HLS=1 (MP4 mode ignores this setting)
            segment_duration = int(os.getenv("EGRESS_SEGMENT_DURATION", "2"))
            
            return api.RoomCompositeEgressRequest(
                room_name=self.room_name,
                audio_only=True,
                segment_outputs=[api.SegmentedFileOutput(
                    filename_prefix=filename_prefix,
                    playlist_name=os.getenv("EGRESS_PLAYLIST_NAME", "playlist.m3u8"),
                    live_playlist_name=os.getenv("EGRESS_LIVE_PLAYLIST_NAME", "live.m3u8"),
                    segment_duration=segment_duration,
                    s3=api.S3Upload(**s3_kwargs),
                )],
            )
    
    def _build_s3_kwargs(self) -> Dict[str, Any]:
        """Build S3 upload configuration from environment variables."""
        s3_kwargs = {
            "bucket": os.getenv("S3_BUCKET"),
            "region": os.getenv("S3_REGION", "auto"),
            "access_key": os.getenv("S3_ACCESS_KEY"),
            "secret": os.getenv("S3_SECRET_KEY"),
            "endpoint": os.getenv("S3_ENDPOINT"),
        }
        
        # Handle force_path_style if supported
        force_path = os.getenv("S3_FORCE_PATH_STYLE", "1") in {"1", "true", "True"}
        if hasattr(api.S3Upload, "force_path_style"):
            s3_kwargs["force_path_style"] = force_path
        
        return s3_kwargs
    
    def _build_filename(self) -> str:
        """
        Build filename with LiveKit placeholders using consistent timestamp.
        
        This method ensures timestamp consistency by using the timestamp captured
        at EgressManager creation, preventing drift between LiveKit's filename
        and our webhook URL construction.
        
        Supports custom S3_FILEPATH patterns with placeholders:
        - {room_name}: Replaced with the actual room name
        - {time}: Replaced with the consistent timestamp (YYYYMMDD-HHMMSS)
        
        Examples:
        - S3_FILEPATH="livekit/{room_name}-{time}.mp4" 
          → "livekit/playground-room-20250830-133731.mp4"
        - S3_FILEPATH="recordings/{room_name}/audio_{time}.mp4"
          → "recordings/playground-room/audio_20250830-133731.mp4"
        
        Returns:
            str: Filename with placeholders replaced using consistent timestamp
        """
        s3_filepath = os.getenv("S3_FILEPATH")
        
        if s3_filepath:
            # Replace placeholders in the custom filepath using stored timestamp
            filename = s3_filepath.replace("{room_name}", self.room_name)
            filename = filename.replace("{time}", self.timestamp)
            
            logger.debug(
                "Built filename from S3_FILEPATH",
                extra={
                    "s3_filepath": s3_filepath,
                    "room_name": self.room_name,
                    "timestamp": self.timestamp,
                    "generated_filename": filename
                }
            )
            return filename
        else:
            # Fallback to default pattern using stored timestamp
            default_filename = f"livekit/{self.room_name}-{self.timestamp}.mp4"
            logger.debug(
                "Using default filename pattern",
                extra={
                    "default_filename": default_filename,
                    "timestamp": self.timestamp,
                    "reason": "S3_FILEPATH not set"
                }
            )
            return default_filename
    
    def _extract_filename_from_response(self, response) -> Optional[str]:
        """Extract actual filename from egress response."""
        try:
            logger.debug("Processing egress response", extra={
                "response_type": type(response).__name__,
                "has_fileResults": hasattr(response, "fileResults"),
                "has_segmentResults": hasattr(response, "segmentResults")
            })
            
            if hasattr(response, "fileResults") and response.fileResults:
                filename = response.fileResults[0].filename
                logger.debug("Extracted filename from fileResults", extra={"extracted_filename": filename})
                return filename
            elif hasattr(response, "segmentResults") and response.segmentResults:
                # For HLS, we might get segment results instead of file results
                filename = response.segmentResults[0].filename
                logger.debug("Extracted filename from segmentResults", extra={"extracted_filename": filename})
                return filename
            else:
                logger.warning("No fileResults or segmentResults found in response")
                return None
                
        except Exception as exc:
            logger.warning("Failed to extract filename from response", exc_info=exc)
            return None
    
    def _build_recording_metadata(self, actual_filename: Optional[str]) -> Dict[str, Any]:
        """
        Build comprehensive recording metadata with proper recording URL.
        
        This method constructs the recording metadata that will be sent to webhooks.
        It handles both MP4 and HLS recording modes and provides fallback URL
        construction when LiveKit doesn't return the actual filename.
        
        URL Construction Logic:
        1. For MP4: If actual_filename is available, use it; otherwise use fallback
        2. For HLS: Construct URL to the playlist file (.m3u8)
        3. Fallback: Use consistent timestamp to build expected filename
        
        Args:
            actual_filename: Filename returned by LiveKit (may be None for MP4)
            
        Returns:
            Dict containing recording metadata with keys:
                - provider: "s3"
                - egress_id: LiveKit egress job ID
                - filepath: Actual filename from LiveKit (may be None)
                - bucket: S3/R2 bucket name
                - endpoint: S3/R2 endpoint URL
                - recording_url: Playable URL for the recording
                - mode: "mp4" or "hls"
                - started_at: ISO timestamp when recording started
        """
        base_url = os.getenv("RECORDING_BASE_URL")
        recording_url = None
        
        # Enhanced debugging for URL construction (same as old code logic)
        logger.info(
            "Building recording metadata",
            extra={
                "base_url": base_url,
                "base_url_type": type(base_url).__name__,
                "base_url_stripped": base_url.strip() if base_url else None,
                "actual_filename": actual_filename,
                "use_hls": self._should_use_hls(),
                "room_name": self.room_name,
                "egress_id": self.egress_id,
                "env_check": {
                    "RECORDING_BASE_URL": os.getenv("RECORDING_BASE_URL"),
                    "S3_FILEPATH": os.getenv("S3_FILEPATH"),
                    "EGRESS_USE_HLS": os.getenv("EGRESS_USE_HLS"),
                    "ENABLE_EGRESS": os.getenv("ENABLE_EGRESS")
                }
            }
        )
        
        # Validate base URL
        if not base_url or not base_url.strip():
            logger.error("RECORDING_BASE_URL is not set or empty")
            logger.error("Please set RECORDING_BASE_URL in your environment variables")
        elif not base_url.startswith(('http://', 'https://')):
            logger.error(f"RECORDING_BASE_URL must be a valid HTTP/HTTPS URL: {base_url}")
        else:
            if self._should_use_hls():
                # For HLS, construct URL to the playlist file using consistent timestamp
                filename_prefix = self._build_filename().rstrip(".mp4")
                playlist_name = os.getenv("EGRESS_PLAYLIST_NAME", "playlist.m3u8")
                recording_url = f"{base_url.rstrip('/')}/{filename_prefix}/{playlist_name}"
                
                logger.info(
                    "Constructed HLS recording URL",
                    extra={
                        "filename_prefix": filename_prefix,
                        "playlist_name": playlist_name,
                        "recording_url": recording_url,
                        "base_url": base_url,
                        "timestamp": self.timestamp
                    }
                )
            else:
                # For MP4 mode, construct URL using the expected file path pattern
                if actual_filename:
                    # Use the actual filename from response if available
                    recording_url = f"{base_url.rstrip('/')}/{actual_filename.lstrip('/')}"
                    
                    logger.info(
                        "Constructed MP4 recording URL from response filename",
                        extra={
                            "actual_filename": actual_filename,
                            "recording_url": recording_url,
                            "base_url": base_url
                        }
                    )
                else:
                    # Fallback: construct URL using the expected file path pattern (same as old code)
                    expected_filename = self._build_filename()
                    recording_url = f"{base_url.rstrip('/')}/{expected_filename}"
                    
                    logger.info(
                        "Constructed MP4 recording URL from expected file path (FALLBACK)",
                        extra={
                            "expected_filename": expected_filename,
                            "recording_url": recording_url,
                            "base_url": base_url,
                            "fallback_used": True,
                            "reason": "No actual_filename from response - using fallback like old code"
                        }
                    )
        
        if not recording_url:
            logger.error(
                "Failed to construct recording URL - This will result in null in webhook",
                extra={
                    "base_url": base_url,
                    "actual_filename": actual_filename,
                    "mode": "hls" if self._should_use_hls() else "mp4",
                    "egress_id": self.egress_id,
                    "environment_vars": {
                        "RECORDING_BASE_URL": os.getenv("RECORDING_BASE_URL"),
                        "S3_FILEPATH": os.getenv("S3_FILEPATH"),
                        "EGRESS_USE_HLS": os.getenv("EGRESS_USE_HLS")
                    }
                }
            )
        else:
            logger.info(
                "Successfully constructed recording URL",
                extra={
                    "recording_url": recording_url,
                    "base_url": base_url,
                    "mode": "hls" if self._should_use_hls() else "mp4"
                }
            )
        
        return {
            "provider": "s3",
            "egress_id": self.egress_id,
            "filepath": actual_filename,
            "bucket": os.getenv("S3_BUCKET"),
            "endpoint": os.getenv("S3_ENDPOINT"),
            "recording_url": recording_url,
            "mode": "hls" if self._should_use_hls() else "mp4",
            "started_at": datetime.now().isoformat(),
        }
