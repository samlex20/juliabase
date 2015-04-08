#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This file is part of JuliaBase, see http://www.juliabase.org.
# Copyright © 2008–2015 Forschungszentrum Jülich GmbH, Jülich, Germany
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import, unicode_literals
from django.utils.six.moves import urllib

import collections
from django.conf import settings
from django.apps import AppConfig
from django.utils.translation import ugettext_lazy as _, ugettext
from django.core.urlresolvers import reverse
import jb_common.utils.base as utils
from jb_common.nav_menu import MenuItem


class JBCommonConfig(AppConfig):
    name = "jb_common"
    verbose_name = _("JuliaBase – administration")

    def ready(self):
        import jb_common.signals

    def build_menu(self, menu, request):
        if request.user.is_authenticated():
            user_menu = menu.setdefault(utils.get_really_full_name(request.user), MenuItem(position="right"))
            user_menu.sub_items[_("Edit preferences")] = MenuItem(
                reverse("samples.views.user_details.edit_preferences", kwargs={"login_name": request.user.username}),
                "wrench")
            user_menu.sub_items[_("Logout")] = MenuItem(reverse("django.contrib.auth.views.logout"), "log-out")
        jb_menu = menu.setdefault("JuliaBase", MenuItem())
        if request.user.is_authenticated() and request.method == "GET":
            first = True
            for code, name in settings.LANGUAGES:
                back_url = request.path
                if request.GET:
                    back_url += "?" + request.GET.urlencode()
                jb_menu.sub_items[name] = MenuItem(
                    "{}?lang={}&amp;next={}".format(reverse("jb_common.views.switch_language"), code,
                                                    urllib.parse.quote_plus(back_url)),
                    icon_url=urllib.parse.urljoin(settings.STATIC_URL, "juliabase/flags/{}.png".format(code)),
                    icon_description=_("switch to {language}").format(language=name),
                    rule_before=first)
                first = False


_ = ugettext
