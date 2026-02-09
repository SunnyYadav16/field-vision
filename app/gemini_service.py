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
from .work_orders import (
    create_work_order, escalate_work_order,
    get_pending_orders, approve_pending_order
)
from .auth import has_permission, load_users

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
- Be concise and direct - technicians are busy.
- Do NOT say 'As an AI' or 'I understand' or 'Certainly'.
- Speak efficiently like a senior field colleague.
- Prioritize safety warnings over other information.
- Reference specific manual sections when applicable.

IMPORTANT: You are an ADVISORY system only. You do NOT control any machinery. All physical actions must be performed by the human technician.

WORK ORDER PROTOCOL:
When a technician requests a work order (e.g., 'create a ticket for...', 'log a work order for...', 'report an issue with...'):
1. First, call the create_work_order tool with the equipment, priority, and description from their request.
2. After the tool response, ask the technician to hold their employee ID badge up to the camera for verification.
3. When you can see a badge in the video frame, read the employee name, ID number, and department from it.
4. Call the verify_badge tool with the extracted information.
5. Based on the verify_badge response:
   - If AUTHORIZED: Confirm the work order was created and provide the order ID.
   - If ESCALATED: Inform the technician their request has been sent to their supervisor for approval.
   - If BADGE NOT FOUND: Ask them to try again, holding the badge closer and steadier.

