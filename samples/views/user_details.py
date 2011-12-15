#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This file is part of Chantal, the samples database.
#
# Copyright (C) 2010 Forschungszentrum Jülich, Germany,
#                    Marvin Goblet <m.goblet@fz-juelich.de>,
#                    Torsten Bronger <t.bronger@fz-juelich.de>
#
# You must not use, install, pass on, offer, sell, analyse, modify, or
# distribute this software without explicit permission of the copyright holder.
# If you have received a copy of this software without the explicit permission
# of the copyright holder, you must destroy it immediately and completely.


u"""Views for showing and editing user data, i.e. real names, contact
information, and preferences.
"""

from __future__ import absolute_import

import re, json, copy
from django.template import RequestContext
from django.shortcuts import render_to_response, get_object_or_404
from django.contrib.auth.decorators import login_required
import django.contrib.auth.models
from django.conf import settings
from django import forms
from django.forms.util import ValidationError
import django.core.urlresolvers
from django.utils.translation import ugettext as _, ugettext_lazy
from django.utils.text import capfirst
from chantal_common.utils import get_really_full_name
from samples import models, permissions
from samples.views import utils, form_utils
from samples.permissions import get_all_addable_physical_process_models
from django.contrib.contenttypes.models import ContentType
from chantal_common import utils as chantal_common_utils
from chantal_common.models import Topic


@login_required
def show_user(request, login_name):
    u"""View for showing basic information about a user, like the email
    address.  Maybe this could be fleshed out with phone number, picture,
    position, and field of interest.

    :Parameters:
      - `request`: the current HTTP Request object
      - `login_name`: the login name of the user to be shown

    :type request: ``HttpRequest``
    :type login_name: str

    :Returns:
      the HTTP response object

    :rtype: ``HttpResponse``
    """
    user = get_object_or_404(django.contrib.auth.models.User,
                             username=login_name, chantal_user_details__is_administrative=False)
    userdetails = user.samples_user_details
    username = get_really_full_name(user)
    return render_to_response("samples/show_user.html", {"title": username, "shown_user": user, "userdetails": userdetails},
                              context_instance=RequestContext(request))


class UserDetailsForm(forms.ModelForm):
    u"""Model form for user preferences.
    """
    _ = ugettext_lazy
    subscribed_feeds = forms.MultipleChoiceField(label=capfirst(_(u"subscribed newsfeeds")), required=False)
    default_folded_process_classes = forms.MultipleChoiceField(label=capfirst(_(u"folded processes")), required=False)

    def __init__(self, user, *args, **kwargs):
        super(UserDetailsForm, self).__init__(*args, **kwargs)
        self.fields["auto_addition_topics"].queryset = user.topics
        self.fields["default_folded_process_classes"].choices = form_utils.choices_of_content_types(
            process_class for process_class in chantal_common_utils.get_all_models().itervalues()
            if issubclass(process_class, models.Process) and not process_class._meta.abstract
            and process_class not in [models.Process, models.Deposition])
        self.fields["default_folded_process_classes"].widget.attrs["size"] = "15"
        self.fields["subscribed_feeds"].choices = form_utils.choices_of_content_types(
            list(get_all_addable_physical_process_models()) + [models.Sample, models.SampleSeries, Topic])
        self.fields["subscribed_feeds"].widget.attrs["size"] = "15"

    class Meta:
        model = models.UserDetails
        fields = ("auto_addition_topics", "only_important_news", "subscribed_feeds",
                  "default_folded_process_classes")


@login_required
def edit_preferences(request, login_name):
    u"""View for editing preferences of a user.  Note that by giving the
    ``login_name`` explicitly, it is possible to edit the preferences of
    another user.  However, this is only allowed to staff.  The main reason for
    this explicitness is to be more “RESTful”.

    You can't switch the prefered language here because there are the flags
    anyway.

    Moreover, for good reason, you can't change your real name here.  This is
    taken automatically from the domain database through LDAP.  I want to give
    as few options as possible in order to avoid misbehaviour.

    :Parameters:
      - `request`: the current HTTP Request object
      - `login_name`: the login name of the user who's preferences should be
        edited.

    :type request: ``HttpRequest``
    :type login_name: unicode

    :Returns:
      the HTTP response object

    :rtype: ``HttpResponse``
    """
    def __change_folded_processes(default_folded_process_classes, user):
        u"""Creates the new exceptional processes dictionary and saves it into the user details.
        """
        old_default_classes = set(cls.id for cls in ContentType.objects.filter(dont_show_to_user=user.samples_user_details))
        new_default_classes = set(map(int, default_folded_process_classes))
        differences = old_default_classes ^ new_default_classes
        exceptional_processes_dict = json.loads(user.samples_user_details.folded_processes)
        for process_id_list in exceptional_processes_dict.itervalues():
            for process_id in copy.copy(process_id_list):
                try:
                    if models.Process.objects.get(pk=process_id).content_type.id in differences:
                        process_id_list.remove(process_id)
                except models.Process.DoesNotExist:
                    # FixMe: the missing process should be removed from the exceptional_processes_dict
                    pass
        user.samples_user_details.folded_processes = json.dumps(exceptional_processes_dict)
        user.samples_user_details.save()

    user = get_object_or_404(django.contrib.auth.models.User, username=login_name)
    if not request.user.is_staff and request.user != user:
        raise permissions.PermissionError(request.user, _(u"You can't access the preferences of another user."))
    initials_mandatory = utils.parse_query_string(request).get("initials_mandatory") == "True"
    user_details = user.samples_user_details
    if request.method == "POST":
        user_details_form = UserDetailsForm(user, request.POST, instance=user_details)
        initials_form = form_utils.InitialsForm(user, initials_mandatory, request.POST)
        if user_details_form.is_valid() and initials_form.is_valid():
            __change_folded_processes(user_details_form.cleaned_data["default_folded_process_classes"], user)
            user_details_form.save()
            initials_form.save()
            return utils.successful_response(request, _(u"The preferences were successfully updated."))
    else:
        user_details_form = UserDetailsForm(user, instance=user_details)
        initials_form = form_utils.InitialsForm(user, initials_mandatory)
    return render_to_response(
        "samples/edit_preferences.html",
        {"title": _(u"Change preferences for {user_name}").format(user_name=get_really_full_name(request.user)),
         "user_details": user_details_form, "initials": initials_form,
         "has_topics": user.topics.exists()},
        context_instance=RequestContext(request))


@login_required
def topics_and_permissions(request, login_name):
    user = get_object_or_404(django.contrib.auth.models.User, username=login_name)
    if not request.user.is_staff and request.user != user:
        raise permissions.PermissionError(
            request.user, _(u"You can't access the list of topics and permissions of another user."))
    return render_to_response(
        "samples/topics_and_permissions.html",
        {"title": _(u"Topics and permissions for {user_name}").format(user_name=get_really_full_name(request.user)),
         "topics": user.topics.all(), "permissions": permissions.get_user_permissions(user),
         "full_user_name": get_really_full_name(request.user),
         "permissions_url": django.core.urlresolvers.reverse("samples.views.permissions.list_")},
        context_instance=RequestContext(request))
