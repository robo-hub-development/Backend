from django.core.validators import RegexValidator

# Digits with an optional leading '+', 10-15 long. Not tied to one country so it can widen later.
validate_mobile = RegexValidator(r"^\+?\d{10,15}$", "Enter a valid mobile number.")
