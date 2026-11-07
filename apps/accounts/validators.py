from django.core.validators import RegexValidator

# Digits with an optional leading '+', 10-15 long. Not tied to one country so it can widen later.
validate_mobile = RegexValidator(r"^\+?\d{10,15}$", "Enter a valid mobile number.")

validate_pincode = RegexValidator(r"^\d{6}$", "Enter a valid 6-digit pincode.")
