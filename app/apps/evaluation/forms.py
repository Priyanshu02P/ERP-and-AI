from django import forms

from apps.core.forms import BootstrapFormMixin

from .models import MANUAL_CRITERIA, Criterion, EvaluationEntry
from .scoring import GATES


class WeightsForm(BootstrapFormMixin, forms.Form):
    """Criterion weights in percent; must total 100."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for code, label in Criterion.choices:
            self.fields[f"w_{code}"] = forms.IntegerField(min_value=0, max_value=100, label=label)
            self.fields[f"w_{code}"].widget.attrs.update({"class": "form-control weight-input", "style": "max-width:90px"})

    def clean(self):
        cleaned = super().clean()
        if not self.errors:
            total = sum(cleaned[f"w_{c}"] for c in Criterion.values)
            if total != 100:
                raise forms.ValidationError(f"Weights must total 100% (currently {total}%).")
        return cleaned

    def weights(self):
        return {c: self.cleaned_data[f"w_{c}"] for c in Criterion.values}


class EntryForm(BootstrapFormMixin, forms.Form):
    """Gates, cost adjustment and manual scores for ONE bidder."""

    gate_specs = forms.NullBooleanField(widget=forms.NullBooleanSelect, required=False)
    gate_certificates = forms.NullBooleanField(widget=forms.NullBooleanSelect, required=False)
    gate_capacity = forms.NullBooleanField(widget=forms.NullBooleanSelect, required=False)
    is_disqualified = forms.BooleanField(required=False, label="Disqualify")
    disqualification_reason = forms.CharField(required=False, max_length=300)
    adjustment_amount = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2, initial=0)
    adjustment_note = forms.CharField(required=False, max_length=300)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for code in MANUAL_CRITERIA:
            self.fields[f"score_{code}"] = forms.DecimalField(required=False, min_value=0, max_value=100,
                                                              max_digits=5, decimal_places=2)
            self.fields[f"note_{code}"] = forms.CharField(required=False, max_length=300)
            self.fields[f"note_{code}"].widget.attrs["placeholder"] = "Evidence / rationale"
        self.fields["adjustment_note"].widget.attrs["placeholder"] = "Assumption (e.g. inspection, expected defects)"
        self.fields["disqualification_reason"].widget.attrs["placeholder"] = "Reason (required)"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_disqualified") and not cleaned.get("disqualification_reason", "").strip():
            self.add_error("disqualification_reason", "Give a reason for the disqualification.")
        if (cleaned.get("adjustment_amount") or 0) > 0 and not cleaned.get("adjustment_note", "").strip():
            self.add_error("adjustment_note", "Document the assumption behind the adjustment.")
        return cleaned

    def service_data(self):
        c = self.cleaned_data
        data = {k: c.get(k) for k in ("gate_specs", "gate_certificates", "gate_capacity", "is_disqualified",
                                      "adjustment_amount")}
        data["disqualification_reason"] = c.get("disqualification_reason", "").strip()
        data["adjustment_note"] = c.get("adjustment_note", "").strip()
        data["scores"] = {code: (c.get(f"score_{code}"), c.get(f"note_{code}", "").strip()) for code in MANUAL_CRITERIA}
        return data

    @staticmethod
    def initial_for(entry: EvaluationEntry) -> dict:
        init = {k: getattr(entry, k) for k in ("gate_specs", "gate_certificates", "gate_capacity", "is_disqualified",
                                               "disqualification_reason", "adjustment_amount", "adjustment_note")}
        for s in entry.scores.all():
            init[f"score_{s.criterion}"], init[f"note_{s.criterion}"] = s.score, s.note
        return init


class MetaForm(BootstrapFormMixin, forms.Form):
    recommended = forms.ModelChoiceField(queryset=EvaluationEntry.objects.none(), required=False,
                                         label="Recommended bidder", empty_label="Top-ranked eligible bidder (default)")
    rationale = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}),
                                label="Recommendation rationale",
                                help_text="Mandatory to submit. If you do not recommend the top-ranked bidder, explain why.")

    def __init__(self, *args, evaluation, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recommended"].queryset = evaluation.entries.select_related("quotation__vendor")
        self.fields["recommended"].label_from_instance = lambda e: f"{e.quotation.vendor.name} ({e.quotation.quote_number})"


class ReviewForm(BootstrapFormMixin, forms.Form):
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                              help_text="Mandatory when returning the evaluation.")
