import asyncio
from io import BytesIO
import time

import aiohttp
import edge_tts
import numpy as np
import resampy
import soundfile as sf

from utils.logger import logger
from .base_tts import BaseTTS, State
from registry import register


@register("tts", "edgetts")
class EdgeTTS(BaseTTS):
    def _active(self, event):
        return (self.state == State.RUNNING and
                ("live_run_id" not in event or event["live_run_id"] == getattr(self.parent, "live_run_id", None)))

    def txt_to_audio(self, msg: tuple[str, dict]):
        text, event = msg
        voice = event.get("tts", {}).get("ref_file") or self.opt.REF_FILE or "zh-CN-YunxiaNeural"
        started = time.monotonic()
        audio = asyncio.run(self._synthesize(text, voice, event))
        logger.info("-------edge tts time:%.4fs", time.monotonic() - started)
        if not audio or not self._active(event):
            return
        samples, sample_rate = sf.read(BytesIO(audio), dtype="float32")
        if samples.ndim > 1:
            samples = samples[:, 0]
        if sample_rate != self.sample_rate:
            samples = resampy.resample(samples, sr_orig=sample_rate, sr_new=self.sample_rate)
        if not len(samples):
            raise RuntimeError("EdgeTTS returned empty audio")
        # Preserve the tail and both playback events for very short utterances.
        frames = max(2, (len(samples) + self.chunk - 1) // self.chunk)
        samples = np.pad(samples, (0, frames * self.chunk - len(samples)))
        for index in range(frames):
            if not self._active(event):
                break
            metadata = dict(event)
            if index == 0:
                metadata.update(status="start", text=text)
            if index == frames - 1:
                metadata.update(status="end", text=text)
            self.parent.put_audio_frame(samples[index * self.chunk:(index + 1) * self.chunk], metadata)

    async def _synthesize(self, text, voice, event):
        for attempt in range(3):
            if not self._active(event):
                return None
            audio = bytearray()
            try:
                communicate = edge_tts.Communicate(text, voice, connect_timeout=10, receive_timeout=15)
                async for chunk in communicate.stream():
                    if not self._active(event):
                        return None
                    if chunk["type"] == "audio":
                        audio.extend(chunk["data"])
                if not audio:
                    raise edge_tts.exceptions.NoAudioReceived("Empty audio")
                return bytes(audio)
            except (edge_tts.exceptions.NoAudioReceived, aiohttp.ClientError, asyncio.TimeoutError):
                if not self._active(event):
                    return None
                if attempt == 2:
                    raise
                logger.warning("EdgeTTS request failed; retry %d/2 (voice=%s)", attempt + 1, voice)
                await asyncio.sleep(0.4 * (attempt + 1))
