from rest_framework.throttling import AnonRateThrottle


class OtpSendThrottle(AnonRateThrottle):
    scope = "otp_send"


class OtpVerifyThrottle(AnonRateThrottle):
    scope = "otp_verify"


class NewsletterThrottle(AnonRateThrottle):
    scope = "newsletter"
