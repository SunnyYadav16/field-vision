from google import genai
from google.genai import types

print("Checking for VoiceActivityDetection in types...")
if hasattr(types, 'VoiceActivityDetection'):
    print("VoiceActivityDetection FOUND in types")
else:
    print("VoiceActivityDetection NOT FOUND in types")

print("\nSpeechConfig Attributes:")
try:
    s = types.SpeechConfig()
    print([x for x in dir(s) if not x.startswith('_')])
except Exception as e:
    print(f"Error making SpeechConfig: {e}")
