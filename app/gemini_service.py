import asyncio
import os
import structlog
from typing import Optional

# Ensure GOOGLE_API_KEY is set before importing ADK (it reads env vars on import)
from app.config import get_settings as _get_settings
_settings = _get_settings()
if _settings.gemini_api_key and not os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = _settings.gemini_api_key

# Set to use Gemini API (not Vertex AI) unless explicitly configured
if not os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"):
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"

from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.fieldvision_agent import fieldvision_agent
from app.config import get_settings

logger = structlog.get_logger(__name__)

APP_NAME = "fieldvision"

# Session service stores conversation state across connections
session_service = InMemorySessionService()

# Runner orchestrates agent execution, tool calls, and session management
runner = Runner(
    app_name=APP_NAME,
    agent=fieldvision_agent,
    session_service=session_service,
)


def build_run_config(
    proactivity: bool = False,
    affective_dialog: bool = False,
) -> RunConfig:
    """
    Build the RunConfig for a bidi-streaming session.
    Automatically detects native-audio vs half-cascade model and configures accordingly.
    """
    settings = get_settings()
    model_name = settings.gemini_model.lower()
    is_native_audio = "native-audio" in model_name

    if is_native_audio:
        # Native audio model: AUDIO response with transcription
        config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Puck"
                    )
                )
            ),
        )

        # Optional: Enable proactive audio (model speaks first)
        if proactivity:
            config.proactivity = True

        # Optional: Enable affective dialog (tone-aware responses)
        if affective_dialog:
            config.affective_dialog = types.AffectiveDialogConfig(
                enabled=True
            )

    else:
        # Half-cascade model: TEXT response (faster, no transcription)
        config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["TEXT"],
            session_resumption=types.SessionResumptionConfig(),
        )

    return config


async def get_or_create_session(user_id: str, session_id: str):
    """
    Get an existing session or create a new one.
    ADK sessions persist conversation history and state.
    """
    # Try to get existing session
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )

    if session is None:
        # Create new session with initial state
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
            state={
                "session_id": session_id,
                "user_id": user_id,
            },
        )
        logger.info("session_created", session_id=session_id, user_id=user_id)
    else:
        logger.info("session_resumed", session_id=session_id, user_id=user_id)

    return session