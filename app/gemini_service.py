"""
FieldVision Gemini Live API Service
Handles real-time audio/video streaming with Gemini
"""

import asyncio
import base64
import json
from typing import Optional, AsyncGenerator, Callable, Any
from dataclasses import dataclass
import structlog
from google import genai
from google.genai import types

from .config import get_settings
from .audit import get_audit_logger

logger = structlog.get_logger(__name__)


@dataclass
class SessionConfig:
    """Configuration for a Gemini Live session"""
    session_id: str
    system_instruction: str
    manual_context: Optional[str] = None
    resume_handle: Optional[str] = None


class GeminiLiveService:
    """
    Service for managing Gemini Live API sessions.
    Handles bidirectional audio/video streaming with tool calling support.
    """
    
    # System instruction for industrial safety expert
    DEFAULT_SYSTEM_INSTRUCTION = """You are FieldVision, an AI-powered Industrial Safety Expert and Maintenance Copilot.

Your role is to:
1. MONITOR the live video feed for safety hazards (missing PPE, unsafe conditions, incorrect procedures)
2. GUIDE technicians through maintenance procedures using cached technical manuals
3. ANSWER questions about equipment, procedures, and safety protocols
4. LOG all safety observations using the log_safety_event tool

Safety Detection Priorities:
- Missing PPE (gloves, safety glasses, hard hats, ear protection)
- Unsafe body positioning near machinery
- Improper tool usage
- Lockout/Tagout (LOTO) violations
- Spills, obstructions, or environmental hazards

Communication Style:
- Be concise and direct - technicians are busy
- Use clear, actionable language
- Prioritize safety warnings over other information
- Reference specific manual sections when applicable

IMPORTANT: You are an ADVISORY system only. You do NOT control any machinery. All physical actions must be performed by the human technician."""

    # Tool definition for safety event logging
    SAFETY_EVENT_TOOL = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="log_safety_event",
                description="Log a safety observation, hazard detection, or compliance event to the audit trail",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "event_type": types.Schema(
                            type=types.Type.STRING,
                            description="Type of safety event",
                            enum=[
                                "missing_ppe",
                                "hazard_detected", 
                                "unsafe_position",
                                "procedure_violation",
                                "equipment_issue",
                                "environment_hazard",
                                "step_verified",
                                "safety_check_passed"
                            ]
                        ),
                        "severity": types.Schema(
                            type=types.Type.INTEGER,
                            description="Severity level: 1=info, 2=low, 3=medium, 4=high, 5=critical"
                        ),
                        "description": types.Schema(
                            type=types.Type.STRING,
                            description="Detailed description of the observation"
                        )
                    },
                    required=["event_type", "severity", "description"]
                )
            )
        ]
    )

    def __init__(self):
        self.settings = get_settings()
        self.client = genai.Client(api_key=self.settings.gemini_api_key)
        self.audit_logger = get_audit_logger(self.settings.audit_log_path)
        self._active_sessions: dict[str, Any] = {}
        
    async def create_session(
        self,
        session_config: SessionConfig,
        on_audio: Callable[[bytes], Any],
        on_text: Callable[[str], Any],
        on_tool_call: Callable[[str, dict], Any]
    ) -> "LiveSession":
        """
        Create a new Gemini Live session.
        
        Args:
            session_config: Session configuration
            on_audio: Callback for audio responses
            on_text: Callback for text responses  
            on_tool_call: Callback for tool calls
            
        Returns:
            LiveSession instance
        """
        # Build system instruction with optional manual context
        full_instruction = session_config.system_instruction or self.DEFAULT_SYSTEM_INSTRUCTION
        if session_config.manual_context:
            full_instruction += f"\n\n---\nTECHNICAL MANUAL CONTEXT:\n{session_config.manual_context}"
        
        # Configure the live session with compression for extended duration
        # Without compression: audio-video sessions limited to 2 minutes
        # With compression: sessions can run much longer
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=full_instruction)]
            ),
            tools=[self.SAFETY_EVENT_TOOL],
            
            # Configure voice and generation settings
            generation_config=types.GenerationConfig(
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name="Puck"
                        )
                    )
                )
            ),
            
            # Enable context window compression for longer sessions
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
                trigger_tokens=25000,  # Compress when context reaches 25k tokens
            ),
            # Enable session resumption for handling connection resets
            session_resumption=types.SessionResumptionConfig(
                handle=session_config.resume_handle  # Use existing handle if resuming
            ),
        )
        
        session = LiveSession(
            service=self,
            session_id=session_config.session_id,
            config=config,
            on_audio=on_audio,
            on_text=on_text,
            on_tool_call=on_tool_call
        )
        
        self._active_sessions[session_config.session_id] = session
        return session
    
    async def handle_tool_call(
        self,
        session_id: str,
        function_name: str,
        arguments: dict
    ) -> dict:
        """
        Handle a tool call from Gemini.
        
        Args:
            session_id: Session identifier
            function_name: Name of the function called
            arguments: Function arguments
            
        Returns:
            Tool response to send back to Gemini
        """
        if function_name == "log_safety_event":
            event = await self.audit_logger.log_event(
                session_id=session_id,
                event_type=arguments.get("event_type", "unknown"),
                severity=arguments.get("severity", 1),
                description=arguments.get("description", ""),
                source="ai"
            )
            
            return {
                "status": "logged",
                "event_id": event.timestamp,
                "message": f"Safety event '{arguments.get('event_type')}' logged successfully"
            }
        
        logger.warning("unknown_tool_call", function_name=function_name)
        return {"status": "error", "message": f"Unknown function: {function_name}"}


