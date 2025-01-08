import os
from pathlib import Path

import numpy as np
import torch


_AUDIOSET_MAP_PATH = os.path.join(Path(__file__).parent, "audioset_textmap.npy")
_AUDIOSET_MAP = np.load(_AUDIOSET_MAP_PATH, allow_pickle=True)
_SHARD_SHUFFLE_SIZE = 2000
_SHARD_SHUFFLE_INITIAL = 500
_SAMPLE_SHUFFLE_SIZE = 5000
_SAMPLE_SHUFFLE_INITIAL = 1000


def get_audio_features(
        audio_data, mel, max_len, data_truncating, data_filling, audio_cfg
):
    """
    Calculate and add audio features to sample.
    Sample: a dict containing all the data of current sample.
    audio_data: a tensor of shape (T) containing audio data.
    max_len: the maximum length of audio data.
    data_truncating: the method of truncating data.
    data_filling: the method of filling data.
    audio_cfg: a dict containing audio configuration. Comes from model_cfg['audio_cfg'].
    """
    sample = {}

    # assert audio_data.size(-1) <= max_len, str(audio_data.size())

    # split to three parts
    chunk_frames = (
            max_len // audio_cfg["hop_size"] + 1
    )  # the +1 related to how the spectrogram is computed
    mel = mel[:chunk_frames]

    audio_data = audio_data[..., :max_len]
    sample["mel_fusion"] = mel
    longer = torch.tensor([True])

    sample["longer"] = longer
    sample["waveform"] = audio_data

    return sample

