class BeautyCutError(Exception):
    """Erreur compréhensible remontée à l'interface."""


class ToolNotFoundError(BeautyCutError):
    pass


class MediaProbeError(BeautyCutError):
    pass


class MediaExportError(BeautyCutError):
    pass


class ExportCancelled(BeautyCutError):
    pass
