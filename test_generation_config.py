from google.genai import types

try:
    gc = types.GenerationConfig(
        speech_config={
            "voice_activity_detection": {
                "silence_duration_ms": 2000,
                "prefix_padding_ms": 500
            }
        }
    )
    print("GenerationConfig (dict) Success")
except Exception as e:
    print(f"GenerationConfig (dict) FAILED: {e}")
