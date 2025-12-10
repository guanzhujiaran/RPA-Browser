from browserforge.fingerprints import FingerprintGenerator, Screen

general_conf = {
    "mock_webrtc": True,
}

desktop_fingerprint_generator = FingerprintGenerator(
    browser=['chrome', 'edge'],
    os=['windows'],
    device=['desktop'],
    locale=['zh-CN', 'zh', 'en', 'en-GB', 'en-US'],
    **general_conf
)
mobile_fingerprint_generator = FingerprintGenerator(
    os=['android', 'ios'],
    device=['mobile'],
    locale=['zh-CN', 'zh', 'en', 'en-GB', 'en-US'],
    **general_conf

)

rand_fingerprint_generator = FingerprintGenerator(**general_conf)

__all__ = [
    "desktop_fingerprint_generator",
    "mobile_fingerprint_generator",
    "rand_fingerprint_generator"
]
