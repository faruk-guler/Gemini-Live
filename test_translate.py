import asyncio
import os
from google import genai
from google.genai import types

async def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(http_options={"api_version": "v1beta"}, api_key=api_key)
    
    config = types.LiveConnectConfig(
        translation_config=types.TranslationConfig(target_language_code="en"),
        response_modalities=["AUDIO"],
    )
    print("Connecting...")
    try:
        async with client.aio.live.connect(model="models/gemini-3.5-live-translate-preview", config=config) as session:
            print("Connected!")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
