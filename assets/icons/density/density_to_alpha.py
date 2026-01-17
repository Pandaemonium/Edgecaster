from PIL import Image
import numpy as np
import argparse

def convert(
    src_path: str,
    out_path: str,
    bg_luma_threshold: float = 0.92,  # pixels brighter than this become fully transparent
    gamma: float = 1.35,              # higher = more transparent midtones
    rgb_mode: str = "original",       # "original" or "grayscale" or "black"
    min_alpha: float = 0.02           # clamp tiny speckle alpha to 0
):
    im = Image.open(src_path).convert("RGBA")
    arr = np.array(im).astype(np.float32) / 255.0
    rgb = arr[..., :3]
    a_in = arr[..., 3]  # preserve any existing alpha if present

    # Relative luminance (sRGB-ish)
    luma = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2])

    # Derive alpha: darker -> more opaque
    alpha = 1.0 - luma
    alpha = np.clip(alpha, 0.0, 1.0)
    alpha = np.power(alpha, gamma)

    # Respect any input alpha
    alpha = alpha * a_in

    # Remove near-white background aggressively
    alpha = np.where(luma >= bg_luma_threshold, 0.0, alpha)

    # Kill tiny residual speckle (helps with faint checkerboard artifacts)
    alpha = np.where(alpha < min_alpha, 0.0, alpha)

    # Output RGB handling
    if rgb_mode == "black":
        out_rgb = np.zeros_like(rgb)
    elif rgb_mode == "grayscale":
        out_rgb = np.repeat(luma[..., None], 3, axis=2)
    else:
        out_rgb = rgb

    out = np.dstack([out_rgb, alpha[..., None]])
    out = (np.clip(out, 0, 1) * 255.0).astype(np.uint8)
    Image.fromarray(out, mode="RGBA").save(out_path)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("out")
    p.add_argument("--bg", type=float, default=0.92, help="background luminance threshold (0..1)")
    p.add_argument("--gamma", type=float, default=1.35, help="alpha gamma")
    p.add_argument("--rgb", type=str, default="original", choices=["original", "grayscale", "black"])
    p.add_argument("--min_alpha", type=float, default=0.02)
    args = p.parse_args()

    convert(args.src, args.out, args.bg, args.gamma, args.rgb, args.min_alpha)
