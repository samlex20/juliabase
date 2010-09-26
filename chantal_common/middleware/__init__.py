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


from __future__ import absolute_import

import locale, re, json
from django.contrib.messages.storage import default_storage
from django.utils.cache import patch_vary_headers, add_never_cache_headers
from django.utils import translation
from django.template import loader, RequestContext
from django.contrib.auth.models import SiteProfileNotAvailable
from chantal_common.models import UserDetails
from chantal_common.utils import is_json_requested
from django.conf import settings
from django.utils.translation import ugettext as _
import django.http
from django.shortcuts import render_to_response


u"""Middleware for setting the current language to what can be found in
`models.UserDetails` and for cache-disabling in presence of temporary messages.
Additionally, I convert responses into JSON format if this was requested.
"""


class LocaleMiddleware(object):
    u"""This is a simple middleware that parses a request and decides what
    translation object to install in the current thread context depending on
    what's found in `models.UserDetails`. This allows pages to be dynamically
    translated to the language the user desires (if the language is available,
    of course).

    It must be after ``AuthenticationMiddleware`` in the list.
    """
    language_pattern = re.compile("[a-zA-Z0-9]+")

    @staticmethod
    def get_language_for_user(request):
        if request.user.is_authenticated():
            try:
                language = request.user.chantal_user_details.language
                return language
            except (SiteProfileNotAvailable, UserDetails.DoesNotExist):
                pass
        return translation.get_language_from_request(request)

    def get_language_code_only(self, language):
        match = self.language_pattern.match(language)
        return match.group(0) if match else "en"

    def process_request(self, request):
        language = self.get_language_for_user(request)
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()
        # Now for the locale, but only if necessary because it seems to be a
        # costly operation.
        new_locale = settings.LOCALES_DICT.get(self.get_language_code_only(language))
        old_locale = locale.getlocale()[0]
        if not old_locale or not new_locale.startswith(old_locale):
            locale.setlocale(locale.LC_ALL, new_locale or "C")

    def process_response(self, request, response):
        patch_vary_headers(response, ("Accept-Language",))
        response["Content-Language"] = translation.get_language()
        translation.deactivate()
        return response


class MessageMiddleware(object):
    u"""Middleware that handles temporary messages.  It is a copy of Django's
    original ``MessageMiddleware`` but it adds cache disabling.  This way,
    pages with messages are never cached by the browser, so that the messages
    don't get persistent.
    """
    def process_request(self, request):
        request._messages = default_storage(request)

    def process_response(self, request, response):
        """
        Updates the storage backend (i.e., saves the messages).

        If not all messages could not be stored and ``DEBUG`` is ``True``, a
        ``ValueError`` is raised.
        """
        # A higher middleware layer may return a request which does not contain
        # messages storage, so make no assumption that it will be there.
        if hasattr(request, '_messages'):
            unstored_messages = request._messages.update(response)
            if unstored_messages and settings.DEBUG:
                raise ValueError('Not all temporary messages could be stored.')
            if request._messages.used:
                del response["ETag"]
                del response["Last-Modified"]
                response["Expires"] = "Fri, 01 Jan 1990 00:00:00 GMT"
                # FixMe: One should check whether the following settings are
                # sensible.
                response["Pragma"] = "no-cache"
                response["Cache-Control"] = "no-cache, no-store, max-age=0, must-revalidate, private"

        return response


class JSONClientMiddleware(object):
    u"""Middleware to convert responses to JSON if this was requested by the
    client.

    It is important that this class comes after non-Chantal middleware in
    ``MIDDLEWARE_CLASSES`` in the ``settings`` module, otherwise the above
    mentioned exceptions may propagate to other middleware which treats them as
    real errors.  It is recommended that it is even the very last entry.
    """

    def process_exception(self, request, exception):
        u"""Convert a 404 to a JSONised 404.  This is, the body contains JSON,
        and the content type is JSON.
        """
        if isinstance(exception, django.http.Http404):
            if is_json_requested(request):
                return django.http.HttpResponseNotFound(json.dumps((2, exception.args[0])),
                                                        content_type="application/json; charset=ascii")
