import urllib.request

from app.config import settings

urllib.request.install_opener(
    urllib.request.build_opener(
        urllib.request.ProxyHandler({
            'http': settings.proxy_server_url,
            'https': settings.proxy_server_url,
        })
    )
)

from browserforge.download import Download

Download(headers=True, fingerprints=True)
