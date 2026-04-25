# import urllib.request

from app.config import settings

# urllib.request.install_opener(
#     urllib.request.build_opener(
#         urllib.request.ProxyHandler(
#             {
#                 "http": settings.proxy_server_url,
#                 "https": settings.proxy_server_url,
#             }
#         )
#     )
# )

from browserforge.download import Download
from browserforge.download import REMOTE_PATHS


def download():
    REMOTE_PATHS.update(
        {
            "headers": settings.github_proxy_url + REMOTE_PATHS.get("headers"),
            "fingerprints": settings.github_proxy_url
            + REMOTE_PATHS.get("fingerprints"),
        }
    )
    Download(headers=True, fingerprints=True)


if __name__ == "__main__":
    download()
