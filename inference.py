import argparse
import gc
import os
import random
import warnings

import librosa
import numpy as np
import pyloudnorm as pyln
import soundfile as sf
import torch
from cog import BasePredictor, Input, Path
from scipy import signal

from audiosr import build_model, super_resolution

warnings.filterwarnings("ignore")

os.environ["TOKENIZERS_PARALLELISM"] = "true"
torch.set_float32_matmul_precision("high")


def match_array_shapes(array_1: np.ndarray, array_2: np.ndarray):
    if (len(array_1.shape) == 1) & (len(array_2.shape) == 1):
        if array_1.shape[0] > array_2.shape[0]:
            array_1 = array_1[:array_2.shape[0]]
        elif array_1.shape[0] < array_2.shape[0]:
            array_1 = np.pad(array_1, (array_2.shape[0] - array_1.shape[0], 0), 'constant', constant_values=0)
    else:
        if array_1.shape[1] > array_2.shape[1]:
            array_1 = array_1[:, :array_2.shape[1]]
        elif array_1.shape[1] < array_2.shape[1]:
            padding = array_2.shape[1] - array_1.shape[1]
            array_1 = np.pad(array_1, ((0, 0), (0, padding)), 'constant', constant_values=0)
    return array_1


def lr_filter(audio, cutoff, filter_type, order=12, sr=48000):
    audio = audio.T
    nyquist = 0.5 * sr
    normal_cutoff = cutoff / nyquist
    b, a = signal.butter(order // 2, normal_cutoff, btype=filter_type, analog=False)
    sos = signal.tf2sos(b, a)
    filtered_audio = signal.sosfiltfilt(sos, audio)
    return filtered_audio.T


class Predictor(BasePredictor):
    def __init__(self):
        super().__init__()
        self.model_name = None
        self.device = None
        self.sr = None
        self.audiosr = None

    def setup(self, model_name="basic", device="auto"):
        self.model_name = model_name
        self.device = device
        self.sr = 48000
        print("Loading Model...")
        self.audiosr = build_model(model_name=self.model_name, device=self.device)
        print("Model loaded!")

    def process_audio(self, input_file, chunk_size=5.12, overlap=0.1, seed=None, guidance_scale=3.5, ddim_steps=50):
        audio, sr = librosa.load(input_file, sr=tgt_cutoff * 2, mono=False)
        temp_dir = os.path.join(os.path.dirname(input_file), "temp")
        os.makedirs(temp_dir, exist_ok=True)
        audio = audio.T
        sr = tgt_cutoff * 2
        print(f"audio.shape = {audio.shape}")
        print(f"input cutoff = {tgt_cutoff}")

        is_stereo = len(audio.shape) == 2
        audio_channels = [audio] if not is_stereo else [audio[:, 0], audio[:, 1]]
        print("audio is stereo" if is_stereo else "Audio is mono")

        chunk_samples = int(chunk_size * sr)
        overlap_samples = int(overlap * chunk_samples)
        output_chunk_samples = int(chunk_size * self.sr)
        output_overlap_samples = int(overlap * output_chunk_samples)
        enable_overlap = overlap > 0
        print(f"enable_overlap = {enable_overlap}")

        def process_chunks(audio):
            chunks = []
            original_lengths = []
            start = 0
            while start < len(audio):
                end = min(start + chunk_samples, len(audio))
                chunk = audio[start:end]
                if len(chunk) < chunk_samples:
                    original_lengths.append(len(chunk))
                    chunk = np.concatenate([chunk, np.zeros(chunk_samples - len(chunk))])
                else:
                    original_lengths.append(chunk_samples)
                chunks.append(chunk)
                start += chunk_samples - overlap_samples if enable_overlap else chunk_samples
            return chunks, original_lengths

        # Process both channels (mono or stereo)
        chunks_per_channel = [process_chunks(channel) for channel in audio_channels]
        sample_rate_ratio = self.sr / sr
        total_length = len(chunks_per_channel[0][0]) * output_chunk_samples - (len(chunks_per_channel[0][0]) - 1) * (
            output_overlap_samples if enable_overlap else 0)
        reconstructed_channels = [np.zeros((1, total_length)) for _ in audio_channels]

        meter_before = pyln.Meter(sr)
        meter_after = pyln.Meter(self.sr)

        # Process chunks for each channel
        for ch_idx, (chunks, original_lengths) in enumerate(chunks_per_channel):
            for i, chunk in enumerate(chunks):
                temp_wav = os.path.join(temp_dir, f"chunk{ch_idx}_{i}.wav")
                loudness_before = meter_before.integrated_loudness(chunk)
                print(
                    f"Processing chunk {i + 1} of {len(chunks)} for {'Left/Mono' if ch_idx == 0 else 'Right'} channel")
                if not isinstance(chunk, np.ndarray):
                    raise ValueError("Audio chunk must be a NumPy array.")
                if not isinstance(sr, int) or sr <= 0:
                    raise ValueError("Sample rate must be a positive integer.")
                sf.write(temp_wav, chunk, sr)

                out_chunk = super_resolution(
                    self.audiosr,
                    temp_wav,
                    seed=seed,
                    guidance_scale=guidance_scale,
                    ddim_steps=ddim_steps,
                    latent_t_per_second=12.8
                )

                out_chunk = out_chunk[0]
                num_samples_to_keep = int(original_lengths[i] * sample_rate_ratio)
                out_chunk = out_chunk[:, :num_samples_to_keep].squeeze()
                loudness_after = meter_after.integrated_loudness(out_chunk)
                out_chunk = pyln.normalize.loudness(out_chunk, loudness_after, loudness_before)

                if enable_overlap:
                    actual_overlap_samples = min(output_overlap_samples, num_samples_to_keep)
                    fade_out = np.linspace(1., 0., actual_overlap_samples)
                    fade_in = np.linspace(0., 1., actual_overlap_samples)

                    if i == 0:
                        out_chunk[-actual_overlap_samples:] *= fade_out
                    elif i < len(chunks) - 1:
                        out_chunk[:actual_overlap_samples] *= fade_in
                        out_chunk[-actual_overlap_samples:] *= fade_out
                    else:
                        out_chunk[:actual_overlap_samples] *= fade_in

                    start = i * (
                        output_chunk_samples - output_overlap_samples if enable_overlap else output_chunk_samples)
                    end = start + out_chunk.shape[0]
                    reconstructed_channels[ch_idx][0, start:end] += out_chunk.flatten()

        reconstructed_audio = np.stack(reconstructed_channels, axis=-1) if is_stereo else reconstructed_channels[0]

        if tgt_ensemble:
            low, _ = librosa.load(input_file, sr=48000, mono=False)
            output = match_array_shapes(reconstructed_audio[0].T, low)
            low = lr_filter(low.T, crossover_freq, 'lowpass', order=10)
            high = lr_filter(output.T, crossover_freq, 'highpass', order=10)
            high = lr_filter(high, 23000, 'lowpass', order=2)
            output = low + high
        else:
            output = reconstructed_audio[0]
        # Delete temp dir and files
        for file in os.listdir(temp_dir):
            os.remove(os.path.join(temp_dir, file))
        os.rmdir(temp_dir)
        # print(output, type(output))
        return output

    def predict(self,
                input: Path = Input(description="Audio to upsample"),
                ddim_steps: int = Input(description="Number of inference steps", default=50, ge=10, le=500),
                guidance_scale: float = Input(description="Scale for classifier free guidance", default=3.5, ge=1.0,
                                              le=20.0),
                overlap: float = Input(description="overlap size", default=0.04),
                chunk_size: float = Input(description="chunksize", default=10.24),
                seed: int = Input(description="Random seed. Leave blank to randomize the seed", default=None)
                ) -> Path:

        if seed == 0:
            seed = random.randint(0, 2 ** 32 - 1)
        print(f"Setting seed to: {seed}")
        print(f"overlap = {overlap}")
        print(f"guidance_scale = {guidance_scale}")
        print(f"ddim_steps = {ddim_steps}")
        print(f"chunk_size = {chunk_size}")
        print(f"multiband_ensemble = {tgt_ensemble}")
        print(f"input file = {os.path.basename(input)}")
        os.makedirs(dest_folder, exist_ok=True)
        waveform = self.process_audio(
            input,
            chunk_size=chunk_size,
            overlap=overlap,
            seed=seed,
            guidance_scale=guidance_scale,
            ddim_steps=ddim_steps
        )

        filename = os.path.splitext(os.path.basename(input))[0]
        sf.write(f"{dest_folder}/SR_{filename}.wav", data=waveform, samplerate=48000, subtype="PCM_16")
        print(f"file created: {dest_folder}/SR_{filename}.wav")
        del self.audiosr, waveform
        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find volume difference of two audio files.")
    parser.add_argument("--input", help="Path to input audio file")
    parser.add_argument("--output", help="Output folder")
    parser.add_argument("--ddim_steps", help="Number of ddim steps", type=int, required=False, default=50)
    parser.add_argument("--chunk_size", help="chunk size", type=float, required=False, default=10.24)
    parser.add_argument("--guidance_scale", help="Guidance scale value", type=float, required=False, default=3.5)
    parser.add_argument("--seed", help="Seed value, 0 = random seed", type=int, required=False, default=0)
    parser.add_argument("--overlap", help="overlap value", type=float, required=False, default=0.04)
    parser.add_argument("--multiband_ensemble", type=bool, help="Use multiband ensemble with input")
    parser.add_argument("--input_cutoff", help="Define the crossover of audio input in the multiband ensemble",
                        type=int, required=False, default=12000)

    args = parser.parse_args()

    tgt_file = args.input
    dest_folder = args.output
    if not dest_folder:
        dest_folder = os.path.dirname(tgt_file)
    steps = args.ddim_steps
    tgt_chunk_size = args.chunk_size
    tgt_scale = args.guidance_scale
    tgt_seed = args.seed
    tgt_overlap = args.overlap
    tgt_cutoff = args.input_cutoff
    tgt_ensemble = args.multiband_ensemble

    crossover_freq = tgt_cutoff - 1000

    p = Predictor()

    p.setup(device='auto')

    out = p.predict(
        tgt_file,
        ddim_steps=steps,
        guidance_scale=tgt_scale,
        seed=tgt_seed,
        chunk_size=tgt_chunk_size,
        overlap=tgt_overlap
    )

    del p
    gc.collect()
    torch.cuda.empty_cache()
