"""
FieldVision Audit Logger
Structured logging for safety events and compliance tracking
"""

import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal
from dataclasses import dataclass, asdict
import aiofiles
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SafetyEvent:
    """Represents a logged safety event"""
    timestamp: str
    session_id: str
    event_type: str
    severity: int  # 1-5 scale (1=info, 5=critical)
    description: str
    source: Literal["ai", "system", "user"]
    metadata: Optional[dict] = None
    
    def to_dict(self) -> dict:
        return asdict(self)


class AuditLogger:
    """
    Thread-safe audit logger for safety events.
    Writes events to JSON file for compliance tracking.
    """
    
    def __init__(self, log_path: str = "./logs/audit_log.json"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._session_events: dict[str, list[SafetyEvent]] = {}
        
        # Load existing logs
        self._load_history()
        
    def _load_history(self) -> None:
        """Load audit history from disk"""
        if not self.log_path.exists():
            return
            
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        event = SafetyEvent(**data)
                        # Ensure severity is valid (clamp 1-5)
                        event.severity = min(max(event.severity, 1), 5)
                        
                        if event.session_id not in self._session_events:
                            self._session_events[event.session_id] = []
                        self._session_events[event.session_id].append(event)
                    except json.JSONDecodeError:
                        continue
            logger.info("audit_history_loaded", 
                       sessions=len(self._session_events), 
                       total_events=sum(len(e) for e in self._session_events.values()))
        except Exception as e:
            logger.error("audit_history_load_error", error=str(e))

    def get_all_sessions(self) -> list[dict]:
        """Get summary of all recorded sessions"""
        sessions = []
        for session_id, events in self._session_events.items():
            if not events:
                continue
                
            start_time = events[0].timestamp
            end_time = events[-1].timestamp
            event_count = len(events)
            critical = sum(1 for e in events if e.severity >= 4)
            
            sessions.append({
                "session_id": session_id,
                "start_time": start_time,
                "end_time": end_time,
                "event_count": event_count,
                "critical_events": critical
            })
            
        # Sort by start time, newest first
        return sorted(sessions, key=lambda x: x["start_time"], reverse=True)
        
    async def log_event(
        self,
        session_id: str,
        event_type: str,
        severity: int,
        description: str,
        source: Literal["ai", "system", "user"] = "ai",
        metadata: Optional[dict] = None
    ) -> SafetyEvent:
        """
        Log a safety event to the audit trail.
        
        Args:
            session_id: Unique session identifier
            event_type: Type of event (e.g., "missing_ppe", "hazard_detected")
            severity: Severity level 1-5
            description: Human-readable description
            source: Event source (ai, system, or user)
            metadata: Additional contextual data
            
        Returns:
            The created SafetyEvent
        """
        event = SafetyEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            event_type=event_type,
            severity=min(max(severity, 1), 5),  # Clamp to 1-5
            description=description,
            source=source,
            metadata=metadata or {}
        )
        
        # Store in memory
        if session_id not in self._session_events:
            self._session_events[session_id] = []
        self._session_events[session_id].append(event)
        
        # Write to file
        await self._append_to_file(event)
        
        logger.info(
            "safety_event_logged",
            event_type=event_type,
            severity=severity,
            session_id=session_id
        )
        
        return event
    
    async def _append_to_file(self, event: SafetyEvent) -> None:
        """Append event to JSON log file (one JSON object per line)"""
        async with self._lock:
            async with aiofiles.open(self.log_path, mode="a", encoding="utf-8") as f:
                await f.write(json.dumps(event.to_dict()) + "\n")
    
    def get_session_events(self, session_id: str) -> list[SafetyEvent]:
        """Get all events for a session"""
        return self._session_events.get(session_id, [])
    
    async def get_session_summary(self, session_id: str) -> dict:
        """Generate summary statistics for a session"""
        events = self.get_session_events(session_id)
        
        if not events:
            return {"session_id": session_id, "total_events": 0}
        
        severity_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        event_types: dict[str, int] = {}
        
        for event in events:
            # Severity should be clamped, but use get for safety
            severity_counts[event.severity] = severity_counts.get(event.severity, 0) + 1
            event_types[event.event_type] = event_types.get(event.event_type, 0) + 1
        
        return {
            "session_id": session_id,
            "total_events": len(events),
            "severity_distribution": severity_counts,
            "event_types": event_types,
            "critical_events": severity_counts[5],
            "high_severity_events": severity_counts[4] + severity_counts[5],
            "first_event": events[0].timestamp,
            "last_event": events[-1].timestamp
        }


# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger(log_path: str = "./logs/audit_log.json") -> AuditLogger:
    """Get or create the global audit logger instance"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(log_path)
    return _audit_logger
