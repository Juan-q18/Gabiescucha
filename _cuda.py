import glob
import logging
import os
import site

log = logging.getLogger(__name__)

_handles = []


def prepare_nvidia_dlls() -> None:
    """Expone los DLL de nvidia (cuBLAS/cuDNN/CUDA runtime) de los wheels pip al loader de Windows."""
    if os.name != "nt":
        return
    try:
        sp = [p for p in site.getsitepackages() if p.endswith("site-packages")]
        if not sp:
            return
        base = os.path.join(sp[0], "nvidia")
        if not os.path.isdir(base):
            return
        bins = {os.path.dirname(p) for p in glob.glob(os.path.join(base, "*", "bin", "*.dll"))}
        os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")
        for b in bins:
            try:
                _handles.append(os.add_dll_directory(b))
            except Exception:
                pass
        log.info("Directorio de DLL de nvidia preparado (%d)", len(bins))
    except Exception:
        log.exception("No se pudieron exponer los DLL de nvidia")


prepare_nvidia_dlls()
