def classFactory(iface):
    from .plugin import OpenAQPlugin
    return OpenAQPlugin(iface)
