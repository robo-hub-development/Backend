from .models import Banner


def active_banners():
    return Banner.objects.filter(is_active=True)
