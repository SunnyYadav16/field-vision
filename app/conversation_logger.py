"""
Conversation Logger Module
Logs session transcripts including user queries, AI responses, and tool calls.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class ConversationLogger:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
    async def log_interaction(self, session_id: str, turn_data: Dict[str, Any]):
        """
        Log a single interaction turn or event.
        
        Args:
            session_id: Unique session identifier
            turn_data: Dictionary containing:
                - timestamp (ISO format)
                - speaker (USER, AI, SYSTEM)
                - type (question, answer, tool_call, tool_response, observation)
                - content (text content)
                - metadata (optional dict)
        """
        transcript_file = self.log_dir / "session_transcript.json"
        
        # Structure for the log entry
        entry = {
            "session_id": session_id,
            "timestamp": turn_data.get("timestamp", datetime.utcnow().isoformat()),
            "speaker": turn_data.get("speaker", "UNKNOWN"),
            "type": turn_data.get("type", "unknown"),
            "content": turn_data.get("content", ""),
            "metadata": turn_data.get("metadata", {})
        }
        
        # Append to file (load, append, save - simple implementation for hackathon)
        # In production, this would use a database or append-only log stream
        try:
            current_logs = []
            if transcript_file.exists():
                try:
                    with open(transcript_file, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:
                            current_logs = json.loads(content)
                except json.JSONDecodeError:
                    logger.warning(f"Corrupt transcript file: {transcript_file}. Starting fresh.")
            
            # Ensure it's a list
            if not isinstance(current_logs, list):
                current_logs = []
                
            current_logs.append(entry)
            
            with open(transcript_file, 'w', encoding='utf-8') as f:
                json.dump(current_logs, f, indent=2)
                
            logger.debug(f"Logged interaction: {entry['type']} by {entry['speaker']}")
            
        except Exception as e:
            logger.error(f"Failed to log interaction: {e}")

# Global instance
conversation_logger = ConversationLogger()
