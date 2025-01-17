#!/usr/bin/env python
# -*- coding: utf-8 -*-

import io
import os
import sys
from shutil import rmtree
from setuptools import find_packages, setup, Command

# Package meta-data
NAME = "audiosr"
DESCRIPTION = "This package is written for text-to-audio/music generation."
URL = "https://github.com/haoheliu/audiosr"
EMAIL = "haoheliu@gmail.com"
AUTHOR = "Haohe Liu"
REQUIRES_PYTHON = ">=3.9.0"
VERSION = "0.0.8"

# Required packages for this module
REQUIRED = [
    "chardet",
    "cog",
    "einops",
    "ftfy",
    "gradio",
    "huggingface_hub",
    "librosa",
    "numpy<=1.23.5",
    "pandas",
    "phonemizer",
    "progressbar",
    "pyloudnorm",
    "pyyaml",
    "scipy",
    "soundfile",
    "timm",
    "torch>=2.4.0",
    "torchaudio>=2.4.0",
    "torchlibrosa>=0.0.9",
    "torchvision==0.19.0",
    "tqdm",
    "transformers>=4.30.2",
    "unidecode",
]

# Optional packages
EXTRAS = {}

# Determine the current directory
here = os.path.abspath(os.path.dirname(__file__))

# Read the README.md for the long description
try:
    with io.open(os.path.join(here, "README.md"), encoding="utf-8") as f:
        long_description = f.read()
except FileNotFoundError:
    long_description = DESCRIPTION

# Retrieve the package version
about = {"__version__": VERSION}


class UploadCommand(Command):
    """Support setup.py upload."""

    description = "Build and publish the package."
    user_options = []

    @staticmethod
    def status(s):
        """Prints things in bold."""
        print(f"\033[1m{s}\033[0m")

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        try:
            self.status("Removing previous builds...")
            rmtree(os.path.join(here, "dist"))
        except OSError:
            pass

        self.status("Building Source and Wheel (universal) distribution...")
        os.system(f"{sys.executable} setup.py sdist bdist_wheel")

        self.status("Uploading the package to PyPI via Twine...")
        os.system("twine upload dist/*")

        self.status("Pushing git tags...")
        os.system(f"git tag v{about['__version__']}")
        os.system("git push --tags")

        sys.exit()


# Setup configuration
setup(
    name=NAME,
    version=about["__version__"],
    description=DESCRIPTION,
    long_description=long_description,
    long_description_content_type="text/markdown",
    author=AUTHOR,
    author_email=EMAIL,
    python_requires=REQUIRES_PYTHON,
    url=URL,
    packages=find_packages(),
    install_requires=REQUIRED,
    extras_require=EXTRAS,
    include_package_data=True,
    license="MIT",
    classifiers=[
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: Implementation :: CPython",
    ],
    cmdclass={
        "upload": UploadCommand,
    },
    scripts=["bin/audiosr.cmd", "bin/audiosr"],
)
