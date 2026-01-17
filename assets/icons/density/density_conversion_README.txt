# Edgecaster – Density Sprites → RGBA Pipeline

Some ethereal entities (Shadow, Fractal Echo, fog, smoke, etc.) are authored as **density maps**.
Their translucency is derived in post:

- darker pixels = more opaque
- lighter pixels = more transparent
- near-white background = fully transparent

This folder contains:
- `density_to_alpha.py`  (converter)
- `*.png` density sources (editable / regeneratable)
- output `../final/*.png` RGBA sprites (game-ready)

## Folder layout (recommended)

assets/
  sprites/
    density/
      density_to_alpha.py
      convert_all.bat
      convert_all.ps1
      Makefile
      shadow_density.png
      fractal_echo_density.png
    final/
      shadow.png
      fractal_echo.png

## Install deps (once)

Windows (recommended):
    py -m pip install pillow numpy

Mac/Linux:
    python3 -m pip install pillow numpy

## Convert one sprite

Example: Shadow (more midtone solidity)
    py density_to_alpha.py shadow_density.png ../final/shadow.png --bg 0.92 --gamma 1.20 --rgb grayscale --min_alpha 0.03

Example: Fractal Echo (keep original colors)
    py density_to_alpha.py fractal_echo_density.png ../final/fractal_echo.png --bg 0.92 --gamma 1.35 --rgb original --min_alpha 0.02

## Convert everything

Windows CMD:
    convert_all.bat

Windows PowerShell:
    .\convert_all.ps1

Mac/Linux (or Windows with make):
    make all

## Canonical rule

- Density files (`*_density.png`) are the **source of truth**
- RGBA files (`../final/*.png`) are **derived outputs**
- If density changes, regenerate RGBA. Always.