BADGE READING:
When looking for a badge in the video, search for a card or ID tag being held up. Look for printed text showing a name, ID number (usually alphanumeric like 'tech_042' or 'EMP-123'), and optionally a department or role. If you cannot read the badge clearly, ask the technician to hold it closer or adjust the angle."""

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

    # Tool definition for creating work orders via voice
    WORK_ORDER_TOOL = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="create_work_order",
                description=(
                    "Creates a maintenance work order when the technician "
                    "requests one via voice. IMPORTANT: After this tool is "
                    "called, you MUST ask the technician to show their ID "
                    "badge to the camera before the order can be processed. "
                    "Do NOT confirm the order until badge verification is "
                    "complete via the verify_badge tool."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "equipment_id": types.Schema(
                            type=types.Type.STRING,
                            description="The equipment name or ID"
                        ),
                        "priority": types.Schema(
                            type=types.Type.STRING,
                            description="Priority level",
                            enum=["low", "medium", "high", "critical"]
                        ),
                        "description": types.Schema(
                            type=types.Type.STRING,
                            description="Description of the issue"
                        )
                    },
                    required=["equipment_id", "priority", "description"]
                )
            )
        ]
    )

    # Tool definition for badge verification
    BADGE_VERIFY_TOOL = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="verify_badge",
                description=(
                    "Verifies an employee ID badge seen in the video feed. "
                    "Call this when you can see a badge being held up to the "
                    "camera. Extract the employee name, ID number, and "
                    "department visible on the badge."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "employee_name": types.Schema(
                            type=types.Type.STRING,
                            description="Name visible on the badge"
                        ),
                        "employee_id": types.Schema(
                            type=types.Type.STRING,
                            description="Employee ID number on the badge"
                        ),
                        "department": types.Schema(
                            type=types.Type.STRING,
                            description="Department shown on the badge"
                        )
                    },
                    required=["employee_name", "employee_id"]
                )
            )
        ]
    )

    def __init__(self):
        self.settings = get_settings()
        self.client = genai.Client(
            http_options={'api_version': 'v1alpha'},
            api_key=self.settings.gemini_api_key
        )
        self.audit_logger = get_audit_logger()
        self._active_sessions: dict[str, "LiveSession"] = {}
        # Global storage for session histories to survive reconnections
        self._session_histories: dict[str, list[types.Content]] = {}
    
    async def create_session(
        self,
        session_config: SessionConfig,
        on_audio: Callable[[bytes], Any],
        on_text: Callable[[str], Any],
        on_tool_call: Callable[[str, dict], Any],
        on_turn_complete: Optional[Callable[[], Any]] = None
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
            tools=[self.SAFETY_EVENT_TOOL, self.WORK_ORDER_TOOL, self.BADGE_VERIFY_TOOL],
            
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
        
        if session_config.session_id not in self._session_histories:
            self._session_histories[session_config.session_id] = []
            
        session = LiveSession(
            service=self,
            session_id=session_config.session_id,
            config=config,
            on_audio=on_audio,
            on_text=on_text,
            on_tool_call=on_tool_call,
            on_turn_complete=on_turn_complete,
            history=self._session_histories[session_config.session_id]
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
            # Phase 2: capture visual proof if available
            evidence_url = None
            session = self._active_sessions.get(session_id)
            
            if session and session._latest_frame and arguments.get("severity", 1) >= 3:
                try:
                    import os
                    from pathlib import Path
                    
                    # Create evidence directory
                    evidence_dir = Path("static/evidence")
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename = f"evidence_{session_id}_{timestamp}.jpg"
                    filepath = evidence_dir / filename
                    
                    with open(filepath, "wb") as f:
                        f.write(session._latest_frame)
                    
                    evidence_url = f"/static/evidence/{filename}"
                    logger.info("evidence_captured", path=str(filepath), session_id=session_id)
                except Exception as e:
                    logger.error("evidence_capture_failed", error=str(e), session_id=session_id)

            event = await self.audit_logger.log_event(
                session_id=session_id,
                event_type=arguments.get("event_type", "unknown"),
                severity=arguments.get("severity", 1),
                description=arguments.get("description", ""),
                source="ai",
                metadata={"evidence_url": evidence_url} if evidence_url else {}
            )
            
            return {
                "status": "logged",
                "event_id": event.timestamp,
                "evidence_captured": True if evidence_url else False,
                "message": f"Safety event '{arguments.get('event_type')}' logged successfully"
            }
        
        elif function_name == "create_work_order":
            # Step 1: Technician requested a work order.
            # DON'T create it yet. Store the request and tell Gemini
            # to ask for badge verification.
            # Note: pending_work_order is stored on the LiveSession instance
            session = self._active_sessions.get(session_id)
            if session:
                session.pending_work_order = {
                    "equipment_id": arguments.get("equipment_id", ""),
                    "priority": arguments.get("priority", "medium"),
                    "description": arguments.get("description", ""),
                }
            
            return {
                "status": "badge_verification_required",
                "message": (
                    "Work order request received. Badge verification "
                    "is required before processing. Please ask the "
                    "technician to hold their employee ID badge up "
                    "to the camera."
                )
            }
        
        elif function_name == "verify_badge":
            # Step 2: Gemini read the badge from video.
            # Look up the employee and check permissions.
            badge_employee_id = arguments.get("employee_id", "")
            badge_name = arguments.get("employee_name", "")
            badge_dept = arguments.get("department", "")
            
            # Get pending work order from session
            session = self._active_sessions.get(session_id)
            pending = getattr(session, 'pending_work_order', {}) if session else {}
            
            # Look up this employee in users.json
            users = load_users()
            badge_user = users.get(badge_employee_id)
            
            if badge_user is None:
                # Employee ID not found in our system
                return {
                    "status": "badge_not_found",
                    "message": (
                        f"Employee ID '{badge_employee_id}' was not "
                        f"found in the system. Please try scanning "
                        f"the badge again or verify the ID."
                    )
                }
            
            elif "create_work_order" in badge_user.get("permissions", []):
                # AUTHORIZED: Create the work order
                order = create_work_order(
                    equipment_id=pending.get("equipment_id", "unknown"),
                    priority=pending.get("priority", "medium"),
                    description=pending.get("description", ""),
                    requested_by={
                        "id": badge_employee_id,
                        "name": badge_name,
                        "role": badge_user["role"]
                    }
                )
                # Clear pending work order
                if session:
                    session.pending_work_order = {}
                    
                return {
                    "status": "authorized",
                    "order_id": order["order_id"],
                    "message": (
                        f"Work order {order['order_id']} created "
                        f"successfully. {order['priority'].upper()} "
                        f"priority ticket for {order['equipment']} "
                        f"- {order['description']}. Assigned to "
                        f"maintenance queue."
                    )
                }
            
            else:
                # NOT AUTHORIZED: Escalate to supervisor
                order = escalate_work_order(
                    equipment_id=pending.get("equipment_id", "unknown"),
                    priority=pending.get("priority", "medium"),
                    description=pending.get("description", ""),
                    requested_by={
                        "id": badge_employee_id,
                        "name": badge_name,
                        "role": badge_user["role"]
                    },
                    escalate_to="sup_007"
                )
                # Clear pending work order
                if session:
                    session.pending_work_order = {}
                    
                return {
                    "status": "escalated",
                    "order_id": order["order_id"],
                    "message": (
                        f"Employee {badge_name} ({badge_employee_id}) "
                        f"does not have work order creation "
                        f"permission. Request {order['order_id']} has "
                        f"been escalated to supervisor Morgan Chen "
                        f"(sup_007) for approval. The technician "
                        f"should be informed their request was "
                        f"submitted for supervisor review."
                    )
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
        on_tool_call: Callable[[str, dict], Any],
        on_turn_complete: Optional[Callable[[], Any]] = None,
        history: list[types.Content] = None
    ):
        self.service = service
        self.session_id = session_id
        self.config = config
        self.on_audio = on_audio
        self.on_text = on_text
        self.on_tool_call = on_tool_call
        self.on_turn_complete = on_turn_complete
        self.history = history if history is not None else []
        self._session = None
        self._session_context = None
        self._running = False
        self._resume_handle: Optional[str] = None
        self.pending_work_order: dict = {}  # State for chained tool calls
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # Turn Parts Accumulator
        self._current_ai_response_parts: list[types.Part] = []
        
        # Turn Completion Tracking (faster 2.5s fallback)
        self._turn_in_progress = False
        self._turn_complete_timer: Optional[asyncio.Task] = None
        self._turn_complete_timeout: float = 2.5  # Snappier fallback
        
        # Phase 2: Visual Evidence Buffer
        self._latest_frame: Optional[bytes] = None
        
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
        
        # Start heartbeat to prevent 1011 keepalive timeouts
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        # Handle session re-hydration: replay history if it exists
        if self.history:
            logger.info("rehydrating_session", 
                        history_turns=len(self.history), 
                        session_id=self.session_id)
            await self._session.send(
                input=types.LiveClientContent(
                    turns=self.history,
                    turn_complete=True
                )
            )
        
        logger.info("connected_to_gemini", session_id=self.session_id)
    
    async def disconnect(self) -> None:
        """Close the Gemini session"""
        self._running = False
        
        # Cancel timers
        if self._turn_complete_timer:
            self._turn_complete_timer.cancel()
            self._turn_complete_timer = None
            
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
                
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
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
            # Buffer the frame for Phase 2 evidence capture
            self._latest_frame = jpeg_data
            
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
        """Send a text message to Gemini.
        
        The Gemini Live API maintains conversation context internally via
        the persistent bidirectional stream. We only send the NEW user
        message — not the full history. Resending full history causes
        duplicate/confused context and breaks multi-turn sessions.
        """
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
        
        # Track in local history for logging/reporting only
        user_content = types.Content(
            role="user",
            parts=[types.Part(text=text.strip())]
        )
        self.history.append(user_content)
        
        # Phase 2: Log user interaction to transcript
        from app.conversation_logger import conversation_logger
        asyncio.create_task(conversation_logger.log_interaction(self.session_id, {
            "speaker": "USER",
            "type": "question",
            "content": text.strip()
        }))
        
        try:
            # Send ONLY the new user message — Gemini Live API keeps
            # its own internal conversation context across the stream.
            await self._session.send(
                input=types.LiveClientContent(
                    turns=[user_content],
                    turn_complete=True
                )
            )
            logger.debug("sent_text",
                         text_preview=text[:50],
                         history_len=len(self.history),
                         session_id=self.session_id)
        except Exception as e:
            logger.error("send_text_error", error=str(e), session_id=self.session_id)

    async def _heartbeat_loop(self) -> None:
        """Periodic pulse to keep the WebSocket connection alive"""
        try:
            while self._running:
                await asyncio.sleep(10)  # Pulse every 10 seconds (faster to stay alive)
                if self._session and self._running:
                    try:
                        # Send a minimal content pulse or empty realtime input
                        # This keeps the underlying TCP/WebSocket connection active
                        await self._session.send(
                            input=types.LiveClientRealtimeInput(
                                media_chunks=[]
                            )
                        )
                        logger.debug("heartbeat_sent", session_id=self.session_id)
                    except Exception as e:
                        logger.debug("heartbeat_failed", error=str(e))
        except asyncio.CancelledError:
            pass
    
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
        """Process a response from Gemini with history accumulation"""
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
                self._turn_in_progress = True
                self._reset_turn_complete_timer()
                
                for part in content.model_turn.parts:
                    # Handle audio
                    if hasattr(part, 'inline_data') and part.inline_data:
                        if part.inline_data.mime_type.startswith("audio/"):
                            await self.on_audio(part.inline_data.data)
                            # Accumulate response for history if needed (audio is usually ephemeral)
                            self._reset_turn_complete_timer()
                    
                    # Handle text
                    if hasattr(part, 'text') and part.text:
                        await self.on_text(part.text)
                        
                        # Phase 2: Log AI interaction to transcript
                        from app.conversation_logger import conversation_logger
                        asyncio.create_task(conversation_logger.log_interaction(self.session_id, {
                            "speaker": "AI",
                            "type": "answer",
                            "content": part.text
                        }))
                        
                        # Add to AI turn accumulator
                        self._current_ai_response_parts.append(types.Part(text=part.text))
                        # Reset timer
                        self._reset_turn_complete_timer()
            
            # Check for native turn_complete signal
            if hasattr(content, 'turn_complete') and content.turn_complete:
                await self._finalize_turn("native")
        
        # Handle tool calls
        if hasattr(response, 'tool_call') and response.tool_call:
                # Notify callback
                await self.on_tool_call(fc.name, dict(fc.args))
                
                # Phase 2: Log Tool Call to transcript
                from app.conversation_logger import conversation_logger
                asyncio.create_task(conversation_logger.log_interaction(self.session_id, {
                    "speaker": "SYSTEM",
                    "type": "tool_call",
                    "content": f"AI requested tool: {fc.name}",
                    "metadata": {"function": fc.name, "args": dict(fc.args)}
                }))
                
                # Check for reset of timer if it was a lengthy tool call
                self._reset_turn_complete_timer()
                
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

    def _reset_turn_complete_timer(self) -> None:
        """Reset the turn completion timer"""
        if self._turn_complete_timer:
            self._turn_complete_timer.cancel()
        
        logger.debug("timer_reset", session_id=self.session_id)
        self._turn_complete_timer = asyncio.create_task(self._turn_complete_timer_task())
    
    async def _turn_complete_timer_task(self) -> None:
        """Fires turn_complete if no new activity for 5 seconds"""
        try:
            await asyncio.sleep(self._turn_complete_timeout)
            if self._turn_in_progress:
                await self._finalize_turn("fallback_timer")
        except asyncio.CancelledError:
            pass

    async def _finalize_turn(self, reason: str) -> None:
        """Finalize the current turn: cancel timer, save history, and notify callback"""
        if self._turn_complete_timer:
            self._turn_complete_timer.cancel()
            self._turn_complete_timer = None
        
        if not self._turn_in_progress:
            return
            
        self._turn_in_progress = False
        logger.debug("turn_finalized", reason=reason, session_id=self.session_id)
        
        # Combine accumulated AI parts into history turn
        if self._current_ai_response_parts:
            # Append to shared history
            self.history.append(types.Content(
                role="model",
                parts=list(self._current_ai_response_parts)
            ))
            self._current_ai_response_parts = []
            
        # Fire callback to notify user/client they can ask the next question
        if self.on_turn_complete:
            await self.on_turn_complete()


# Service singleton
_gemini_service: Optional[GeminiLiveService] = None


def get_gemini_service() -> GeminiLiveService:
    """Get or create the Gemini service singleton"""
    global _gemini_service
    if _gemini_service is None:
        _gemini_service = GeminiLiveService()
    return _gemini_service
