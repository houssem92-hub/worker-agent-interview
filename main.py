import os
import wave
import asyncio
import datetime
from fastapi import FastAPI, HTTPException
from livekit import rtc
from livekit.api import AccessToken, VideoGrants

app = FastAPI()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audio")

SUPPORTED_DURATIONS = {10, 12, 15}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/agent/start")
async def start(payload: dict):
    room_name = payload.get("roomName")
    candidate_name = payload.get("candidateName")
    public_id = payload.get("publicId")
    duration_minutes = payload.get("durationMinutes")

    if not room_name or not candidate_name or not public_id:
        raise HTTPException(status_code=400, detail="roomName, candidateName et publicId sont obligatoires.")

    if duration_minutes is None:
        raise HTTPException(status_code=400, detail="durationMinutes est obligatoire.")

    try:
        duration_minutes = int(duration_minutes)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="durationMinutes doit être un entier.")

    if duration_minutes not in SUPPORTED_DURATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"durationMinutes invalide. Valeurs autorisées: {sorted(SUPPORTED_DURATIONS)}"
        )

    print(
        f"room={room_name}, candidate_name={candidate_name}, "
        f"public_id={public_id}, duration_minutes={duration_minutes}"
    )

    asyncio.create_task(
        run_agent(room_name, candidate_name, public_id, duration_minutes)
    )

    return {"status": "started"}


async def run_agent(
        room_name: str,
        candidate_name: str,
        public_id: str,
        duration_minutes: int
):
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

    # 1) Choix dynamique de l'intro selon la durée
    intro_file = get_intro_file(duration_minutes)
    await play_wav_file(source, intro_file)

    # 2) Attendre jusqu'à 30 secondes avant la fin
    warning_delay = max(duration_minutes * 60 - 30, 0)
    await asyncio.sleep(warning_delay)

    # 3) Annonce "il vous reste 30 secondes"
    await play_wav_file(source, os.path.join(AUDIO_DIR, "remaining_30s.wav"))

    # 4) Attente 30 secondes
    await asyncio.sleep(30)

    # 5) Annonce de fin
    await play_wav_file(source, os.path.join(AUDIO_DIR, "end.wav"))

    await asyncio.sleep(1)
    await room.disconnect()


def get_intro_file(duration_minutes: int) -> str:
    mapping = {
        10: "intro_10.wav",
        12: "intro_12.wav",
        15: "intro_15.wav",
    }

    filename = mapping.get(duration_minutes)
    if not filename:
        raise ValueError(f"Aucun fichier d'introduction défini pour {duration_minutes} minutes.")

    filepath = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Fichier audio introuvable: {filepath}")

    return filepath


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