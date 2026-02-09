from google.genai import types

sc_dict = {
    "voice_config": types.VoiceConfig(
        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
    ),
    "voice_activity_detection": {
        "silence_duration_ms": 2000,
        "prefix_padding_ms": 500
    }
}
# Try creating speech config via construction
try:
    # Assuming Pydantic v2 compatible
    if hasattr(types.SpeechConfig, 'model_construct'):
        sc = types.SpeechConfig.model_construct(**sc_dict)
        print("Constructed SpeechConfig OK")
        # Check if dump keeps it
        print("Dump:", sc.model_dump(exclude_unset=True))
    else:
        print("No model_construct")
except Exception as e:
    print(f"Construct SpeechConfig Failed: {e}")

# Now create GenerationConfig with it
try:
    if 'sc' in locals():
        gc = types.GenerationConfig(speech_config=sc) # This might validate
        print("GenerationConfig OK")
except Exception as e:
    print(f"GenerationConfig Failed: {e}")
    # Try constructing GenerationConfig too
    try:
        gc2 = types.GenerationConfig.model_construct(speech_config=sc)
        print("Constructed GenerationConfig OK")
        print("Dump GC:", gc2.model_dump(exclude_unset=True))
    except Exception as e2:
        print(f"Construct GenerationConfig Failed: {e2}")
