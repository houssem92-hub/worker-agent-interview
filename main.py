import os
import wave
import math
import asyncio
import datetime
from fastapi import FastAPI
from livekit import rtc
from livekit.api import AccessToken, VideoGrants

app = FastAPI()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audio")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/agent/start")
async def start(payload: dict):
    room_name = payload["roomName"]
    candidate_name = payload["candidateName"]
    public_id = payload["publicId"]

    print(f"room={room_name}, candidate_name={candidate_name}, public_id={public_id}")

    asyncio.create_task(run_agent(room_name, candidate_name, public_id))
    return {"status": "started"}


async def run_agent(room_name: str, candidate_name: str, public_id: str):
    agent_identity = f"agent-{public_id}"

    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(agent_identity)
        .with_name("Assistant IA")
        .with_kind("agent")
        .with_ttl(datetime.timedelta(hours=1))
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    room = rtc.Room()
    await room.connect(LIVEKIT_URL, token)

    sample_rate = 48000
    num_channels = 1

    source = rtc.AudioSource(sample_rate, num_channels)
    track = rtc.LocalAudioTrack.create_audio_track("assistant-audio", source)

    await room.local_participant.publish_track(track)
    print("Assistant audio track published")

    # 1) message d'intro
    await play_wav_file(source, os.path.join(AUDIO_DIR, "intro.wav"))

    # 2) attente 9 min 30
    await asyncio.sleep(9 * 60 + 30)

    # 3) annonce 30 secondes
    await play_wav_file(source, os.path.join(AUDIO_DIR, "remaining_30s.wav"))

    # 4) attente 30 secondes
    await asyncio.sleep(30)

    # 5) annonce de fin
    await play_wav_file(source, os.path.join(AUDIO_DIR, "end.wav"))

    await asyncio.sleep(1)
    await room.disconnect()


async def play_wav_file(source: rtc.AudioSource, filepath: str):
    """
    Lit un fichier WAV PCM 16-bit mono/48kHz et l'envoie dans la room LiveKit.
    """
    print(f"Playing audio file: {filepath}")

    with wave.open(filepath, "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frame_rate = wf.getframerate()

        if channels != 1:
            raise ValueError(f"{filepath}: must be mono (1 channel), got {channels}")
        if sample_width != 2:
            raise ValueError(f"{filepath}: must be 16-bit PCM, got sample width={sample_width}")
        if frame_rate != 48000:
            raise ValueError(f"{filepath}: must be 48000 Hz, got {frame_rate}")

        samples_per_chunk = 480  # 10 ms à 48kHz
        bytes_per_chunk = samples_per_chunk * channels * sample_width

        while True:
            data = wf.readframes(samples_per_chunk)
            if not data:
                break

            frame = rtc.AudioFrame(
                data=data,
                sample_rate=frame_rate,
                num_channels=channels,
                samples_per_channel=len(data) // (sample_width * channels),
            )

            await source.capture_frame(frame)