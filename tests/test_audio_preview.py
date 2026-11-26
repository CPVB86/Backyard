import array
import io
import math
import sys
import wave
from unittest.mock import patch
import pytest
from app.modules.observations.preview import derive
from test_observations_api import client, create, upload


def tone(rate=96000, frequency=20000):
    values = array.array("h",(round(1000*math.sin(2*math.pi*frequency*n/rate)) for n in range(rate//10)))
    if sys.byteorder != "little":
        values.byteswap()
    stream=io.BytesIO()
    with wave.open(stream,"wb") as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(values.tobytes())
    return stream.getvalue()


def test_bat_time_expansion_and_bounded_gain():
    source=tone()
    audio, meta=derive(source,"bat")
    with wave.open(io.BytesIO(audio)) as w:
        assert w.getframerate()==9600
        assert w.getnframes()==9600
    assert meta["duration_seconds"]==.1
    assert meta["playback_duration_seconds"]==1
    assert meta["gain_db"]<=24.09
    assert meta["frequency_max_hz"]==48000
    assert meta["spectrogram_png"].startswith("iVBOR")
    assert len(meta["waveform"])==256


def test_bird_speed_unchanged():
    audio,meta=derive(tone(),"bird")
    with wave.open(io.BytesIO(audio)) as w:
        assert w.getframerate()==96000
    assert meta["time_expansion"]==1


def test_auth_cache_original_and_removed_evidence(client):
    item,original=upload(client,create(client))
    base=f"/api/observations/{item['id']}"
    assert client.get(base+"/audio-view",headers={"Authorization":"Bearer wrong"}).status_code==401
    result=client.get(base+"/audio-view")
    assert result.status_code==200, result.text
    value=result.json()
    assert value["detection_window"]["exact_call_location"] is False
    assert value["detection_window"]["start_seconds"]==0
    assert client.get(base+"/audio").content==original
    with patch("app.modules.observations.preview.derive",side_effect=AssertionError("cache miss")):
        assert client.get(base+"/audio-listen").status_code==200
    from sqlalchemy.orm import Session
    from app.modules.observations.models import Observation
    with Session(client.app.state.engine) as session:
        record=session.get(Observation,item["id"])
        record.evidence_kind="deleted"
        session.commit()
    assert client.get(base+"/audio-view").status_code==404
    assert client.get(base+"/audio-listen").status_code==404


def test_short_ultrasonic_pulse_is_visible_at_original_frequency():
    import base64
    import numpy as np
    from PIL import Image
    rate=96000
    samples=np.zeros(rate,dtype="<i2")
    positions=np.arange(192)
    samples[48000:48192]=(10000*np.sin(2*np.pi*20000*positions/rate)).astype("<i2")
    source=io.BytesIO()
    with wave.open(source,"wb") as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate);w.writeframes(samples.tobytes())
    _,meta=derive(source.getvalue(),"bat")
    image=np.array(Image.open(io.BytesIO(base64.b64decode(meta["spectrogram_png"]))))
    # 20 kHz of original 48 kHz Nyquist, around t=.5s.
    assert image[140:155,125:133,0].max()>100
    assert image[:,0,0].max()==0


@pytest.mark.parametrize("domain", ["bird", "bat"])
def test_quiet_signal_has_bright_peaks_and_silence_stays_black(domain):
    import base64
    import numpy as np
    from PIL import Image
    source = tone()
    audio, meta = derive(source, domain)
    pixels = np.array(Image.open(io.BytesIO(base64.b64decode(meta["spectrogram_png"]))))
    assert pixels[:, :, 1].max() >= 240
    assert meta["spectrogram_dbfs_max"] < 0
    assert meta["spectrogram_dbfs_max"] - meta["spectrogram_dbfs_min"] == 60
    silent = io.BytesIO()
    with wave.open(silent, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(96000)
        wav.writeframes(bytes(19200))
    _, meta = derive(silent.getvalue(), domain)
    pixels = np.array(Image.open(io.BytesIO(base64.b64decode(meta["spectrogram_png"]))))
    assert not pixels.any()
