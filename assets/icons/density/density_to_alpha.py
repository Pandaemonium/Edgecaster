from PIL import Image
import numpy as np
import argparse

def convert(
    src_path: str,
    out_path: str,
    bg_luma_threshold: float = 0.92,  # pixels brighter than this become transparent (white-key uses this too)
    gamma: float = 1.35,              # higher = more transparent midtones (density mode only)
    rgb_mode: str = "original",       # "original" or "grayscale" or "black"
    min_alpha: float = 0.02,          # clamp tiny speckle alpha to 0 (density mode only)
    whitekey_only: bool = False,      # NEW: only remove near-white background, preserve all RGB/detail
    white_rgb_threshold: float = 0.96,# NEW: per-channel whiteness cutoff (0..1), used in white-key mode
    white_dist_max: float = 0.18      # NEW: max distance-to-white (0..1), helps avoid nuking bright colors
):
    im = Image.open(src_path).convert("RGBA")
    arr = np.array(im).astype(np.float32) / 255.0
    rgb = arr[..., :3]
    a_in = arr[..., 3]  # preserve any existing alpha if present

    # Relative luminance (sRGB-ish)
    luma = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2])

    if whitekey_only:
        # ---- WHITE-KEY ONLY MODE ----
        # Keep RGB exactly; only punch out near-white pixels.
        # Two guards:
        #  1) luma high
        #  2) color close to white (low chroma), so bright saturated pixels survive
        dist_to_white = np.sqrt(((rgb - 1.0) ** 2).sum(axis=2)) / np.sqrt(3.0)

        near_white = (
            (luma >= bg_luma_threshold) &
            (rgb[..., 0] >= white_rgb_threshold) &
            (rgb[..., 1] >= white_rgb_threshold) &
            (rgb[..., 2] >= white_rgb_threshold) &
            (dist_to_white <= white_dist_max)
        )

        alpha = a_in.copy()
        alpha = np.where(near_white, 0.0, alpha)

        out_rgb = rgb  # preserve original colors exactly

    else:
        # ---- ORIGINAL DENSITY->ALPHA MODE ----
        # Derive alpha: darker -> more opaque
        alpha = 1.0 - luma
        alpha = np.clip(alpha, 0.0, 1.0)
        alpha = np.power(alpha, gamma)

        # Respect any input alpha
        alpha = alpha * a_in

        # Remove near-white background aggressively
        alpha = np.where(luma >= bg_luma_threshold, 0.0, alpha)

        # Kill tiny residual speckle (helps with faint artifacts)
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
    p.add_argument("--gamma", type=float, default=1.35, help="alpha gamma (density mode)")
    p.add_argument("--rgb", type=str, default="original", choices=["original", "grayscale", "black"])
    p.add_argument("--min_alpha", type=float, default=0.02)

    # NEW
    p.add_argument("--whitekey_only", action="store_true", help="Only key out near-white pixels; preserve RGB/detail")
    p.add_argument("--white_rgb_threshold", type=float, default=0.96, help="Per-channel whiteness cutoff (0..1)")
    p.add_argument("--white_dist_max", type=float, default=0.18, help="Max distance-to-white (0..1)")

    args = p.parse_args()

    convert(
        args.src, args.out,
        bg_luma_threshold=args.bg,
        gamma=args.gamma,
        rgb_mode=args.rgb,
        min_alpha=args.min_alpha,
        whitekey_only=args.whitekey_only,
        white_rgb_threshold=args.white_rgb_threshold,
        white_dist_max=args.white_dist_max
    )
