from enum import StrEnum


class RouterPrefix(StrEnum):
    browser = "/browser"
    browser_control = "/browser_control"


class BrowserRouterPath(StrEnum):
    gen_rand_fingerprint = "/gen_rand_fingerprint"
    create_fingerprint = '/create_fingerprint'
    read_fingerprint = '/read_fingerprint'
    update_fingerprint = '/update_fingerprint'
    delete_fingerprint = '/delete_fingerprint'
    count_fingerprint = '/count_fingerprint'
    list_fingerprint = '/list_fingerprint'


class BrowserControlRouterPath(StrEnum):
    open_url = '/open_url'
    screenshot = '/screenshot'
    release = '/release_session'
    live_create = '/live/create'
    live_view = '/live/view'
    live_stream = '/live/stream'
    live_ws = '/live/ws'
    live_stop = '/live/stop'
