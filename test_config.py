from google.genai import types

try:
    sc = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
        ),
        voice_activity_detection={
            "silence_duration_ms": 2000,
            "prefix_padding_ms": 500
        }
    )
    print("SpeechConfig created SUCCESSFULLY")
except Exception as e:
    print(f"SpeechConfig FAILED: {e}")
