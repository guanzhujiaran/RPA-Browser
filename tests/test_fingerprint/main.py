import uuid

from app.models.RPA_browser.browser_info_model import UserBrowserInfoCreateParams
from app.services.broswer_fingerprint.fingerprint_gen import gen_from_browserforge_fingerprint

bpp = gen_from_browserforge_fingerprint(params=UserBrowserInfoCreateParams(
    browser_token=uuid.uuid4()
)
)
print(bpp.patchright_fingerprint_dict)

print(bpp.browserforge_fingerprint_object)

print(type(bpp.browserforge_fingerprint_object.navigator.userAgentData))