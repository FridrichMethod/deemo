# Upstream historical processing scripts

**English** · [简体中文](README.zh-CN.md)

This directory keeps, unchanged, the three experimental scripts from the original repository's `pil/` directory, to explain how the inherited assets were processed:

- `pil.py`: turns pure-white pixels transparent and generates images composited with a background.
- `pix.py`: a single-image experiment that turns pure white transparent.
- `rgb.py`: an experiment using color-channel masks.

These files depend on the original author's relative paths at the time (for example `saurce/`, `bg.png`) and on experimental inputs, some of which were not shipped with the repository. They are kept as historical sources: they play no part in the current fetching or build, and they were not run during this reorganization.

The current gallery is built with `python scripts/build_catalog.py --verify`, which does not re-encode the assets or remove their backgrounds. The original paths and content hashes of the old scripts are recorded in [data/legacy-inventory.json](../../data/legacy-inventory.json).
