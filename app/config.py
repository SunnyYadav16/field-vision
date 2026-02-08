"""
FieldVision Configuration Module
Centralized settings management using Pydantic
"""

from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # API Configuration
    gemini_api_key: str = Field(..., description="Google Gemini API Key")
    
    # Server Configuration
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")
    debug: bool = Field(default=False, description="Debug mode")
    
    # Session Configuration
    session_ttl_seconds: int = Field(default=3600, description="Session TTL in seconds")
    max_resume_attempts: int = Field(default=3, description="Max session resume attempts")
    
    # Audio Configuration
    input_sample_rate: int = Field(default=16000, description="Input audio sample rate (Hz)")
    output_sample_rate: int = Field(default=24000, description="Output audio sample rate (Hz)")
    
    # Video Configuration
    frame_rate: int = Field(default=1, description="Video frame rate (FPS)")
    jpeg_quality: int = Field(default=85, description="JPEG compression quality")
    
    # Logging Configuration
    log_level: str = Field(default="INFO", description="Logging level")
    audit_log_path: str = Field(default="./logs/audit_log.json", description="Audit log file path")
    
    # Model Configuration - Preview model that supports bidiGenerateContent
    gemini_model: str = Field(
        default="gemini-2.5-flash-native-audio-preview-12-2025",
        description="Gemini Live API model identifier"
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
