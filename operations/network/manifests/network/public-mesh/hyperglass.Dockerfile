FROM registry.re8ch.com/qianwen/hyperglass:2.0.4-re8ch4

# v2.0.4 recomputes its frontend fingerprint at every start. Runtime-only
# settings can therefore trigger an expensive Next.js rebuild even though this
# image already contains the validated static export. This opt-in guard keeps
# the default upstream behaviour and skips only when the baked export exists.
RUN sed -i \
      '1a import os\nfrom pathlib import Path' \
      /opt/hyperglass/hyperglass/main.py \
    && sed -i \
      '/    if not Settings.disable_ui:/i\    prebuilt_ui = os.getenv("HYPERGLASS_USE_PREBUILT_UI", "").lower() == "true" and Path(Settings.app_path, "static", "ui", "index.html").is_file()' \
      /opt/hyperglass/hyperglass/main.py \
    && sed -i \
      's/    if not Settings.disable_ui:/    if not Settings.disable_ui and not prebuilt_ui:/' \
      /opt/hyperglass/hyperglass/main.py
