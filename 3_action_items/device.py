"""Which torch device to use: Apple's MPS if available, else CPU (LIVEMINUTES_DEVICE=cpu|mps|cuda overrides)."""


def pick_device(wanted="auto"):
    import torch
    if wanted and wanted != "auto":
        return wanted
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"