class LiveSession:
    """
    Represents an active Gemini Live session.
    Manages the bidirectional streaming connection.
    """
    
    def __init__(
        self,
        service: GeminiLiveService,
        session_id: str,
        config: types.LiveConnectConfig,
        on_audio: Callable[[bytes], Any],
        on_text: Callable[[str], Any],
        on_tool_call: Callable[[str, dict], Any]
    ):
        self.service = service
        self.session_id = session_id
        self.config = config
        self.on_audio = on_audio
        self.on_text = on_text
        self.on_tool_call = on_tool_call
        self._session = None
        self._session_context = None
        self._running = False
        self._resume_handle: Optional[str] = None
        self._receive_task: Optional[asyncio.Task] = None
        
    @property
    def resume_handle(self) -> Optional[str]:
        """Get the session resume handle for reconnection"""
        return self._resume_handle
    
    async def connect(self) -> None:
        """Establish connection to Gemini Live API"""
        logger.info("connecting_to_gemini", session_id=self.session_id)
        
        # Get the async context manager
        self._session_context = self.service.client.aio.live.connect(
            model=self.service.settings.gemini_model,
            config=self.config
        )
        
        # Enter the context manager to get the session
        self._session = await self._session_context.__aenter__()
        self._running = True
        
        # Start receiving responses in background
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        logger.info("connected_to_gemini", session_id=self.session_id)
    
    async def disconnect(self) -> None:
        """Close the Gemini session"""
        self._running = False
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        # Exit the context manager properly
        if self._session_context:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("session_close_error", error=str(e))
            self._session_context = None
            self._session = None
            
        logger.info("disconnected_from_gemini", session_id=self.session_id)
    
    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio data to Gemini with validation"""
        if not self._session or not self._running:
            return
        
        # Validate audio data
        if not audio_data:
            return
        
        # Limit audio chunk size (max 32KB for efficiency)
        max_chunk_size = 32 * 1024
        if len(audio_data) > max_chunk_size:
            logger.warning("audio_chunk_too_large", 
                          size=len(audio_data), 
                          max_size=max_chunk_size)
            audio_data = audio_data[:max_chunk_size]
        
        try:
            await self._session.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[
                        types.Blob(
                            mime_type="audio/pcm;rate=16000",
                            data=audio_data
                        )
                    ]
                )
            )
        except Exception as e:
            logger.error("send_audio_error", error=str(e), session_id=self.session_id)
    
    async def send_video_frame(self, jpeg_data: bytes) -> None:
        """Send a video frame to Gemini with validation"""
        if not self._session or not self._running:
            return
        
        # Validate video data
        if not jpeg_data:
            return
        
        # Limit frame size (max 512KB for efficiency)
        max_frame_size = 512 * 1024
        if len(jpeg_data) > max_frame_size:
            logger.warning("video_frame_too_large", 
                          size=len(jpeg_data), 
                          max_size=max_frame_size)
            return  # Skip oversized frames instead of truncating
        
        try:
            await self._session.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[
                        types.Blob(
                            mime_type="image/jpeg",
                            data=jpeg_data
                        )
                    ]
                )
            )
        except Exception as e:
            logger.error("send_video_error", error=str(e), session_id=self.session_id)
    
    async def send_text(self, text: str) -> None:
        """Send a text message to Gemini with validation"""
        if not self._session or not self._running:
            return
        
        # Validate text
        if not text or not text.strip():
            return
        
        # Limit text length
        max_text_length = 4000
        if len(text) > max_text_length:
            logger.warning("text_too_long", length=len(text), max_length=max_text_length)
            text = text[:max_text_length]
        
        try:
            await self._session.send(
                input=types.LiveClientContent(
                    turns=[
                        types.Content(
                            role="user",
                            parts=[types.Part(text=text.strip())]
                        )
                    ],
                    turn_complete=True
                )
            )
        except Exception as e:
            logger.error("send_text_error", error=str(e), session_id=self.session_id)
    
    async def _receive_loop(self) -> None:
        """Background task to receive responses from Gemini"""
        try:
            async for response in self._session.receive():
                if not self._running:
                    break
                    
                try:
                    await self._handle_response(response)
                except Exception as e:
                    logger.error("handle_response_error", error=str(e), session_id=self.session_id)
                    # Continue listening despite error
                    
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("receive_loop_fatal_error", error=str(e), session_id=self.session_id)
    
    async def _handle_response(self, response) -> None:
        """Process a response from Gemini"""
        # Handle session resumption updates
        if hasattr(response, 'session_resumption_update'):
            update = response.session_resumption_update
            if update and hasattr(update, 'new_handle'):
                self._resume_handle = update.new_handle
                logger.debug("resume_handle_updated", session_id=self.session_id)
        
        # Handle server content (audio/text responses)
        if hasattr(response, 'server_content') and response.server_content:
            content = response.server_content
            
            if hasattr(content, 'model_turn') and content.model_turn:
                for part in content.model_turn.parts:
                    # Handle audio
                    if hasattr(part, 'inline_data') and part.inline_data:
                        if part.inline_data.mime_type.startswith("audio/"):
                            await self.on_audio(part.inline_data.data)
                    
                    # Handle text
                    if hasattr(part, 'text') and part.text:
                        await self.on_text(part.text)
        
        # Handle tool calls
        if hasattr(response, 'tool_call') and response.tool_call:
            for fc in response.tool_call.function_calls:
                # Notify callback
                await self.on_tool_call(fc.name, dict(fc.args))
                
                # Process tool call and send response
                result = await self.service.handle_tool_call(
                    self.session_id,
                    fc.name,
                    dict(fc.args)
                )
                
                # Send tool response back to Gemini
                try:
                    await self._session.send(
                        input=types.LiveClientToolResponse(
                            function_responses=[
                                types.FunctionResponse(
                                    name=fc.name,
                                    id=fc.id,
                                    response=result
                                )
                            ]
                        )
                    )
                    logger.debug("tool_response_sent", function=fc.name, session_id=self.session_id)
                except Exception as e:
                    logger.error("tool_response_send_failed", function=fc.name, error=str(e), session_id=self.session_id)


# Service singleton
_gemini_service: Optional[GeminiLiveService] = None


def get_gemini_service() -> GeminiLiveService:
    """Get or create the Gemini service singleton"""
    global _gemini_service
    if _gemini_service is None:
        _gemini_service = GeminiLiveService()
    return _gemini_service
