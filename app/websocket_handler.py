"""
FieldVision WebSocket Handler
Manages browser<->server<->Gemini communication via ADK bidi-streaming
"""

import asyncio
import base64
import json
import uuid
from typing import Optional
from dataclasses import dataclass, asdict
from enum import Enum
import structlog
from fastapi import WebSocket, WebSocketDisconnect

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.genai import types

from .gemini_service import runner, build_run_config, get_or_create_session
from .audit import get_audit_logger
from .manual_loader import get_manual_loader, validate_manual_context

logger = structlog.get_logger(__name__)

# Store latest frame per active technician session (for manager camera view)
# key: user_id, value: { "frame": bytes, "zone": str, "name": str, "role": str }
active_camera_feeds = {}


class MessageType(str, Enum):
    """WebSocket message types"""
    # Client -> Server
    START_SESSION = "start_session"
    END_SESSION = "end_session"
    AUDIO_DATA = "audio_data"
    VIDEO_FRAME = "video_frame"
    TEXT_MESSAGE = "text_message"
    
    # Server -> Client
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    AUDIO_RESPONSE = "audio_response"
    TEXT_RESPONSE = "text_response"
    TOOL_CALL = "tool_call"
    ERROR = "error"
    STATUS = "status"
    TURN_COMPLETE = "turn_complete"


@dataclass
class WSMessage:
    """WebSocket message structure"""
    type: str
    payload: dict
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))
    
    @classmethod
    def from_json(cls, data: str) -> "WSMessage":
        parsed = json.loads(data)
        return cls(type=parsed["type"], payload=parsed.get("payload", {}))


class ConnectionManager:
    """
    Manages WebSocket connections and their associated Gemini sessions.
    """
    
    def __init__(self):
        self.active_connections: dict[str, "ClientConnection"] = {}
        self.audit_logger = get_audit_logger()
    
    async def connect(self, websocket: WebSocket, session_user: dict = None) -> "ClientConnection":
        """Accept a new WebSocket connection with optional user context"""
        await websocket.accept()
        
        connection_id = str(uuid.uuid4())
        connection = ClientConnection(
            connection_id=connection_id,
            websocket=websocket,
            manager=self,
            session_user=session_user
        )
        self.active_connections[connection_id] = connection
        
        logger.info("client_connected", connection_id=connection_id, user_id=session_user.get("user_id") if session_user else None)
        return connection
    
    async def disconnect(self, connection_id: str) -> None:
        """Handle client disconnection"""
        if connection_id in self.active_connections:
            connection = self.active_connections[connection_id]
            await connection.cleanup()
            del self.active_connections[connection_id]
            logger.info("client_disconnected", connection_id=connection_id)


