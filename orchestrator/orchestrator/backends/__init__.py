from .base import Backend

NAMES = ("fake", "local", "slices")


def get_backend(name, **kw):
    """Lazy import: avoids circular imports and does not load unused backends."""
    if name == "fake":
        from .fake import FakeBackend as cls
    elif name == "local":
        from .local import LocalBackend as cls
    elif name == "slices":
        from ..slices_adapter import SlicesBackend as cls
    else:
        raise ValueError(f"backend sconosciuto '{name}' (disponibili: {', '.join(NAMES)})")
    return cls(**kw)
