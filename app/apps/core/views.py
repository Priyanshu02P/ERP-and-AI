from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def home(request):
    """Landing page: approvers go to their inbox, everyone else to the PR list."""
    if request.user.can_approve:
        return redirect("requisitions:inbox")
    return redirect("requisitions:pr_list")
