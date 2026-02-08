"""
FieldVision WebSocket Handler
Manages browser<->server<->Gemini communication
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

from .gemini_service import get_gemini_service, SessionConfig, LiveSession
from .audit import get_audit_logger
from .manual_loader import get_manual_loader, validate_manual_context

logger = structlog.get_logger(__name__)


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
        self.gemini_service = get_gemini_service()
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
    Bridges browser and Gemini Live session.
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
        self.session: Optional[LiveSession] = None
        self.session_id: Optional[str] = None
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._send_lock = asyncio.Lock()
    
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
        """Start a new Gemini Live session"""
        if self.session:
            await self._send_error("Session already active")
            return
        
        self.session_id = str(uuid.uuid4())
        
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
        
        # Create session config
        config = SessionConfig(
            session_id=self.session_id,
            system_instruction=payload.get("system_instruction"),
            manual_context=manual_context,
            resume_handle=payload.get("resume_handle")
        )
        
        try:
            # Create Gemini session with callbacks
            self.session = await self.manager.gemini_service.create_session(
                session_config=config,
                on_audio=self._on_audio_response,
                on_text=self._on_text_response,
                on_tool_call=self._on_tool_call
            )
            
            await self.session.connect()
            
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
    
    async def _handle_end_session(self, payload: dict) -> None:
        """End the current Gemini session"""
        if self.session:
            # Get session summary before ending
            summary = await self.manager.audit_logger.get_session_summary(self.session_id)
            
            await self.session.disconnect()
            
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
                "summary": summary,
                "resume_handle": self.session.resume_handle
            })
            
            self.session = None
            self.session_id = None
    
    async def _handle_audio_data(self, payload: dict) -> None:
        """Forward audio data to Gemini"""
        if not self.session:
            return
            
        # Decode base64 audio
        audio_b64 = payload.get("data", "")
        audio_bytes = base64.b64decode(audio_b64)
        
        await self.session.send_audio(audio_bytes)
    
    async def _handle_video_frame(self, payload: dict) -> None:
        """Forward video frame to Gemini"""
        if not self.session:
            return
            
        # Decode base64 JPEG
        frame_b64 = payload.get("data", "")
        frame_bytes = base64.b64decode(frame_b64)
        
        await self.session.send_video_frame(frame_bytes)
    
    async def _handle_text_message(self, payload: dict) -> None:
        """Forward text message to Gemini"""
        if not self.session:
            return
            
        text = payload.get("text", "")
        if text:
            await self.session.send_text(text)
    
    async def _on_audio_response(self, audio_data: bytes) -> None:
        """Callback for audio responses from Gemini"""
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")
        await self._send_message(MessageType.AUDIO_RESPONSE, {
            "data": audio_b64,
            "mime_type": "audio/pcm;rate=24000"
        })
    
    async def _on_text_response(self, text: str) -> None:
        """Callback for text responses from Gemini"""
        await self._send_message(MessageType.TEXT_RESPONSE, {
            "text": text
        })
    
    async def _on_tool_call(self, function_name: str, arguments: dict) -> None:
        """Callback for tool calls from Gemini"""
        await self._send_message(MessageType.TOOL_CALL, {
            "function": function_name,
            "arguments": arguments
        })
    
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
        if self.session:
            await self.session.disconnect()


# Connection manager singleton
_connection_manager: Optional[ConnectionManager] = None


def get_connection_manager() -> ConnectionManager:
    """Get or create the connection manager singleton"""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager
