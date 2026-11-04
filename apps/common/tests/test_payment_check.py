from apps.common.checks import payments_are_configured


def test_warns_when_razorpay_keys_are_missing(settings):
    settings.PAYMENT_GATEWAY = "razorpay"
    settings.RAZORPAY_KEY_ID = "k"
    settings.RAZORPAY_KEY_SECRET = ""
    settings.RAZORPAY_WEBHOOK_SECRET = "w"
    assert [w.id for w in payments_are_configured(None)] == ["robohub.W003"]


def test_silent_when_configured_or_using_the_fake_gateway(settings):
    settings.PAYMENT_GATEWAY = "razorpay"
    settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET, settings.RAZORPAY_WEBHOOK_SECRET = "k", "s", "w"
    assert payments_are_configured(None) == []
    settings.PAYMENT_GATEWAY = "fake"
    settings.RAZORPAY_KEY_ID = ""
    assert payments_are_configured(None) == []
