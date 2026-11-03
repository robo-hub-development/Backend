from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class OtpSendThrottle(AnonRateThrottle):
    scope = "otp_send"


class OtpVerifyThrottle(AnonRateThrottle):
    scope = "otp_verify"


class NewsletterThrottle(AnonRateThrottle):
    scope = "newsletter"


class OrderPlaceThrottle(UserRateThrottle):
    scope = "order_place"
