"""Thin wrapper over the bundled mtkclient (port of InniUpdaterChin's ``mtk_api``).

Only ``init`` and ``connect`` are used by the flash service; ``main`` is kept
as a debug/demo helper mirroring the original.
"""

import logging
import os

from . import paths

# The bundled mtkclient lives in vendor/ (dev) or the PyInstaller bundle.
paths.ensure_mtkclient_importable()

from mtkclient.Library.DA.mtk_da_handler import DaHandler  # type: ignore
from mtkclient.Library.mtk_class import Mtk  # type: ignore
from mtkclient.config.mtk_config import MtkConfig  # type: ignore


def init(loader=None, preloader=None, serialport=None, loglevel=logging.INFO):
    # ``loglevel`` (lowercase) is the MtkConfig kwarg name; the camelCase
    # variant was a porting typo that made every MTKClient install die with a
    # TypeError. Mirrors the upstream (InniUpdaterChin / mtkclient) signature.
    config = MtkConfig(loglevel=loglevel, gui=None, guiprogress=None)
    config.loader = loader
    if preloader and os.path.exists(preloader):
        config.preloader_filename = preloader
        config.preloader = open(config.preloader_filename, "rb").read()
    mtk = Mtk(config=config, loglevel=loglevel, serialportname=serialport)
    return mtk


def connect(mtk, directory="."):
    da_handler = DaHandler(mtk, logging.INFO)
    mtk = da_handler.connect(mtk, directory)
    if mtk is None:
        return (None, None)
    mtk = da_handler.configure_da(mtk)
    return (mtk, da_handler)


def main():
    """Leftover debug/demo: dump 16384 sectors from offset 0."""
    mtk = init(None, None)
    mtk, da_handler = connect(mtk, directory=".")
    data = da_handler.da_rs(start=0, sectors=16384, filename="", parttype="user", display=False)
    print(data.hex())


if __name__ == "__main__":
    main()