class ClientConnection:
    """
    Represents a single client WebSocket connection.
    Bridges browser and Gemini Live session via ADK bidi-streaming.
    """
    
    def __init__(
        self,
        connection_id: str,
        websocket: WebSocket,
        manager: ConnectionManager,
        session_user: dict = None
    ):
        self.connection_id = connection_id
        self.websocket = websocket
        self.manager = manager
        self.session_user = session_user  # User context for permission checks
        self.session_id: Optional[str] = None
        self.live_queue: Optional[LiveRequestQueue] = None
        self._downstream_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()
        self._is_session_active = False
    
    async def handle_messages(self) -> None:
        """Main message handling loop"""
        try:
            while True:
                data = await self.websocket.receive_text()
                message = WSMessage.from_json(data)
                await self._handle_message(message)
                
        except WebSocketDisconnect:
            logger.info("websocket_disconnected", connection_id=self.connection_id)
        except Exception as e:
            logger.error("message_handling_error", error=str(e))
            await self._send_error(str(e))
    
    async def _handle_message(self, message: WSMessage) -> None:
        """Route incoming messages to handlers"""
        handlers = {
            MessageType.START_SESSION: self._handle_start_session,
            MessageType.END_SESSION: self._handle_end_session,
            MessageType.AUDIO_DATA: self._handle_audio_data,
            MessageType.VIDEO_FRAME: self._handle_video_frame,
            MessageType.TEXT_MESSAGE: self._handle_text_message,
        }
        
        handler = handlers.get(message.type)
        if handler:
            await handler(message.payload)
        else:
            logger.warning("unknown_message_type", type=message.type)
    
    async def _handle_start_session(self, payload: dict) -> None:
        """Start a new Gemini Live session using ADK bidi-streaming"""
        if self._is_session_active:
            await self._send_error("Session already active")
            return
        
        self.session_id = str(uuid.uuid4())
        user_id = self.session_user.get("user_id", "anonymous") if self.session_user else "anonymous"
        
        # Load manual context (from payload or default)
        manual_context = payload.get("manual_context")
        if manual_context is None:
            # Auto-load default safety manual
            manual_loader = get_manual_loader()
            manual_context = manual_loader.get_default_manual()
            if manual_context:
                logger.info("default_manual_loaded", session_id=self.session_id)
        
        # Validate manual context
        is_valid, error_msg = validate_manual_context(manual_context)
        if not is_valid:
            await self._send_error(f"Invalid manual context: {error_msg}")
            return
        
        try:
            # Create ADK session
            await get_or_create_session(user_id=user_id, session_id=self.session_id)
            
            # Build the run config for bidi-streaming
            run_config = build_run_config()
            
            # Create the LiveRequestQueue for sending data to the model
            self.live_queue = LiveRequestQueue()
            
            # Start the downstream task that reads events from run_live()
            self._downstream_task = asyncio.create_task(
                self._run_downstream(
                    user_id=user_id,
                    session_id=self.session_id,
                    run_config=run_config
                )
            )
            
            self._is_session_active = True
            
            # Log session start
            await self.manager.audit_logger.log_event(
                session_id=self.session_id,
                event_type="session_started",
                severity=1,
                description="FieldVision session started",
                source="system"
            )
            
            await self._send_message(MessageType.SESSION_STARTED, {
                "session_id": self.session_id,
                "message": "Connected to FieldVision AI"
            })
            
        except Exception as e:
            logger.error("session_start_failed", error=str(e))
            await self._send_error(f"Failed to start session: {str(e)}")
    
    async def _run_downstream(self, user_id: str, session_id: str, run_config) -> None:
        """
        Downstream task: consume events from runner.run_live() and 
        forward them to the WebSocket client as typed messages.
        """
        try:
            async for event in runner.run_live(
                user_id=user_id,
                session_id=session_id,
                live_request_queue=self.live_queue,
                run_config=run_config
            ):
                # Parse the event and send appropriate messages to the client
                await self._process_event(event)
                
        except asyncio.CancelledError:
            logger.info("downstream_task_cancelled", session_id=session_id)
        except Exception as e:
            logger.error("downstream_task_error", error=str(e), session_id=session_id)
            try:
                await self._send_error(f"Stream error: {str(e)}")
            except Exception:
                pass  # WebSocket might already be closed

    async def _process_event(self, event) -> None:
        """
        Process a single ADK event and forward to the WebSocket client.
        Events can contain partial text, audio chunks, tool calls, 
        transcriptions, and turn completion signals.
        """
        try:
            # Check if the event has content parts
            if hasattr(event, 'content') and event.content and hasattr(event.content, 'parts'):
                for part in event.content.parts:
                    # Handle text responses
                    if hasattr(part, 'text') and part.text:
                        await self._send_message(MessageType.TEXT_RESPONSE, {
                            "text": part.text
                        })
                    
                    # Handle audio responses (inline_data with audio)
                    if hasattr(part, 'inline_data') and part.inline_data:
                        if part.inline_data.mime_type and 'audio' in part.inline_data.mime_type:
                            audio_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                            await self._send_message(MessageType.AUDIO_RESPONSE, {
                                "data": audio_b64,
                                "mime_type": part.inline_data.mime_type
                            })
            
            # Check for server content (audio from bidi streaming)
            if hasattr(event, 'server_content') and event.server_content:
                sc = event.server_content
                
                # Model turn parts (audio/text in streaming)
                if hasattr(sc, 'model_turn') and sc.model_turn and hasattr(sc.model_turn, 'parts'):
                    for part in sc.model_turn.parts:
                        if hasattr(part, 'text') and part.text:
                            await self._send_message(MessageType.TEXT_RESPONSE, {
                                "text": part.text
                            })
                        if hasattr(part, 'inline_data') and part.inline_data:
                            if part.inline_data.data:
                                audio_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                                await self._send_message(MessageType.AUDIO_RESPONSE, {
                                    "data": audio_b64,
                                    "mime_type": part.inline_data.mime_type or "audio/pcm;rate=24000"
                                })
                
                # Turn complete signal
                if hasattr(sc, 'turn_complete') and sc.turn_complete:
                    await self._send_message(MessageType.TURN_COMPLETE, {
                        "message": "Turn complete"
                    })
                
                # Input transcription (what the user said)
                if hasattr(sc, 'input_transcription') and sc.input_transcription:
                    if hasattr(sc.input_transcription, 'text') and sc.input_transcription.text:
                        await self._send_message(MessageType.STATUS, {
                            "type": "input_transcription",
                            "text": sc.input_transcription.text
                        })
                
                # Output transcription (what the model said, text version of audio)
                if hasattr(sc, 'output_transcription') and sc.output_transcription:
                    if hasattr(sc.output_transcription, 'text') and sc.output_transcription.text:
                        await self._send_message(MessageType.TEXT_RESPONSE, {
                            "text": sc.output_transcription.text
                        })

            # Check for tool calls
            if hasattr(event, 'tool_calls') and event.tool_calls:
                for tool_call in event.tool_calls:
                    await self._send_message(MessageType.TOOL_CALL, {
                        "function": tool_call.function.name if hasattr(tool_call, 'function') else str(tool_call),
                        "arguments": dict(tool_call.function.args) if hasattr(tool_call, 'function') and hasattr(tool_call.function, 'args') else {}
                    })

            # Check for actions/function calls (ADK style)
            if hasattr(event, 'actions') and event.actions and hasattr(event.actions, 'function_calls'):
                for fc in event.actions.function_calls:
                    await self._send_message(MessageType.TOOL_CALL, {
                        "function": fc.name,
                        "arguments": dict(fc.args) if fc.args else {}
                    })

        except Exception as e:
            logger.error("event_processing_error", error=str(e))
    
    async def _handle_end_session(self, payload: dict) -> None:
        """End the current Gemini session"""
        if self._is_session_active:
            # Get session summary before ending
            summary = await self.manager.audit_logger.get_session_summary(self.session_id)
            
            # Close the live queue to stop the downstream task
            if self.live_queue:
                self.live_queue.close()
            
            # Cancel the downstream task
            if self._downstream_task and not self._downstream_task.done():
                self._downstream_task.cancel()
                try:
                    await self._downstream_task
                except asyncio.CancelledError:
                    pass
            
            # Log session end
            await self.manager.audit_logger.log_event(
                session_id=self.session_id,
                event_type="session_ended",
                severity=1,
                description=f"Session ended. Total events: {summary.get('total_events', 0)}",
                source="system"
            )
            
            await self._send_message(MessageType.SESSION_ENDED, {
                "session_id": self.session_id,
                "summary": summary
            })
            
            # Clean up camera feed for this user
            if self.session_user:
                active_camera_feeds.pop(self.session_user.get("user_id"), None)
            
            self._is_session_active = False
            self.live_queue = None
            self._downstream_task = None
            self.session_id = None
    
    async def _handle_audio_data(self, payload: dict) -> None:
        """Forward audio data to Gemini via LiveRequestQueue"""
        if not self._is_session_active or not self.live_queue:
            return
            
        # Decode base64 audio
        audio_b64 = payload.get("data", "")
        audio_bytes = base64.b64decode(audio_b64)
        
        # Send as real-time blob
        audio_blob = types.Blob(
            mime_type="audio/pcm;rate=16000",
            data=audio_bytes
        )
        self.live_queue.send_realtime(audio_blob)
    
    async def _handle_video_frame(self, payload: dict) -> None:
        """Forward video frame to Gemini via LiveRequestQueue and store for manager view"""
        if not self._is_session_active or not self.live_queue:
            return
            
        # Decode base64 JPEG
        frame_b64 = payload.get("data", "")
        frame_bytes = base64.b64decode(frame_b64)
        
        # Store latest frame for manager camera relay
        if self.session_user:
            active_camera_feeds[self.session_user["user_id"]] = {
                "frame": frame_bytes,
                "zone": self.session_user.get("zone", "unknown"),
                "name": self.session_user.get("name", "Unknown"),
                "role": self.session_user.get("role", "technician")
            }
        
        # Send as real-time blob
        video_blob = types.Blob(
            mime_type="image/jpeg",
            data=frame_bytes
        )
        self.live_queue.send_realtime(video_blob)
    
    async def _handle_text_message(self, payload: dict) -> None:
        """Forward text message to Gemini via LiveRequestQueue"""
        if not self._is_session_active or not self.live_queue:
            return
            
        text = payload.get("text", "")
        if text:
            content = types.Content(
                role="user",
                parts=[types.Part(text=text)]
            )
            self.live_queue.send_content(content)
    
    async def _send_message(self, msg_type: MessageType, payload: dict) -> None:
        """Send a message to the client"""
        async with self._send_lock:
            message = WSMessage(type=msg_type.value, payload=payload)
            await self.websocket.send_text(message.to_json())
    
    async def _send_error(self, error: str) -> None:
        """Send an error message to the client"""
        await self._send_message(MessageType.ERROR, {"error": error})
    
    async def cleanup(self) -> None:
        """Clean up connection resources"""
        if self._is_session_active:
            if self.live_queue:
                self.live_queue.close()
            if self._downstream_task and not self._downstream_task.done():
                self._downstream_task.cancel()
                try:
                    await self._downstream_task
                except asyncio.CancelledError:
                    pass
            self._is_session_active = False


# Connection manager singleton
_connection_manager: Optional[ConnectionManager] = None


def get_connection_manager() -> ConnectionManager:
    """Get or create the connection manager singleton"""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager
