from django.conf import settings
from .models import Invoice, UserProfile


def get_profile_for_user(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    # Re-assert main admin if email matches configured MAIN_ADMIN_EMAIL
    try:
        if user.email and user.email.lower() == settings.MAIN_ADMIN_EMAIL.lower() and not profile.is_main_admin:
            profile.is_main_admin = True
            profile.save(update_fields=['is_main_admin'])
    except Exception:
        # Be conservative if settings missing; don't crash
        pass
    return profile


def get_visible_accounts(request):
    """Return only the accounts owned by the logged-in user.
    Used for enforcing ownership on create/update/delete operations.
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return Invoice.objects.none()
    return Invoice.objects.filter(user=user)


def get_listable_accounts(request):
    """Return all accounts for an authenticated user.
    Used for list views and dropdowns showing all accounts.
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return Invoice.objects.none()
    return Invoice.objects.all()
