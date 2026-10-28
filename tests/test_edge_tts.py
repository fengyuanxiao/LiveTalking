import asyncio
from io import BytesIO
from types import SimpleNamespace
import unittest
import threading
from unittest.mock import AsyncMock, patch

import edge_tts
import numpy as np
import soundfile as sf

from tts.edge import EdgeTTS
from avatars.base_avatar import BaseAvatar
from tts.base_tts import BaseTTS


class EdgeTTSTests(unittest.TestCase):
    def make_tts(self):
        frames = []
        parent = SimpleNamespace(live_run_id="current", put_audio_frame=lambda samples, event: frames.append((samples, event)))
        return EdgeTTS(SimpleNamespace(fps=25, REF_FILE=""), parent), frames

    def test_retry_discards_partial_audio_and_returns_only_successful_attempt(self):
        tts, _ = self.make_tts()
        calls = []

        class Communicate:
            def __init__(self, *args, **kwargs):
                calls.append(args)
                self.attempt = len(calls)

            async def stream(self):
                if self.attempt == 1:
                    yield {"type": "audio", "data": b"partial"}
                    raise edge_tts.exceptions.NoAudioReceived("transient")
                yield {"type": "audio", "data": b"complete"}

        with patch("tts.edge.edge_tts.Communicate", Communicate), patch("tts.edge.asyncio.sleep", new_callable=AsyncMock):
            audio = asyncio.run(tts._synthesize("hello", "voice", {}))
        self.assertEqual(audio, b"complete")
        self.assertEqual(len(calls), 2)

    def test_permanent_failure_is_bounded(self):
        tts, _ = self.make_tts()
        calls = []

        class Communicate:
            def __init__(self, *args, **kwargs):
                calls.append(1)

            async def stream(self):
                if False:
                    yield
                raise edge_tts.exceptions.NoAudioReceived("failure")

        with patch("tts.edge.edge_tts.Communicate", Communicate), patch("tts.edge.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaises(edge_tts.exceptions.NoAudioReceived):
                asyncio.run(tts._synthesize("hello", "voice", {}))
        self.assertEqual(len(calls), 3)

    def test_tiny_audio_has_start_and_end_and_preserves_samples(self):
        tts, frames = self.make_tts()
        audio = BytesIO()
        sf.write(audio, np.full(10, 0.25), 16000, format="WAV", subtype="FLOAT")
        with patch.object(tts, "_synthesize", new=AsyncMock(return_value=audio.getvalue())):
            tts.txt_to_audio(("短句", {"live_run_id": "current", "live_turn_id": "turn"}))
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0][1]["status"], "start")
        self.assertEqual(frames[-1][1]["status"], "end")
        np.testing.assert_allclose(frames[0][0][:10], 0.25)

    def test_late_audio_from_stopped_run_is_discarded_at_avatar_boundary(self):
        calls = []
        avatar = BaseAvatar.__new__(BaseAvatar)
        avatar.live_run_id = "new"
        avatar.asr = SimpleNamespace(put_audio_frame=lambda *args: calls.append(args))
        avatar.put_audio_frame(np.zeros(320), {"live_run_id": "old"})
        avatar.put_audio_frame(np.zeros(320), {})
        self.assertEqual(calls, [])
        avatar.put_audio_frame(np.zeros(320), {"live_run_id": "new"})
        self.assertEqual(len(calls), 1)

    def test_synthesis_exception_reports_failure_and_worker_processes_next_message(self):
        events, processed = [], []
        quit_event = threading.Event()
        tts = BaseTTS(SimpleNamespace(fps=25), SimpleNamespace(send_msg=events.append))

        def synthesize(msg):
            processed.append(msg[0])
            if msg[0] == "fail":
                raise RuntimeError("test failure")
            quit_event.set()

        tts.txt_to_audio = synthesize
        tts.put_msg_txt("fail", {"live_turn_id": "failed-turn"})
        tts.put_msg_txt("next")
        tts.process_tts(quit_event)
        self.assertEqual(processed, ["fail", "next"])
        import json
        self.assertEqual(json.loads(events[0])["status"], "tts_error")


if __name__ == "__main__":
    unittest.main()
