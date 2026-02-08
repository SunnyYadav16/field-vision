"""
FieldVision Manual Loader
Loads and manages technical manual context for AI grounding
"""

from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)

# Default manual path
DEFAULT_MANUAL_PATH = Path(__file__).parent.parent / "manuals" / "safety_manual.md"


class ManualLoader:
    """Loads and caches technical manual content for context injection"""
    
    _instance: Optional["ManualLoader"] = None
    _cache: dict[str, str] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def load_manual(self, path: Optional[Path] = None) -> Optional[str]:
        """
        Load manual content from file.
        
        Args:
            path: Path to manual file (uses default if None)
            
        Returns:
            Manual content as string, or None if load fails
        """
        manual_path = path or DEFAULT_MANUAL_PATH
        cache_key = str(manual_path)
        
        # Return cached version if available
        if cache_key in self._cache:
            logger.debug("manual_cache_hit", path=cache_key)
            return self._cache[cache_key]
        
        try:
            if not manual_path.exists():
                logger.warning("manual_not_found", path=str(manual_path))
                return None
            
            content = manual_path.read_text(encoding="utf-8")
            
            # Validate content length (>1024 tokens for context caching benefit)
            if len(content) < 500:
                logger.warning("manual_too_short", 
                             path=str(manual_path), 
                             length=len(content))
            
            self._cache[cache_key] = content
            logger.info("manual_loaded", 
                       path=str(manual_path), 
                       chars=len(content),
                       estimated_tokens=len(content) // 4)
            return content
            
        except Exception as e:
            logger.error("manual_load_error", path=str(manual_path), error=str(e))
            return None
    
    def clear_cache(self) -> None:
        """Clear the manual cache"""
        self._cache.clear()
        logger.info("manual_cache_cleared")
    
    def get_default_manual(self) -> Optional[str]:
        """Load the default safety manual"""
        return self.load_manual(DEFAULT_MANUAL_PATH)


def get_manual_loader() -> ManualLoader:
    """Get the singleton manual loader instance"""
    return ManualLoader()


def validate_manual_context(context: Optional[str]) -> tuple[bool, str]:
    """
    Validate manual context before sending to API.
    
    Args:
        context: Manual content to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if context is None:
        return True, ""  # None is valid (no manual)
    
    if not isinstance(context, str):
        return False, "Manual context must be a string"
    
    if len(context) > 100000:  # ~25k tokens max
        return False, f"Manual too large: {len(context)} chars (max 100000)"
    
    # Check for potentially problematic content
    if "<script" in context.lower():
        return False, "Manual contains potentially unsafe content"
    
    return True, ""
