from django import forms


class RichTextWidget(forms.Textarea):
    """A textarea that the admin upgrades to a rich text editor (Quill, bundled in static/richtext/vendor).

    The stored value is HTML; it is cleaned by apps.common.richtext.clean_html when the form is saved."""

    def __init__(self, attrs=None):
        super().__init__({"data-richtext": "1", "rows": 8, **(attrs or {})})

    class Media:
        css = {"all": ("richtext/vendor/quill.snow.css", "richtext/richtext.css")}
        js = ("richtext/vendor/quill.js", "richtext/richtext.js")
